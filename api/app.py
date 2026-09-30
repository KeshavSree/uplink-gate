"""The gate service: REST and WebSocket access to the gate library.

Every decision is made by `gate.engine.evaluate` and every transmission goes through
`gate.uplink.UplinkAdapter`. This module only wires them to HTTP.
"""

from __future__ import annotations

import asyncio
import hmac
import itertools
import logging
import multiprocessing
import os
import re
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from api.events import EventBus
from api.workers import compile_worker
from gate.compile import CompileError, record_compile
from gate.config import GateConfig
from gate.dictionary import DictionaryError
from gate.engine import evaluate
from gate.registry import Compile, Registry, Vehicle, utcnow
from gate.resolve import resolve_running_dictionary
from gate.rules import RuleError, load_rules_file
from gate.telemetry import IngestManager, VehicleComms
from gate.testbed import TestbedRecorder
from gate.uplink import UplinkAdapter, UplinkRefused

log = logging.getLogger(__name__)


@dataclass
class Settings:
    """Service settings that are not part of the gate's own configuration."""

    #: vehicle ID -> "host:port" of that vehicle's GDS.
    vehicles: dict[str, str] = field(default_factory=dict)
    #: Directory searched for *TopologyDictionary.json files to register at startup.
    seed_dictionaries: Path | None = None
    #: Directory of example .fpy sequences offered by the API.
    sequences_dir: Path = Path("sequences")
    #: Built web UI to serve at /, if present.
    web_dir: Path | None = None
    #: Demo pacing: seconds each compile job sleeps before compiling. 0 turns it off.
    compile_pacing_s: float = 0.0
    compile_workers: int = 4
    influx_url: str = ""
    influx_token: str = ""
    influx_org: str = "gate"
    influx_bucket: str = "telemetry"
    #: YAML flight rules file loaded into the registry at startup, if set.
    rules_file: Path | None = None
    start_ingest: bool = True
    #: If set, every POST to the API needs this value in the X-Gate-Token header.
    #: Reading stays open. Meant for a demo exposed on the internet.
    write_token: str = ""

    @classmethod
    def from_env(cls) -> Settings:
        vehicles: dict[str, str] = {}
        for item in os.environ.get("GATE_VEHICLES", "").split(","):
            if "=" in item:
                vehicle_id, endpoint = item.split("=", 1)
                vehicles[vehicle_id.strip()] = endpoint.strip()
        seed = os.environ.get("GATE_SEED_DICTIONARIES")
        web = os.environ.get("GATE_WEB_DIR", "web/dist")
        rules = os.environ.get("GATE_RULES_FILE")
        return cls(
            vehicles=vehicles,
            seed_dictionaries=Path(seed) if seed else None,
            sequences_dir=Path(os.environ.get("GATE_SEQUENCES_DIR", "sequences")),
            web_dir=Path(web) if web else None,
            compile_pacing_s=float(os.environ.get("GATE_COMPILE_PACING_S", "0")),
            compile_workers=int(os.environ.get("GATE_COMPILE_WORKERS", "4")),
            influx_url=os.environ.get("INFLUX_URL", ""),
            influx_token=os.environ.get("INFLUX_TOKEN", ""),
            influx_org=os.environ.get("INFLUX_ORG", "gate"),
            influx_bucket=os.environ.get("INFLUX_BUCKET", "telemetry"),
            rules_file=Path(rules) if rules else None,
            write_token=os.environ.get("GATE_WRITE_TOKEN", ""),
        )


@dataclass
class CompileJob:
    """One compile request. Runs in the process pool; progress is published on the event bus."""

    id: int
    sequence_name: str
    vehicle_id: str | None
    dictionary_hash: str
    status: str = "queued"  # queued -> running -> done | failed
    compile: dict[str, Any] | None = None
    error: str | None = None
    log: str = ""
    worker_pid: int | None = None
    duration_ms: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence_name": self.sequence_name,
            "vehicle_id": self.vehicle_id,
            "dictionary_hash": self.dictionary_hash,
            "status": self.status,
            "compile": self.compile,
            "error": self.error,
            "log": self.log,
            "worker_pid": self.worker_pid,
            "duration_ms": self.duration_ms,
        }


class CompileRequest(BaseModel):
    #: Compile with the dictionary this vehicle is running.
    vehicle_id: str | None = None
    #: Or: a dictionary hash (or unique prefix) to compile with.
    dictionary: str | None = None
    #: Name of a sequence in the server's sequence library, for example "signal_setup".
    sequence_name: str | None = None
    #: Or: Fpy source text. `sequence_name` is then the name to store it under.
    source: str | None = None


