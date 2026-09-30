"""The registry: the database of dictionaries, vehicles, compiles and gate decisions.

PostgreSQL in the gate service, SQLite for the CLI and for tests. The models are plain
SQLAlchemy and use nothing specific to either database.
"""

from __future__ import annotations

import shutil
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.types import TypeDecorator

from gate.config import GateConfig
from gate.dictionary import DictionaryInfo, load_dictionary
from gate.results import CheckResult, GateDecision, Result

VEHICLE_KINDS = ("testbed", "spacecraft")


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """A timestamp that is always timezone-aware UTC, including on SQLite, which drops zones."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime given to the registry; use timezone-aware UTC")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    pass


class Dictionary(Base):
    """One dictionary file, identified by the SHA-256 of its bytes."""

    __tablename__ = "dictionary"

    hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    deployment_name: Mapped[str] = mapped_column(String(200))
    project_version: Mapped[str] = mapped_column(String(200), index=True)
    framework_version: Mapped[str] = mapped_column(String(200), default="")
    spec_version: Mapped[str] = mapped_column(String(50), default="")
    path: Mapped[str] = mapped_column(Text)
    loaded_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hash": self.hash,
            "deployment_name": self.deployment_name,
            "project_version": self.project_version,
            "framework_version": self.framework_version,
            "spec_version": self.spec_version,
            "path": self.path,
            "loaded_at": self.loaded_at.isoformat(),
        }


class Vehicle(Base):
    """Anything that runs a build and sends telemetry: a testbed or a spacecraft."""

    __tablename__ = "vehicle"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20), default="spacecraft")
    gds_endpoint: Mapped[str | None] = mapped_column(String(200), nullable=True)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "kind": self.kind,
            "gds_endpoint": self.gds_endpoint,
        }


class VehicleState(Base):
    """The latest build identity reading for a vehicle. One row per vehicle."""

    __tablename__ = "vehicle_state"

    vehicle_id: Mapped[str] = mapped_column(ForeignKey("vehicle.id"), primary_key=True)
    observed_project_version: Mapped[str] = mapped_column(String(200))
    #: Content identity reported by the build (`dict-sha256:<hex prefix>`), if it reports one.
    observed_dictionary_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(UTCDateTime)
    resolved_dictionary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: Where the reading came from: "manual" or "telemetry".
    source: Mapped[str] = mapped_column(String(20), default="manual")

    def to_dict(self) -> dict[str, Any]:
        return {
            "vehicle_id": self.vehicle_id,
            "observed_project_version": self.observed_project_version,
            "observed_dictionary_id": self.observed_dictionary_id,
            "observed_at": self.observed_at.isoformat(),
            "resolved_dictionary_hash": self.resolved_dictionary_hash,
            "source": self.source,
        }


class Compile(Base):
    """The gate's record that bytecode X was compiled from sequence Y with dictionary Z."""

    __tablename__ = "compile"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sequence_sha256: Mapped[str] = mapped_column(String(64))
    sequence_path: Mapped[str] = mapped_column(Text)
    dictionary_hash: Mapped[str] = mapped_column(ForeignKey("dictionary.hash"))
    compiler_version: Mapped[str] = mapped_column(String(50))
    bytecode_sha256: Mapped[str] = mapped_column(String(64), index=True)
    bytecode_path: Mapped[str] = mapped_column(Text)
    #: The vehicle the compile was requested for, if any. Informational only.
    vehicle_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "sequence_sha256": self.sequence_sha256,
            "sequence_path": self.sequence_path,
            "dictionary_hash": self.dictionary_hash,
            "compiler_version": self.compiler_version,
            "bytecode_sha256": self.bytecode_sha256,
            "bytecode_path": self.bytecode_path,
            "vehicle_id": self.vehicle_id,
            "created_at": self.created_at.isoformat(),
        }


