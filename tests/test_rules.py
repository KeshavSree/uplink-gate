"""The flight rule format and the three rule types, against real compiled sequences."""

from __future__ import annotations

from pathlib import Path

import pytest

from gate.bytecode import load_model
from gate.compile import run_compiler
from gate.rules import Rule, RuleError, evaluate_rule, load_rules_file, parse_rules
from tests.conftest import DICT_A, SEQUENCES

DEMO_RULES = SEQUENCES.parent / "rules" / "demo_rules.yaml"


def model_of(tmp_path: Path, source: str):
    sequence = tmp_path / "seq.fpy"
    sequence.write_text(source)
    return load_model(run_compiler(sequence, DICT_A, tmp_path / "seq.bin").bytecode_path, DICT_A)


def rule(rule_type: str, **params: object) -> Rule:
    return parse_rules({"rules": [{"id": "r", "type": rule_type, **params}]})[0]


def test_demo_rules_file_parses() -> None:
    rules = load_rules_file(DEMO_RULES)
    assert {r.id for r in rules} == {"sg-frequency-range", "sg-retune-spacing", "no-parameter-save"}
    assert {r.type for r in rules} == {"argument_range", "min_separation", "forbidden_command"}


@pytest.mark.parametrize(
    "document",
    [
        None,
        {},
        {"rules": "x"},
        {"rules": ["x"]},
        {"rules": [{"type": "forbidden_command", "command": "X"}]},
        {"rules": [{"id": "a", "type": "nope"}]},
        {"rules": [{"id": "a", "type": "forbidden_command"}]},
        {"rules": [{"id": "a", "type": "forbidden_command", "command": "X", "extra": 1}]},
        {"rules": [{"id": "a", "type": "argument_range", "command": "X", "argument": "y"}]},
        {
            "rules": [
                {"id": "a", "type": "min_separation", "first": "X", "second": "Y", "seconds": "2"}
            ]
        },
        {
            "rules": [
                {"id": "a", "type": "forbidden_command", "command": "X"},
                {"id": "a", "type": "forbidden_command", "command": "Y"},
            ]
        },
    ],
)
def test_malformed_rules_are_rejected(document: object) -> None:
    with pytest.raises(RuleError):
        parse_rules(document)


def test_unreadable_rules_file(tmp_path: Path) -> None:
    with pytest.raises(RuleError):
        load_rules_file(tmp_path / "missing.yaml")
    bad = tmp_path / "bad.yaml"
    bad.write_text("rules: [unclosed")
    with pytest.raises(RuleError):
        load_rules_file(bad)


def test_forbidden_command(tmp_path: Path) -> None:
    model = model_of(tmp_path, "Ref.SG1.Toggle()\nRef.SG2.Toggle()\nRef.SG1.Skip()\n")
    assert [
        v.command for v in evaluate_rule(rule("forbidden_command", command="Ref.SG1.Toggle"), model)
    ] == ["Ref.SG1.Toggle"]
    assert len(evaluate_rule(rule("forbidden_command", command="Ref.SG*.Toggle"), model)) == 2
    assert evaluate_rule(rule("forbidden_command", command="Ref.SG3.*"), model) == []


def test_forbidden_command_also_catches_dynamic_calls(tmp_path: Path) -> None:
    model = model_of(tmp_path, "f: U32 = 4\nRef.SG2.Settings(f, 1.0, 0.0, Ref.SignalType.SINE)\n")
    assert len(evaluate_rule(rule("forbidden_command", command="Ref.SG2.Settings"), model)) == 1


def test_argument_range(tmp_path: Path) -> None:
    model = model_of(
        tmp_path,
        "Ref.SG1.Settings(5, 1.0, 0.0, Ref.SignalType.SINE)\n"
        "Ref.SG1.Settings(20, 1.0, 0.0, Ref.SignalType.SINE)\n"
        "Ref.SG2.Settings(0, 1.0, 0.0, Ref.SignalType.NOISE)\n",
    )
    frequency = rule(
        "argument_range", command="Ref.SG*.Settings", argument="Frequency", min=1, max=12
    )
    messages = [v.message for v in evaluate_rule(frequency, model)]
    assert messages == [
        "Ref.SG1.Settings Frequency=20 is above 12",
        "Ref.SG2.Settings Frequency=0 is below 1",
    ]

    signal_type = rule(
        "argument_range", command="Ref.SG*.Settings", argument="SigType", allowed=["SINE", "SQUARE"]
    )
    assert [v.command for v in evaluate_rule(signal_type, model)] == ["Ref.SG2.Settings"]

    missing = rule("argument_range", command="Ref.SG1.Settings", argument="Nope", min=0)
    assert all("no argument named Nope" in v.message for v in evaluate_rule(missing, model))

    not_a_number = rule("argument_range", command="Ref.SG1.Settings", argument="SigType", max=3)
    assert all("is not a number" in v.message for v in evaluate_rule(not_a_number, model))