class CompileAllRequest(BaseModel):
    sequence_name: str | None = None
    source: str | None = None


class CheckRequest(BaseModel):
    compile_id: int
    vehicle_id: str
    checks: list[str] | None = None


class UplinkRequest(BaseModel):
    compile_id: int
    vehicle_id: str
    decision_id: int | None = None
    run: bool = True


class GateService:
    """The objects behind the API, kept together so tests can build one without a server."""

    def __init__(
        self,
        config: GateConfig | None = None,
        settings: Settings | None = None,
        comms: VehicleComms | None = None,
    ) -> None:
        self.config = config or GateConfig.from_env()
        self.settings = settings or Settings.from_env()
        self.registry = Registry(self.config)
        self.bus = EventBus()
        self.recorder = TestbedRecorder(self.registry, self.config, publish=self.bus.publish)
        self.manager: IngestManager | None = None
        self.comms = comms
        self.jobs: dict[int, CompileJob] = {}
        self._job_ids = itertools.count(1)
        self.pool: ProcessPoolExecutor | None = None
        self._sink: Any = None
        self._tasks: set[asyncio.Task[Any]] = set()

    # --- startup and shutdown -------------------------------------------------------------

    def start(self) -> None:
        self._seed()
        if self.settings.start_ingest and self.comms is None:
            if self.settings.influx_url:
                try:
                    from gate.influx import InfluxSink

                    self._sink = InfluxSink(
                        self.settings.influx_url,
                        self.settings.influx_token,
                        self.settings.influx_org,
                        self.settings.influx_bucket,
                    )
                except Exception as error:
                    log.warning("InfluxDB sink disabled: %s", error)
            self.manager = IngestManager(
                self.registry,
                self.config,
                publish=self.bus.publish,
                sink=self._sink,
                on_event=self.recorder.on_event,
            )
            self.manager.start()
            self.comms = self.manager
        self.pool = ProcessPoolExecutor(
            max_workers=self.settings.compile_workers,
            mp_context=multiprocessing.get_context("spawn"),
        )

    def stop(self) -> None:
        if self.manager is not None:
            self.manager.stop()
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)
        if self._sink is not None:
            self._sink.close()

    def _seed(self) -> None:
        seed = self.settings.seed_dictionaries
        if seed is not None and seed.is_dir():
            for path in sorted(seed.rglob("*TopologyDictionary.json")):
                try:
                    row = self.registry.add_dictionary(path)
                    log.info("registered dictionary %s (%s)", row.hash[:12], row.project_version)
                except DictionaryError as error:
                    log.warning("skipping %s: %s", path, error)
        for vehicle_id, endpoint in self.settings.vehicles.items():
            self.registry.upsert_vehicle(vehicle_id, gds_endpoint=endpoint)
        rules_file = self.settings.rules_file
        if rules_file is not None and rules_file.is_file():
            try:
                count = self.registry.load_flight_rules(load_rules_file(rules_file))
                log.info("loaded %d flight rule(s) from %s", count, rules_file)
            except RuleError as error:
                log.warning("flight rules not loaded: %s", error)

    # --- views ----------------------------------------------------------------------------

    def vehicle_summary(self, vehicle: Vehicle) -> dict[str, Any]:
        now = utcnow()
        state = self.registry.get_vehicle_state(vehicle.id)
        resolution = resolve_running_dictionary(self.registry, vehicle.id, now, self.config)
        running = (
            self.registry.get_dictionary(resolution.dictionary_hash)
            if resolution.dictionary_hash
            else None
        )
        latest = self.registry.list_decisions(vehicle.id, limit=1)
        return {
            **vehicle.to_dict(),
            "state": state.to_dict() if state else None,
            "reading_age_s": (now - state.observed_at).total_seconds() if state else None,
            "max_state_age_s": self.config.max_state_age_s,
            "resolution": {
                "ok": resolution.ok,
                "reason": resolution.reason.value,
                "message": resolution.message,
                "identified_by": resolution.identified_by,
            },
            "running_dictionary": running.to_dict() if running else None,
            "link": self.manager.status(vehicle.id) if self.manager else None,
            "last_decision": latest[0].to_dict() if latest else None,
        }

    def sequences(self) -> list[dict[str, str]]:
        directory = self.settings.sequences_dir
        if not directory.is_dir():
            return []
        return [
            {"name": path.stem, "source": path.read_text(encoding="utf-8")}
            for path in sorted(directory.glob("*.fpy"))
        ]

    # --- compile --------------------------------------------------------------------------

    def _sequence_source(self, name: str | None, source: str | None) -> tuple[str, str]:
        if source is not None:
            return _safe_name(name or "sequence"), source
        if name is None:
            raise HTTPException(422, "give sequence_name or source")
        path = self.settings.sequences_dir / f"{_safe_name(name)}.fpy"
        if not path.is_file():
            raise HTTPException(404, f"no sequence named '{name}'")
        return path.stem, path.read_text(encoding="utf-8")

    def _dictionary_for(self, vehicle_id: str | None, dictionary: str | None) -> str:
        if dictionary is not None:
            found = self.registry.find_dictionary(dictionary)
            if found is None:
                raise HTTPException(404, f"'{dictionary}' is not a unique dictionary hash prefix")
            return found.hash
        if vehicle_id is None:
            raise HTTPException(422, "give vehicle_id or dictionary")
        if self.registry.get_vehicle(vehicle_id) is None:
            raise HTTPException(404, f"no vehicle '{vehicle_id}'")
        resolution = resolve_running_dictionary(self.registry, vehicle_id, utcnow(), self.config)
        if resolution.dictionary_hash is None:
            raise HTTPException(
                409,
                detail={
                    "reason": resolution.reason.value,
                    "message": f"cannot compile for {vehicle_id}: {resolution.message}",
                },
            )
        return resolution.dictionary_hash

    def submit_compile(
        self, vehicle_id: str | None, dictionary: str | None, name: str | None, source: str | None
    ) -> tuple[CompileJob, asyncio.Task[None]]:
        sequence_name, text = self._sequence_source(name, source)
        dictionary_hash = self._dictionary_for(vehicle_id, dictionary)
        job = CompileJob(
            id=next(self._job_ids),
            sequence_name=sequence_name,
            vehicle_id=vehicle_id,
            dictionary_hash=dictionary_hash,
        )
        self.jobs[job.id] = job
        # Job numbers restart with the service, so the directory name must not rely on them:
        # reusing one would overwrite bytecode that an older compile record points to.
        work_dir = self.config.bytecode_store / f"job-{uuid.uuid4().hex[:16]}"
        work_dir.mkdir(parents=True, exist_ok=True)
        sequence_path = work_dir / f"{sequence_name}.fpy"
        sequence_path.write_text(text, encoding="utf-8")
        self.bus.publish("compile.queued", {"job": job.to_dict()})
        task = asyncio.create_task(self._run_compile(job, sequence_path))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job, task

    async def _run_compile(self, job: CompileJob, sequence_path: Path) -> None:
        assert self.pool is not None
        loop = asyncio.get_running_loop()
        dictionary = self.registry.get_dictionary(job.dictionary_hash)
        assert dictionary is not None
        job.status = "running"
        self.bus.publish("compile.started", {"job": job.to_dict()})
        try:
            result = await loop.run_in_executor(
                self.pool,
                compile_worker,
                str(sequence_path),
                dictionary.path,
                str(sequence_path.with_suffix(".bin")),
                self.settings.compile_pacing_s,
            )
            job.worker_pid = result["pid"]
            job.duration_ms = result["duration_ms"]
            if "error" in result:
                raise CompileError(result["error"], result.get("log", ""))
            job.log = result["output"].log
            record: Compile = await asyncio.to_thread(
                record_compile, self.registry, result["output"], job.dictionary_hash, job.vehicle_id
            )
            job.compile = record.to_dict()
            job.status = "done"
            self.bus.publish("compile.finished", {"job": job.to_dict()})
        except CompileError as error:
            job.status = "failed"
            job.error = str(error)
            job.log = error.output
            self.bus.publish("compile.failed", {"job": job.to_dict()})
        except Exception as error:  # a crashed worker must not leave the job hanging
            log.exception("compile job %s crashed", job.id)
            job.status = "failed"
            job.error = f"internal error: {error!r}"
            self.bus.publish("compile.failed", {"job": job.to_dict()})

    # --- check and uplink -----------------------------------------------------------------

    def _compile_record(self, compile_id: int) -> Compile:
        record = self.registry.get_compile(compile_id)
        if record is None:
            raise HTTPException(404, f"no compile record {compile_id}")
        if not Path(record.bytecode_path).is_file():
            raise HTTPException(410, f"bytecode file of compile {compile_id} is gone")
        return record

    def check(self, request: CheckRequest) -> dict[str, Any]:
        record = self._compile_record(request.compile_id)
        try:
            decision = evaluate(
                self.registry,
                record.bytecode_path,
                request.vehicle_id,
                config=self.config,
                checks=request.checks,
                telemetry=self.comms.latest_channels(request.vehicle_id) if self.comms else None,
            )
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        payload = decision.to_dict() | {"requested_compile_id": record.id}
        self.bus.publish("gate.decision", {"decision": payload})
        return payload

    def uplink(self, request: UplinkRequest) -> JSONResponse:
        record = self._compile_record(request.compile_id)
        if self.comms is None:
            raise HTTPException(503, "no vehicle links: telemetry ingest is not running")
        adapter = UplinkAdapter(self.registry, self.comms, self.config)
        try:
            result = adapter.uplink(
                record.bytecode_path,
                request.vehicle_id,
                decision_id=request.decision_id,
                run=request.run,
            )
        except UplinkRefused as refused:
            body = {
                "refused": True,
                "vehicle_id": request.vehicle_id,
                "compile_id": record.id,
                "reason": refused.reason.value,
                "message": refused.message,
                "decision": refused.decision.to_dict() if refused.decision else None,
            }
            if refused.decision is not None:
                self.bus.publish("gate.decision", {"decision": refused.decision.to_dict()})
            self.bus.publish("uplink.refused", body)
            return JSONResponse(body, status_code=409)
        body = result.to_dict() | {"refused": False, "compile_id": record.id}
        recheck = self.registry.get_decision(result.decision_id)
        if recheck is not None:
            self.bus.publish("gate.decision", {"decision": recheck.to_dict()})
        self.bus.publish("uplink.finished", body)
        return JSONResponse(body, status_code=200)


