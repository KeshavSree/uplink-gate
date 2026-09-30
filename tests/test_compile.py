from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate.compile import (
    CompileError,
    SidecarError,
    compile_sequence,
    read_sidecar,
    run_compiler,
    sidecar_path,
)
from gate.dictionary import sha256_file
from gate.registry import Compile, Dictionary, Registry
from tests.conftest import DICT_A, DICT_B, VERSION_A


def test_compile_writes_bytecode_sidecar_and_record(
    registry: Registry, dict_a: Dictionary, compiled_a: Compile
) -> None:
    bytecode = Path(compiled_a.bytecode_path)
    assert bytecode.is_file()
    assert compiled_a.bytecode_sha256 == sha256_file(bytecode)
    assert compiled_a.dictionary_hash == dict_a.hash

    sidecar = read_sidecar(bytecode)
    assert sidecar is not None
    assert sidecar.bytecode_sha256 == compiled_a.bytecode_sha256
    assert sidecar.dictionary_hash == dict_a.hash
    assert sidecar.project_version == VERSION_A
    assert sidecar.sequence_sha256 == compiled_a.sequence_sha256
    assert sidecar.compiler_version == "0.6.0"

    assert [c.id for c in registry.compiles_for_bytecode(compiled_a.bytecode_sha256)] == [
        compiled_a.id
    ]


def test_sidecar_has_the_documented_keys(compiled_a: Compile) -> None:
    data = json.loads(sidecar_path(compiled_a.bytecode_path).read_text())
    assert {
        "bytecode_sha256",
        "dictionary_hash",
        "project_version",
        "sequence_sha256",
        "compiler_version",
        "compiled_at",
    } <= set(data)


def test_same_sequence_compiles_to_different_bytecode_per_dictionary(
    tmp_path: Path, sequence: Path
) -> None:
    a = run_compiler(sequence, DICT_A, tmp_path / "a.bin")
    b = run_compiler(sequence, DICT_B, tmp_path / "b.bin")
    assert a.sequence_sha256 == b.sequence_sha256
    assert a.bytecode_sha256 != b.bytecode_sha256


def test_compiler_error_is_reported_with_its_output(
    registry: Registry, dict_a: Dictionary, tmp_path: Path
) -> None:
    bad = tmp_path / "bad.fpy"
    bad.write_text("Ref.noSuchComponent.NOPE()\n")
    with pytest.raises(CompileError) as excinfo:
        compile_sequence(registry, bad, dict_a.hash)
    assert excinfo.value.output
    assert not (tmp_path / "bad.bin").exists()


def test_compile_needs_a_registered_dictionary(registry: Registry, sequence: Path) -> None:
    with pytest.raises(CompileError):
        compile_sequence(registry, sequence, "0" * 64)


def test_no_sidecar_reads_as_none(tmp_path: Path) -> None:
    assert read_sidecar(tmp_path / "missing.bin") is None


def test_corrupt_sidecar_is_an_error(tmp_path: Path) -> None:
    bytecode = tmp_path / "x.bin"
    bytecode.write_bytes(b"\x00")
    sidecar_path(bytecode).write_text("{not json")
    with pytest.raises(SidecarError):
        read_sidecar(bytecode)
