"""Compile an Fpy sequence and record which dictionary it was compiled with.

Fpy bytecode carries the compiler version and a checksum in its header, but nothing that
says which dictionary it was compiled against. The gate fills that gap in two places:

* a compile record in the registry, and
* a sidecar file `<out>.bin.gate.json` next to the bytecode, so the record travels with the
  file even where the registry database is not available.
"""

from __future__ import annotations

import json
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from gate.dictionary import sha256_file
from gate.registry import Compile, Registry, utcnow

SIDECAR_SUFFIX = ".gate.json"
SIDECAR_VERSION = 1


class CompileError(RuntimeError):
    """The Fpy compiler rejected the sequence. `output` holds what it printed."""

    def __init__(self, message: str, output: str = "") -> None:
        super().__init__(message)
        self.output = output


class SidecarError(ValueError):
    """The sidecar file exists but cannot be used."""


@dataclass(frozen=True)
class Sidecar:
    """The portable compile record written next to every bytecode file."""

    bytecode_sha256: str
    dictionary_hash: str
    project_version: str
    sequence_sha256: str
    compiler_version: str
    compiled_at: str
    sidecar_version: int = SIDECAR_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class CompilerOutput:
    """What one run of the Fpy compiler produced. Holds no registry state."""

    sequence_path: str
    sequence_sha256: str
    dictionary_path: str
    bytecode_path: str
    bytecode_sha256: str
    compiler_version: str
    log: str


def compiler_version() -> str:
    try:
        return version("fprime-fpy")
    except PackageNotFoundError:
        return "unknown"


def sidecar_path(bytecode_path: Path | str) -> Path:
    bytecode_path = Path(bytecode_path)
    return bytecode_path.with_name(bytecode_path.name + SIDECAR_SUFFIX)


def read_sidecar(bytecode_path: Path | str) -> Sidecar | None:
    """Read the sidecar of a bytecode file. None if there is no sidecar."""
    path = sidecar_path(bytecode_path)
    if not path.exists():
        return None
    try:
        data: Any = json.loads(path.read_text(encoding="utf-8"))
        return Sidecar(
            bytecode_sha256=str(data["bytecode_sha256"]),
            dictionary_hash=str(data["dictionary_hash"]),
            project_version=str(data.get("project_version", "")),
            sequence_sha256=str(data.get("sequence_sha256", "")),
            compiler_version=str(data.get("compiler_version", "")),
            compiled_at=str(data.get("compiled_at", "")),
            sidecar_version=int(data.get("sidecar_version", SIDECAR_VERSION)),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise SidecarError(f"sidecar {path} is not readable: {error}") from error


def run_compiler(
    sequence_path: Path | str, dictionary_path: Path | str, output_path: Path | str | None = None
) -> CompilerOutput:
    """Run `fprime-fpyc` in a subprocess. Safe to call from a process pool worker."""
    sequence_path = Path(sequence_path)
    dictionary_path = Path(dictionary_path)
    output_path = Path(output_path) if output_path else sequence_path.with_suffix(".bin")
    if not sequence_path.is_file():
        raise CompileError(f"sequence {sequence_path} does not exist")
    if not dictionary_path.is_file():
        raise CompileError(f"dictionary {dictionary_path} does not exist")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Run the compiler's own entry point with this interpreter, so the compiler version
    # recorded below is the one that actually ran, whatever is on PATH.
    command = [
        sys.executable,
        "-c",
        "from fpy.main import compile_main; compile_main()",
        str(sequence_path),
        "--dictionary",
        str(dictionary_path),
        "--output",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    log = (completed.stdout + completed.stderr).strip()
    if completed.returncode != 0 or not output_path.is_file():
        raise CompileError(f"fprime-fpyc failed for {sequence_path.name}", output=log)

    return CompilerOutput(
        sequence_path=str(sequence_path),
        sequence_sha256=sha256_file(sequence_path),
        dictionary_path=str(dictionary_path),
        bytecode_path=str(output_path),
        bytecode_sha256=sha256_file(output_path),
        compiler_version=compiler_version(),
        log=log,
    )


def record_compile(
    registry: Registry,
    output: CompilerOutput,
    dictionary_hash: str,
    vehicle_id: str | None = None,
    compiled_at: datetime | None = None,
) -> Compile:
    """Write the sidecar and the compile record for a finished compiler run."""
    dictionary = registry.get_dictionary(dictionary_hash)
    if dictionary is None:
        raise CompileError(f"dictionary {dictionary_hash} is not in the registry")
    compiled_at = compiled_at or utcnow()

    sidecar = Sidecar(
        bytecode_sha256=output.bytecode_sha256,
        dictionary_hash=dictionary.hash,
        project_version=dictionary.project_version,
        sequence_sha256=output.sequence_sha256,
        compiler_version=output.compiler_version,
        compiled_at=compiled_at.isoformat(),
    )
    sidecar_path(output.bytecode_path).write_text(sidecar.to_json(), encoding="utf-8")

    return registry.add_compile(
        Compile(
            sequence_sha256=output.sequence_sha256,
            sequence_path=str(Path(output.sequence_path).resolve()),
            dictionary_hash=dictionary.hash,
            compiler_version=output.compiler_version,
            bytecode_sha256=output.bytecode_sha256,
            bytecode_path=str(Path(output.bytecode_path).resolve()),
            vehicle_id=vehicle_id,
            created_at=compiled_at,
        )
    )


def compile_sequence(
    registry: Registry,
    sequence_path: Path | str,
    dictionary_hash: str,
    output_path: Path | str | None = None,
    vehicle_id: str | None = None,
) -> Compile:
    """Compile a sequence with a registered dictionary and record the compile."""
    dictionary = registry.get_dictionary(dictionary_hash)
    if dictionary is None:
        raise CompileError(f"dictionary {dictionary_hash} is not in the registry")
    output = run_compiler(sequence_path, dictionary.path, output_path)
    return record_compile(registry, output, dictionary.hash, vehicle_id=vehicle_id)