_ROUTINE_EVENTS = ("CdhCore.cmdDisp.OpCodeDispatched", "CdhCore.cmdDisp.OpCodeCompleted")


def _is_routine(event_name: str) -> bool:
    return event_name in _ROUTINE_EVENTS or event_name.startswith("CdhCore.version.")


def _safe_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_-]", "_", Path(name).stem)
    return cleaned or "sequence"


def create_app(
    config: GateConfig | None = None,
    settings: Settings | None = None,
    comms: VehicleComms | None = None,
) -> FastAPI:
    service = GateService(config, settings, comms)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        service.bus.bind(asyncio.get_running_loop())
        await asyncio.to_thread(service.start)
        try:
            yield
        finally:
            await asyncio.to_thread(service.stop)

    app = FastAPI(title="Uplink Gate", version="0.1.0", lifespan=lifespan)

    @app.middleware("http")
    async def require_token_for_actions(request: Request, call_next: Any) -> Any:
        token = service.settings.write_token
        if (
            token
            and request.method == "POST"
            and request.url.path.startswith("/api/")
            and not hmac.compare_digest(request.headers.get("x-gate-token", ""), token)
        ):
            return JSONResponse({"detail": "a password is needed to do this"}, status_code=401)
        return await call_next(request)

    app.state.service = service
    registry = service.registry

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {"ok": True, "vehicles": len(registry.list_vehicles())}

    @app.get("/api/vehicles")
    def vehicles() -> list[dict[str, Any]]:
        return [service.vehicle_summary(v) for v in registry.list_vehicles()]

    @app.get("/api/vehicles/{vehicle_id}")
    def vehicle(vehicle_id: str) -> dict[str, Any]:
        row = registry.get_vehicle(vehicle_id)
        if row is None:
            raise HTTPException(404, f"no vehicle '{vehicle_id}'")
        events = service.manager.recent_events(vehicle_id, 400) if service.manager else []
        # Leave out the chatter of the periodic version request, so real activity is visible.
        events = [e for e in events if not _is_routine(e.name)][-50:]
        return service.vehicle_summary(row) | {
            "compiles": [c.to_dict() for c in registry.list_compiles(vehicle_id, limit=20)],
            "decisions": [d.to_dict() for d in registry.list_decisions(vehicle_id, limit=20)],
            "uplinks": [u.to_dict() for u in registry.list_uplinks(vehicle_id, limit=20)],
            "events": [e.to_dict() for e in events],
        }

    @app.get("/api/dictionaries")
    def dictionaries() -> list[dict[str, Any]]:
        return [d.to_dict() for d in registry.list_dictionaries()]

    @app.get("/api/sequences")
    def sequences() -> list[dict[str, str]]:
        return service.sequences()

    @app.post("/api/compile", status_code=202)
    async def compile_(request: CompileRequest, wait: bool = False) -> dict[str, Any]:
        """Start a compile job. With `?wait=true` the response is sent when the job is over."""
        job, task = service.submit_compile(
            request.vehicle_id, request.dictionary, request.sequence_name, request.source
        )
        if wait:
            await task
        return job.to_dict()

    @app.post("/api/compile/all", status_code=202)
    async def compile_all(request: CompileAllRequest, wait: bool = False) -> list[dict[str, Any]]:
        """Start one compile job per vehicle, each with the dictionary that vehicle is running.

        The jobs run at the same time in the process pool. A vehicle whose running dictionary
        cannot be resolved gets a failed job instead of stopping the others.
        """
        jobs: list[CompileJob] = []
        tasks: list[asyncio.Task[None]] = []
        for row in registry.list_vehicles():
            try:
                job, task = service.submit_compile(
                    row.id, None, request.sequence_name, request.source
                )
                tasks.append(task)
            except HTTPException as error:
                if error.status_code != 409:
                    raise
                detail: dict[str, Any] = error.detail if isinstance(error.detail, dict) else {}
                job = CompileJob(
                    id=next(service._job_ids),
                    sequence_name=request.sequence_name or "sequence",
                    vehicle_id=row.id,
                    dictionary_hash="",
                    status="failed",
                    error=str(detail.get("message", error.detail)),
                )
                service.jobs[job.id] = job
                service.bus.publish("compile.failed", {"job": job.to_dict()})
            jobs.append(job)
        if wait and tasks:
            await asyncio.gather(*tasks)
        return [job.to_dict() for job in jobs]

    @app.get("/api/compile/jobs/{job_id}")
    def compile_job(job_id: int) -> dict[str, Any]:
        job = service.jobs.get(job_id)
        if job is None:
            raise HTTPException(404, f"no compile job {job_id}")
        return job.to_dict()

    @app.get("/api/compiles")
    def compiles(vehicle_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [c.to_dict() for c in registry.list_compiles(vehicle_id, limit=limit)]

    @app.post("/api/check")
    def check(request: CheckRequest) -> dict[str, Any]:
        """Run the gate for a compiled sequence and a vehicle. Returns the decision."""
        return service.check(request)

    @app.get("/api/decisions")
    def decisions(vehicle_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [d.to_dict() for d in registry.list_decisions(vehicle_id, limit=limit)]

    @app.post("/api/uplink")
    def uplink(request: UplinkRequest) -> JSONResponse:
        """Send a compiled sequence to a vehicle and run it. 409 if the gate refuses."""
        return service.uplink(request)

    @app.get("/api/uplinks")
    def uplinks(vehicle_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return [u.to_dict() for u in registry.list_uplinks(vehicle_id, limit=limit)]

    @app.get("/api/rules")
    def rules() -> list[dict[str, Any]]:
        return [r.to_dict() for r in registry.list_flight_rules()]

    @app.get("/api/testbed-runs")
    def testbed_runs(limit: int = 50) -> list[dict[str, Any]]:
        return [r.to_dict() for r in registry.list_testbed_runs(limit)]

    @app.get("/api/config")
    def config_() -> dict[str, Any]:
        return {
            "checks": list(service.config.checks),
            "max_state_age_s": service.config.max_state_age_s,
            "max_decision_age_s": service.config.max_decision_age_s,
            "allow_dirty": service.config.allow_dirty,
            "compile_pacing_s": service.settings.compile_pacing_s,
        }

    @app.get("/api/events/recent")
    def recent_events(limit: int = 100) -> list[dict[str, Any]]:
        return service.bus.recent(limit)

    @app.websocket("/api/events")
    async def events(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = service.bus.subscribe()
        try:
            while True:
                await websocket.send_json(await queue.get())
        except WebSocketDisconnect:
            pass
        finally:
            service.bus.unsubscribe(queue)
            with suppress(Exception):
                await websocket.close()

    web_dir = service.settings.web_dir
    if web_dir is not None and web_dir.is_dir():
        app.mount("/", StaticFiles(directory=web_dir, html=True), name="web")

    return app
