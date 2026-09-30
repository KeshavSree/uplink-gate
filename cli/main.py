"""`gate` command line interface. A thin layer over the gate library."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from gate.compile import CompileError, compile_sequence
from gate.config import GateConfig
from gate.dictionary import DictionaryError
from gate.engine import evaluate
from gate.registry import VEHICLE_KINDS, Registry, UnknownVehicleError, utcnow
from gate.resolve import resolve_running_dictionary
from gate.results import CheckStatus, GateDecision

EXIT_PASS = 0
EXIT_ERROR = 1
EXIT_BLOCK = 2

app = typer.Typer(
    help="Uplink gate: blocks an Fpy sequence unless it was compiled with the dictionary "
    "of the flight software build the vehicle is actually running.",
    no_args_is_help=True,
    add_completion=False,
)
dict_app = typer.Typer(help="Dictionaries known to the registry.", no_args_is_help=True)
vehicle_app = typer.Typer(help="Vehicles known to the registry.", no_args_is_help=True)
state_app = typer.Typer(
    help="The latest build identity reading of a vehicle.", no_args_is_help=True
)
app.add_typer(dict_app, name="dict")
app.add_typer(vehicle_app, name="vehicle")
app.add_typer(state_app, name="state")

console = Console(highlight=False)
error_console = Console(stderr=True, highlight=False)


def _registry() -> Registry:
    return Registry(GateConfig.from_env())


def _fail(message: str) -> typer.Exit:
    error_console.print(f"[red]error:[/red] {message}")
    return typer.Exit(EXIT_ERROR)


def _short(value: str | None, length: int = 12) -> str:
    return value[:length] if value else "-"


# --- dict ---------------------------------------------------------------------------------


@dict_app.command("add")
def dict_add(
    path: Annotated[Path, typer.Argument(help="A <Deployment>TopologyDictionary.json file.")],
) -> None:
    """Hash a dictionary and store its metadata in the registry."""
    try:
        row = _registry().add_dictionary(path)
    except DictionaryError as error:
        raise _fail(str(error)) from error
    console.print(f"dictionary [bold]{row.hash}[/bold]")
    console.print(f"  deployment       {row.deployment_name}")
    console.print(f"  project version  {row.project_version}")
    console.print(f"  framework        {row.framework_version}")


@dict_app.command("list")
def dict_list() -> None:
    """List the dictionaries in the registry."""
    table = Table("hash", "project version", "deployment", "loaded at")
    for row in _registry().list_dictionaries():
        table.add_row(
            _short(row.hash, 16),
            row.project_version,
            row.deployment_name,
            f"{row.loaded_at:%F %T}Z",
        )
    console.print(table)


# --- vehicle ------------------------------------------------------------------------------


@vehicle_app.command("add")
def vehicle_add(
    vehicle_id: Annotated[str, typer.Argument(help="Short ID, for example sc1.")],
    kind: Annotated[str, typer.Option(help=f"One of: {', '.join(VEHICLE_KINDS)}.")] = "spacecraft",
    name: Annotated[str | None, typer.Option(help="Display name.")] = None,
    gds: Annotated[str | None, typer.Option(help="host:port of the vehicle's GDS.")] = None,
) -> None:
    """Add a vehicle, or update one that already exists."""
    try:
        row = _registry().upsert_vehicle(vehicle_id, name=name, kind=kind, gds_endpoint=gds)
    except ValueError as error:
        raise _fail(str(error)) from error
    console.print(f"vehicle [bold]{row.id}[/bold] ({row.kind})")


@vehicle_app.command("list")
def vehicle_list() -> None:
    """List vehicles with what each one is running."""
    registry = _registry()
    now = utcnow()
    table = Table("vehicle", "kind", "project version", "running dictionary", "reading age")
    for vehicle in registry.list_vehicles():
        state = registry.get_vehicle_state(vehicle.id)
        resolution = resolve_running_dictionary(registry, vehicle.id, now)
        table.add_row(
            vehicle.id,
            vehicle.kind,
            state.observed_project_version if state else "-",
            _short(resolution.dictionary_hash) if resolution.ok else resolution.reason.value,
            f"{(now - state.observed_at).total_seconds():.0f} s" if state else "-",
        )
    console.print(table)


# --- state --------------------------------------------------------------------------------


@state_app.command("set")
def state_set(
    vehicle_id: Annotated[str, typer.Argument(help="Vehicle ID. Created if it does not exist.")],
    project_version: Annotated[
        str, typer.Option("--project-version", help="The project version the vehicle reports.")
    ],
    dictionary_id: Annotated[
        str | None,
        typer.Option("--dictionary-id", help="The dictionary ID the vehicle reports, if any."),
    ] = None,
    observed_at: Annotated[
        str | None,
        typer.Option("--observed-at", help="ISO 8601 time of the reading. Default: now."),
    ] = None,
) -> None:
    """Record a build identity reading by hand. A stand-in for live telemetry."""
    registry = _registry()
    when = utcnow()
    if observed_at is not None:
        try:
            when = datetime.fromisoformat(observed_at)
        except ValueError as error:
            raise _fail(f"--observed-at is not an ISO 8601 time: {observed_at}") from error
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
    registry.upsert_vehicle(vehicle_id)
    registry.set_vehicle_state(
        vehicle_id, project_version, observed_at=when, dictionary_id=dictionary_id, source="manual"
    )
    resolution = resolve_running_dictionary(registry, vehicle_id, utcnow())
    console.print(f"{vehicle_id}: project version [bold]{project_version}[/bold]")
    console.print(f"  {resolution.message}")


@state_app.command("show")
def state_show(vehicle_id: Annotated[str, typer.Argument(help="Vehicle ID.")]) -> None:
    """Show the latest reading for a vehicle and the dictionary it resolves to."""
    registry = _registry()
    state = registry.get_vehicle_state(vehicle_id)
    resolution = resolve_running_dictionary(registry, vehicle_id, utcnow())
    if state is not None:
        console.print(
            f"{vehicle_id}: project version [bold]{state.observed_project_version}[/bold]"
        )
        console.print(f"  dictionary ID  {state.observed_dictionary_id or '-'}")
        console.print(f"  observed at    {state.observed_at.isoformat()} ({state.source})")
    console.print(f"  {resolution.message}")


# --- compile ------------------------------------------------------------------------------


@app.command("compile")
def compile_command(
    sequence: Annotated[Path, typer.Argument(help="The .fpy sequence to compile.")],
    vehicle: Annotated[
        str | None,
        typer.Option(
            "--vehicle", "-v", help="Compile with the dictionary this vehicle is running."
        ),
    ] = None,
    dictionary: Annotated[
        str | None,
        typer.Option(
            "--dictionary", "-d", help="Override: a dictionary hash (or unique prefix) or file."
        ),
    ] = None,
    output: Annotated[
        Path | None, typer.Option("--output", "-o", help="Bytecode path. Default: <seq>.bin.")
    ] = None,
) -> None:
    """Compile a sequence and write its compile record and sidecar file."""
    registry = _registry()
    if dictionary is not None:
        if Path(dictionary).is_file():
            try:
                dictionary_hash = registry.add_dictionary(Path(dictionary)).hash
            except DictionaryError as error:
                raise _fail(str(error)) from error
        else:
            found = registry.find_dictionary(dictionary)
            if found is None:
                raise _fail(f"'{dictionary}' is not a file and not a unique dictionary hash prefix")
            dictionary_hash = found.hash
    elif vehicle is not None:
        resolution = resolve_running_dictionary(registry, vehicle, utcnow())
        if not resolution.ok:
            error_console.print(
                f"[red]cannot compile for {vehicle}:[/red] {resolution.reason.value}: "
                f"{resolution.message}"
            )
            error_console.print("  use --dictionary to choose a dictionary explicitly")
            raise typer.Exit(EXIT_ERROR)
        assert resolution.dictionary_hash is not None
        dictionary_hash = resolution.dictionary_hash
    else:
        raise _fail("give --vehicle or --dictionary")

    try:
        record = compile_sequence(
            registry, sequence, dictionary_hash, output_path=output, vehicle_id=vehicle
        )
    except CompileError as error:
        error_console.print(f"[red]compile failed:[/red] {error}")
        if error.output:
            error_console.print(error.output, markup=False)
        raise typer.Exit(EXIT_ERROR) from error

    row = registry.get_dictionary(dictionary_hash)
    assert row is not None
    console.print(f"compiled [bold]{os.path.relpath(record.bytecode_path)}[/bold]")
    console.print(f"  bytecode    {_short(record.bytecode_sha256)}")
    console.print(f"  dictionary  {_short(record.dictionary_hash)} ({row.project_version})")
    console.print(f"  compiler    fprime-fpy {record.compiler_version}")


# --- check --------------------------------------------------------------------------------

_STATUS_STYLE = {
    CheckStatus.PASS: ("green", "PASS "),
    CheckStatus.BLOCK: ("red", "BLOCK"),
    CheckStatus.SKIPPED: ("yellow", "SKIP "),
}


def print_decision(decision: GateDecision, bytecode: Path) -> None:
    colour = "green" if decision.passed else "red"
    console.print(
        f"[bold {colour}]{decision.result.value}[/bold {colour}]  "
        f"{bytecode.name} -> {decision.vehicle_id}"
    )
    for check in decision.checks:
        style, label = _STATUS_STYLE[check.status]
        console.print(f"  [{style}]{label}[/{style}] {check.check:<14} {check.reason.value}")
        console.print(f"        {check.message}", markup=False)


@app.command("check")
def check_command(
    bytecode: Annotated[Path, typer.Argument(help="The compiled .bin file.")],
    vehicle: Annotated[str, typer.Option("--vehicle", "-v", help="The target vehicle.")],
    as_json: Annotated[bool, typer.Option("--json", help="Print the decision as JSON.")] = False,
    allow_dirty: Annotated[
        bool, typer.Option("--allow-dirty", help="Let a -dirty or -broken project version pass.")
    ] = False,
    max_age: Annotated[
        float | None,
        typer.Option("--max-age", help="Seconds before a build identity reading is stale."),
    ] = None,
    checks: Annotated[
        str | None, typer.Option("--checks", help="Comma-separated checks to run.")
    ] = None,
) -> None:
    """Run the gate. Exit code 0 on PASS, 2 on BLOCK, 1 on a usage error."""
    if not bytecode.is_file():
        raise _fail(f"{bytecode} does not exist")
    config = GateConfig.from_env()
    if allow_dirty:
        config = config.with_(allow_dirty=True)
    if max_age is not None:
        config = config.with_(max_state_age_s=max_age)
    registry = Registry(config)
    try:
        decision = evaluate(
            registry,
            bytecode,
            vehicle,
            config=config,
            checks=[c.strip() for c in checks.split(",")] if checks else None,
        )
    except (ValueError, UnknownVehicleError) as error:
        raise _fail(str(error)) from error

    if as_json:
        console.print_json(json.dumps(decision.to_dict()))
    else:
        print_decision(decision, bytecode)
    raise typer.Exit(EXIT_PASS if decision.passed else EXIT_BLOCK)


@app.command("log")
def log_command(
    vehicle: Annotated[
        str | None, typer.Option("--vehicle", "-v", help="Only this vehicle.")
    ] = None,
    limit: Annotated[int, typer.Option(help="How many decisions to show.")] = 20,
) -> None:
    """Show recent gate decisions, newest first."""
    table = Table("id", "decided at", "vehicle", "bytecode", "result", "reasons")
    for decision in _registry().list_decisions(vehicle_id=vehicle, limit=limit):
        colour = "green" if decision.passed else "red"
        reasons = ", ".join(c.reason.value for c in decision.blocking) or "OK"
        table.add_row(
            str(decision.id),
            f"{decision.decided_at:%F %T}Z",
            decision.vehicle_id,
            _short(decision.bytecode_sha256),
            f"[{colour}]{decision.result.value}[/{colour}]",
            reasons,
        )
    console.print(table)


@app.command("serve")
def serve_command(
    host: Annotated[str, typer.Option(help="Address to listen on.")] = "127.0.0.1",
    port: Annotated[int, typer.Option(help="Port to listen on.")] = 8000,
) -> None:
    """Run the gate service (REST and WebSocket API, telemetry ingest, web UI)."""
    try:
        import uvicorn
    except ImportError as error:
        raise _fail("the service needs the 'server' extra: uv sync --extra server") from error
    uvicorn.run("api.app:create_app", factory=True, host=host, port=port)


if __name__ == "__main__":
    app()