class GateDecisionRow(Base):
    """A stored gate decision."""

    __tablename__ = "gate_decision"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    compile_id: Mapped[int | None] = mapped_column(ForeignKey("compile.id"), nullable=True)
    vehicle_id: Mapped[str] = mapped_column(String(64), index=True)
    bytecode_sha256: Mapped[str] = mapped_column(String(64), index=True)
    running_dictionary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[str] = mapped_column(String(10))
    checks: Mapped[list[dict[str, Any]]] = mapped_column(JSON)
    decided_at: Mapped[datetime] = mapped_column(UTCDateTime)

    def to_decision(self) -> GateDecision:
        return GateDecision(
            id=self.id,
            result=Result(self.result),
            vehicle_id=self.vehicle_id,
            bytecode_sha256=self.bytecode_sha256,
            checks=tuple(CheckResult.from_dict(c) for c in self.checks),
            decided_at=self.decided_at,
            running_dictionary_hash=self.running_dictionary_hash,
            compile_id=self.compile_id,
        )


class Uplink(Base):
    """One attempt to send bytecode to a vehicle. Only the uplink adapter writes these."""

    __tablename__ = "uplink"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    decision_id: Mapped[int] = mapped_column(ForeignKey("gate_decision.id"))
    vehicle_id: Mapped[str] = mapped_column(String(64), index=True)
    sent_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ok: Mapped[bool] = mapped_column(Boolean)
    detail: Mapped[str] = mapped_column(Text, default="")

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "decision_id": self.decision_id,
            "vehicle_id": self.vehicle_id,
            "sent_at": self.sent_at.isoformat(),
            "ok": self.ok,
            "detail": self.detail,
        }


class TestbedRun(Base):
    """One run of a bytecode file on a vehicle, recorded from the sequencer's own events."""

    __tablename__ = "testbed_run"
    __test__ = False  # not a pytest class

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bytecode_sha256: Mapped[str] = mapped_column(String(64), index=True)
    dictionary_hash: Mapped[str] = mapped_column(String(64))
    vehicle_id: Mapped[str] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(10))
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "bytecode_sha256": self.bytecode_sha256,
            "dictionary_hash": self.dictionary_hash,
            "vehicle_id": self.vehicle_id,
            "result": self.result,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "evidence": self.evidence,
        }


class FlightRule(Base):
    """One flight rule. `dictionary_hash` NULL means the rule applies to every dictionary."""

    __tablename__ = "flight_rule"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    dictionary_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rule_json: Mapped[dict[str, Any]] = mapped_column(JSON)

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "dictionary_hash": self.dictionary_hash, "rule": self.rule_json}


class UnknownVehicleError(LookupError):
    """No vehicle with that ID is in the registry."""


