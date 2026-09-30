"""The CLI end to end, including the exit codes a CI step would rely on."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cli.main import EXIT_BLOCK, EXIT_ERROR, EXIT_PASS, app
from tests.conftest import DICT_A, DICT_B, VERSION_A, VERSION_B

runner = CliRunner()


@pytest.fixture(autouse=True)
def gate_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "gate-home"
    monkeypatch.setenv("GATE_HOME", str(home))
    monkeypatch.delenv("GATE_DATABASE_URL", raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    return home


def run(*args: str | Path, code: int = EXIT_PASS) -> str:
    result = runner.invoke(app, [str(a) for a in args])
    assert result.exit_code == code, result.output
    return result.output


def test_sixty_second_demo(sequence: Path) -> None:
    """The README walkthrough: compile for sc1, pass, swap the build, block."""
    run("dict", "add", DICT_A)
    run("dict", "add", DICT_B)
    run("state", "set", "sc1", "--project-version", VERSION_A)

    out = run("compile", sequence, "--vehicle", "sc1")
    assert VERSION_A in out
    bytecode = sequence.with_suffix(".bin")
    assert bytecode.is_file()

    out = run("check", bytecode, "--vehicle", "sc1")
    assert "PASS" in out

    run("state", "set", "sc1", "--project-version", VERSION_B)
    out = run("check", bytecode, "--vehicle", "sc1", code=EXIT_BLOCK)
    assert "BLOCK" in out
    assert "DICTIONARY_MISMATCH" in out

    out = run("log")
    assert "DICTIONARY_MISMATCH" in out


def test_check_json_output_is_machine_readable(sequence: Path) -> None:
    run("dict", "add", DICT_A)
    run("state", "set", "sc1", "--project-version", VERSION_A)
    run("compile", sequence, "--vehicle", "sc1")
    out = run("check", sequence.with_suffix(".bin"), "--vehicle", "sc1", "--json")
    decision = json.loads(out)
    assert decision["result"] == "PASS"
    assert decision["checks"][0]["check"] == "build_match"
    assert decision["checks"][0]["reason"] == "OK"


def test_check_blocks_bytecode_without_a_compile_record(tmp_path: Path) -> None:
    run("dict", "add", DICT_A)
    run("state", "set", "sc1", "--project-version", VERSION_A)
    stray = tmp_path / "stray.bin"
    stray.write_bytes(b"\x00\x06\x00\x07")
    out = run("check", stray, "--vehicle", "sc1", code=EXIT_BLOCK)
    assert "NO_COMPILE_RECORD" in out


def test_check_max_age_flag(sequence: Path) -> None:
    run("dict", "add", DICT_A)
    run(
        "state",
        "set",
        "sc1",
        "--project-version",
        VERSION_A,
        "--observed-at",
        "2026-01-01T00:00:00Z",
    )
    out = run("compile", sequence, "--vehicle", "sc1", code=EXIT_ERROR)
    assert "STALE_BUILD_IDENTITY" in out
    run("compile", sequence, "--dictionary", DICT_A)
    bytecode = sequence.with_suffix(".bin")
    out = run("check", bytecode, "--vehicle", "sc1", code=EXIT_BLOCK)
    assert "STALE_BUILD_IDENTITY" in out
    run("check", bytecode, "--vehicle", "sc1", "--max-age", "1e12")


def test_compile_with_dictionary_hash_prefix(sequence: Path) -> None:
    run("dict", "add", DICT_A)
    run("dict", "add", DICT_B)
    out = run("compile", sequence, "--dictionary", "d6c09e7f")
    assert VERSION_B in out
    run("compile", sequence, "--dictionary", "ffff", code=EXIT_ERROR)


def test_compile_needs_a_vehicle_or_a_dictionary(sequence: Path) -> None:
    run("compile", sequence, code=EXIT_ERROR)


def test_compile_error_shows_the_compiler_output(tmp_path: Path) -> None:
    run("dict", "add", DICT_A)
    bad = tmp_path / "bad.fpy"
    bad.write_text("Ref.nothing.NOPE()\n")
    out = run("compile", bad, "--dictionary", DICT_A, code=EXIT_ERROR)
    assert "compile failed" in out


def test_missing_bytecode_is_a_usage_error_not_a_block(tmp_path: Path) -> None:
    run("check", tmp_path / "nope.bin", "--vehicle", "sc1", code=EXIT_ERROR)


def test_dict_add_rejects_a_non_dictionary(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{}")
    run("dict", "add", bad, code=EXIT_ERROR)


def test_vehicle_and_state_listing(sequence: Path) -> None:
    run("dict", "add", DICT_A)
    run("vehicle", "add", "testbed", "--kind", "testbed")
    run("state", "set", "sc1", "--project-version", VERSION_A)
    out = run("vehicle", "list")
    assert "testbed" in out and "sc1" in out and "NO_BUILD_IDENTITY" in out
    out = run("state", "show", "sc1")
    assert VERSION_A in out
    assert VERSION_A in run("dict", "list")
    run("vehicle", "add", "x", "--kind", "rover", code=EXIT_ERROR)


def test_rules_load_and_list(tmp_path: Path) -> None:
    rules = Path(__file__).parent.parent / "rules" / "demo_rules.yaml"
    assert "loaded 3 flight rule(s)" in run("rules", "load", rules)
    out = run("rules", "list")
    assert "sg-frequency-range" in out and "min_separation" in out
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules: nope")
    run("rules", "load", bad, code=EXIT_ERROR)


def test_check_shows_each_check_separately(sequence: Path) -> None:
    rules = Path(__file__).parent.parent / "rules" / "demo_rules.yaml"
    run("dict", "add", DICT_A)
    run("rules", "load", rules)
    run("vehicle", "add", "testbed", "--kind", "testbed")
    run("state", "set", "sc1", "--project-version", VERSION_A)
    run("compile", sequence, "--vehicle", "sc1")
    out = run(
        "check",
        sequence.with_suffix(".bin"),
        "--vehicle",
        "sc1",
        "--checks",
        "build_match,testbed_equiv,flight_rules",
        code=EXIT_BLOCK,
    )
    lines = [
        line for line in out.splitlines() if line.startswith("  ") and not line.startswith("    ")
    ]
    assert [line.split()[:3] for line in lines] == [
        ["PASS", "build_match", "OK"],
        ["BLOCK", "testbed_equiv", "NO_TESTBED_RUN"],
        ["PASS", "flight_rules", "OK"],
    ]