def test_argument_rule_cannot_verify_a_computed_argument(tmp_path: Path) -> None:
    model = model_of(tmp_path, "f: U32 = 4\nRef.SG2.Settings(f, 1.0, 0.0, Ref.SignalType.SINE)\n")
    frequency = rule("argument_range", command="Ref.SG2.Settings", argument="Frequency", max=12)
    violations = evaluate_rule(frequency, model)
    assert len(violations) == 1 and "computed while the sequence runs" in violations[0].message


def test_min_separation(tmp_path: Path) -> None:
    spacing = rule("min_separation", first="Ref.SG1.Settings", second="Ref.SG1.Settings", seconds=2)
    good = model_of(
        tmp_path,
        "Ref.SG1.Settings(2, 1.0, 0.0, Ref.SignalType.SINE)\nsleep(3)\n"
        "Ref.SG1.Settings(4, 1.0, 0.0, Ref.SignalType.SINE)\n",
    )
    assert evaluate_rule(spacing, good) == []

    bad = model_of(
        tmp_path,
        "Ref.SG1.Settings(2, 1.0, 0.0, Ref.SignalType.SINE)\nsleep(1)\n"
        "Ref.SG1.Settings(4, 1.0, 0.0, Ref.SignalType.SINE)\n",
    )
    violations = evaluate_rule(spacing, bad)
    assert len(violations) == 1
    assert "after at least 1 s, but 2 s are required" in violations[0].message


def test_min_separation_between_two_different_commands(tmp_path: Path) -> None:
    settle = rule("min_separation", first="Ref.SG1.Settings", second="Ref.SG1.Toggle", seconds=2)
    model = model_of(
        tmp_path,
        "Ref.SG1.Toggle()\nRef.SG1.Settings(2, 1.0, 0.0, Ref.SignalType.SINE)\nRef.SG1.Toggle()\n",
    )
    violations = evaluate_rule(settle, model)
    # The Toggle before Settings is fine. The one right after it is not.
    assert [v.directive_index for v in violations] == [model.commands[2].index]


def test_min_separation_does_not_trust_a_conditional_wait(tmp_path: Path) -> None:
    spacing = rule("min_separation", first="Ref.SG1.Skip", second="Ref.SG1.Toggle", seconds=2)
    model = model_of(
        tmp_path,
        "Ref.SG1.Skip()\nif Ref.SG1.Output > 0.5:\n    sleep(10)\nRef.SG1.Toggle()\n",
    )
    assert len(evaluate_rule(spacing, model)) == 1


def test_min_separation_inside_a_loop_cannot_be_shown(tmp_path: Path) -> None:
    spacing = rule("min_separation", first="Ref.SG1.Skip", second="Ref.SG1.Toggle", seconds=1)
    model = model_of(
        tmp_path, "for i in 0..3:\n    Ref.SG1.Skip()\n    sleep(5)\n    Ref.SG1.Toggle()\n"
    )
    violations = evaluate_rule(spacing, model)
    assert len(violations) == 1 and "inside the same loop" in violations[0].message


def test_demo_sequences_against_demo_rules(tmp_path: Path) -> None:
    rules = load_rules_file(DEMO_RULES)

    def broken(name: str) -> set[str]:
        output = run_compiler(SEQUENCES / f"{name}.fpy", DICT_A, tmp_path / f"{name}.bin")
        model = load_model(output.bytecode_path, DICT_A)
        return {v.rule_id for r in rules for v in evaluate_rule(r, model)}

    assert broken("signal_setup") == set()
    assert broken("signal_sweep") == set()
    assert broken("signal_too_fast") == {"sg-frequency-range", "sg-retune-spacing"}