class Registry:
    """Opens the database and offers the queries the rest of the gate needs."""

    def __init__(self, config: GateConfig | None = None, engine: Engine | None = None) -> None:
        self.config = config or GateConfig.from_env()
        if engine is None:
            url = self.config.resolved_database_url
            if url.startswith("sqlite:///"):
                Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
            engine = create_engine(url, future=True)
        self.engine = engine
        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions() as session, session.begin():
            yield session

    # --- dictionaries -------------------------------------------------------------------

    def add_dictionary(self, path: Path | str, copy_into_store: bool = True) -> Dictionary:
        """Hash a dictionary file and store its metadata. Adding the same file twice is a no-op."""
        info = load_dictionary(path)
        return self.add_dictionary_info(info, copy_into_store=copy_into_store)

    def add_dictionary_info(self, info: DictionaryInfo, copy_into_store: bool = True) -> Dictionary:
        stored_path = info.path.resolve()
        if copy_into_store:
            store = self.config.dictionary_store
            store.mkdir(parents=True, exist_ok=True)
            stored_path = (store / f"{info.hash}.json").resolve()
            if not stored_path.exists():
                shutil.copyfile(info.path, stored_path)
        with self.session() as session:
            row = session.get(Dictionary, info.hash)
            if row is None:
                row = Dictionary(
                    hash=info.hash,
                    deployment_name=info.deployment_name,
                    project_version=info.project_version,
                    framework_version=info.framework_version,
                    spec_version=info.spec_version,
                    path=str(stored_path),
                    loaded_at=utcnow(),
                )
                session.add(row)
            return row

    def get_dictionary(self, dictionary_hash: str) -> Dictionary | None:
        with self.session() as session:
            return session.get(Dictionary, dictionary_hash)

    def find_dictionary(self, reference: str) -> Dictionary | None:
        """Find a dictionary by full hash or by a unique hash prefix."""
        matches = self.dictionaries_by_hash_prefix(reference.lower())
        return matches[0] if len(matches) == 1 else None

    def list_dictionaries(self) -> list[Dictionary]:
        with self.session() as session:
            return list(session.scalars(select(Dictionary).order_by(Dictionary.loaded_at)))

    def dictionaries_by_project_version(self, project_version: str) -> list[Dictionary]:
        with self.session() as session:
            statement = select(Dictionary).where(Dictionary.project_version == project_version)
            return list(session.scalars(statement.order_by(Dictionary.hash)))

    def dictionaries_by_hash_prefix(self, prefix: str) -> list[Dictionary]:
        with self.session() as session:
            statement = select(Dictionary).where(
                Dictionary.hash.startswith(prefix, autoescape=True)
            )
            return list(session.scalars(statement.order_by(Dictionary.hash)))

    # --- vehicles -----------------------------------------------------------------------

    def upsert_vehicle(
        self,
        vehicle_id: str,
        name: str | None = None,
        kind: str | None = None,
        gds_endpoint: str | None = None,
    ) -> Vehicle:
        if kind is not None and kind not in VEHICLE_KINDS:
            raise ValueError(f"vehicle kind must be one of {VEHICLE_KINDS}, not {kind!r}")
        with self.session() as session:
            row = session.get(Vehicle, vehicle_id)
            if row is None:
                row = Vehicle(
                    id=vehicle_id,
                    name=name or vehicle_id,
                    kind=kind or ("testbed" if vehicle_id == "testbed" else "spacecraft"),
                    gds_endpoint=gds_endpoint,
                )
                session.add(row)
            else:
                if name is not None:
                    row.name = name
                if kind is not None:
                    row.kind = kind
                if gds_endpoint is not None:
                    row.gds_endpoint = gds_endpoint
            return row

    def get_vehicle(self, vehicle_id: str) -> Vehicle | None:
        with self.session() as session:
            return session.get(Vehicle, vehicle_id)

    def require_vehicle(self, vehicle_id: str) -> Vehicle:
        vehicle = self.get_vehicle(vehicle_id)
        if vehicle is None:
            raise UnknownVehicleError(vehicle_id)
        return vehicle

    def list_vehicles(self) -> list[Vehicle]:
        with self.session() as session:
            return list(session.scalars(select(Vehicle).order_by(Vehicle.id)))

    # --- vehicle state ------------------------------------------------------------------

    def set_vehicle_state(
        self,
        vehicle_id: str,
        project_version: str,
        observed_at: datetime | None = None,
        dictionary_id: str | None = None,
        source: str = "manual",
    ) -> VehicleState:
        """Store the latest build identity reading for a vehicle."""
        self.require_vehicle(vehicle_id)
        with self.session() as session:
            row = session.get(VehicleState, vehicle_id)
            if row is None:
                row = VehicleState(vehicle_id=vehicle_id)
                session.add(row)
            row.observed_project_version = project_version
            row.observed_dictionary_id = dictionary_id
            row.observed_at = observed_at or utcnow()
            row.resolved_dictionary_hash = None
            row.source = source
            return row

    def set_resolved_dictionary(self, vehicle_id: str, dictionary_hash: str | None) -> None:
        with self.session() as session:
            row = session.get(VehicleState, vehicle_id)
            if row is not None:
                row.resolved_dictionary_hash = dictionary_hash

    def get_vehicle_state(self, vehicle_id: str) -> VehicleState | None:
        with self.session() as session:
            return session.get(VehicleState, vehicle_id)

    # --- compiles -----------------------------------------------------------------------

    def add_compile(self, record: Compile) -> Compile:
        with self.session() as session:
            session.add(record)
        return record

    def get_compile(self, compile_id: int) -> Compile | None:
        with self.session() as session:
            return session.get(Compile, compile_id)

    def compiles_for_bytecode(self, bytecode_sha256: str) -> list[Compile]:
        with self.session() as session:
            statement = select(Compile).where(Compile.bytecode_sha256 == bytecode_sha256)
            return list(session.scalars(statement.order_by(Compile.id)))

    def list_compiles(self, vehicle_id: str | None = None, limit: int = 50) -> list[Compile]:
        with self.session() as session:
            statement = select(Compile)
            if vehicle_id is not None:
                statement = statement.where(Compile.vehicle_id == vehicle_id)
            return list(session.scalars(statement.order_by(Compile.id.desc()).limit(limit)))

    # --- decisions ----------------------------------------------------------------------

    def add_decision(self, decision: GateDecision) -> GateDecision:
        row = GateDecisionRow(
            compile_id=decision.compile_id,
            vehicle_id=decision.vehicle_id,
            bytecode_sha256=decision.bytecode_sha256,
            running_dictionary_hash=decision.running_dictionary_hash,
            result=decision.result.value,
            checks=[c.to_dict() for c in decision.checks],
            decided_at=decision.decided_at,
        )
        with self.session() as session:
            session.add(row)
        return row.to_decision()

    def get_decision(self, decision_id: int) -> GateDecision | None:
        with self.session() as session:
            row = session.get(GateDecisionRow, decision_id)
            return row.to_decision() if row else None

    def list_decisions(self, vehicle_id: str | None = None, limit: int = 50) -> list[GateDecision]:
        with self.session() as session:
            statement = select(GateDecisionRow)
            if vehicle_id is not None:
                statement = statement.where(GateDecisionRow.vehicle_id == vehicle_id)
            rows = session.scalars(statement.order_by(GateDecisionRow.id.desc()).limit(limit))
            return [row.to_decision() for row in rows]

    def latest_decision(self, vehicle_id: str, bytecode_sha256: str) -> GateDecision | None:
        with self.session() as session:
            statement = (
                select(GateDecisionRow)
                .where(
                    GateDecisionRow.vehicle_id == vehicle_id,
                    GateDecisionRow.bytecode_sha256 == bytecode_sha256,
                )
                .order_by(GateDecisionRow.id.desc())
                .limit(1)
            )
            row = session.scalars(statement).first()
            return row.to_decision() if row else None

    # --- uplinks ------------------------------------------------------------------------

    def add_uplink(
        self, decision_id: int, vehicle_id: str, ok: bool, sent_at: datetime, detail: str = ""
    ) -> Uplink:
        row = Uplink(
            decision_id=decision_id, vehicle_id=vehicle_id, ok=ok, sent_at=sent_at, detail=detail
        )
        with self.session() as session:
            session.add(row)
        return row

    def list_uplinks(self, vehicle_id: str | None = None, limit: int = 50) -> list[Uplink]:
        with self.session() as session:
            statement = select(Uplink)
            if vehicle_id is not None:
                statement = statement.where(Uplink.vehicle_id == vehicle_id)
            return list(session.scalars(statement.order_by(Uplink.id.desc()).limit(limit)))

    # --- testbed runs -------------------------------------------------------------------

    def add_testbed_run(self, run: TestbedRun) -> TestbedRun:
        with self.session() as session:
            session.add(run)
        return run

    def testbed_runs_for(self, bytecode_sha256: str, dictionary_hash: str) -> list[TestbedRun]:
        with self.session() as session:
            statement = select(TestbedRun).where(
                TestbedRun.bytecode_sha256 == bytecode_sha256,
                TestbedRun.dictionary_hash == dictionary_hash,
            )
            return list(session.scalars(statement.order_by(TestbedRun.id)))

    def list_testbed_runs(self, limit: int = 50) -> list[TestbedRun]:
        with self.session() as session:
            return list(
                session.scalars(select(TestbedRun).order_by(TestbedRun.id.desc()).limit(limit))
            )

    # --- flight rules -------------------------------------------------------------------

    def replace_flight_rules(self, rules: Sequence[FlightRule]) -> None:
        with self.session() as session:
            for existing in session.scalars(select(FlightRule)):
                session.delete(existing)
            session.flush()
            session.add_all(rules)

    def flight_rules_for(self, dictionary_hash: str | None) -> list[FlightRule]:
        """Rules that apply to every dictionary plus rules keyed to this one."""
        with self.session() as session:
            rows = list(session.scalars(select(FlightRule).order_by(FlightRule.id)))
        return [
            r for r in rows if r.dictionary_hash is None or r.dictionary_hash == dictionary_hash
        ]

    def list_flight_rules(self) -> list[FlightRule]:
        with self.session() as session:
            return list(session.scalars(select(FlightRule).order_by(FlightRule.id)))
