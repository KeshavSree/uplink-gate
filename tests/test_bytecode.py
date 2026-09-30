"""Reading commands, arguments and waits out of real compiled bytecode."""

from __future__ import annotations

from pathlib import Path

import pytest

from gate.bytecode import BytecodeError, CommandDictionary, DecodeError, SequenceModel, load_model
from gate.compile import run_compiler
from tests.conftest import DICT_A, DICT_B


def model_of(tmp_path: Path, source: str, compile_with: Path = DICT_A, read_with: Path = DICT_A):
    sequence = tmp_path / "seq.fpy"
    sequence.write_text(source)
    output = run_compiler(sequence, compile_with, tmp_path / "seq.bin")
    return load_model(output.bytecode_path, read_with)


def test_constant_commands_decode_to_names_and_arguments(tmp_path: Path) -> None:
    model = model_of(
        tmp_path,
        "Ref.SG1.Settings(5, 2.5, 0.0, Ref.SignalType.SINE)\n"
        'CdhCore.cmdDisp.CMD_NO_OP_STRING("hi")\n'
        "Ref.SG1.Toggle()\n",
    )
    assert [c.name for c in model.commands] == [
        "Ref.SG1.Settings",
        "CdhCore.cmdDisp.CMD_NO_OP_STRING",
        "Ref.SG1.Toggle",
    ]
    assert model.commands[0].args == {
        "Frequency": 5,
        "Amplitude": 2.5,
        "Phase": 0.0,
        "SigType": "SINE",
    }
    assert model.commands[1].args == {"arg1": "hi"}
    assert model.commands[2].args == {}
    assert all(not c.dynamic and c.problem is None for c in model.commands)


def test_same_source_decodes_per_dictionary(tmp_path: Path) -> None:
    source = "Ref.SG1.Settings(5, 2.5, 0.0, Ref.SignalType.SINE)\n"
    for dictionary in (DICT_A, DICT_B):
        model = model_of(tmp_path, source, dictionary, dictionary)
        assert model.commands[0].args is not None
        assert model.commands[0].args["Frequency"] == 5


def test_bytecode_read_with_the_wrong_dictionary_does_not_decode(tmp_path: Path) -> None:
    """The static reader sees the same mismatch the flight software rejects at run time."""
    source = "Ref.SG1.Settings(5, 2.5, 0.0, Ref.SignalType.SINE)\n"
    a_read_as_b = model_of(tmp_path, source, compile_with=DICT_A, read_with=DICT_B)
    call = a_read_as_b.commands[0]
    assert call.name == "Ref.SG1.Settings" and call.args is None
    assert call.problem is not None and "bytes left over" in call.problem

    b_read_as_a = model_of(tmp_path, source, compile_with=DICT_B, read_with=DICT_A)
    assert b_read_as_a.commands[0].args is None
    assert "shorter" in (b_read_as_a.commands[0].problem or "")


def test_command_with_a_variable_argument_is_dynamic_but_named(tmp_path: Path) -> None:
    model = model_of(
        tmp_path,
        "freq: U32 = 4\nRef.SG2.Settings(freq, 1.0, 0.0, Ref.SignalType.SQUARE)\n",
    )
    call = model.commands[0]
    assert call.dynamic and call.name == "Ref.SG2.Settings" and call.args is None


def test_constant_waits_are_measured(tmp_path: Path) -> None:
    model = model_of(
        tmp_path,
        "Ref.SG1.Toggle()\nsleep(3)\nRef.SG1.Toggle()\nsleep(1, 500000)\nRef.SG1.Toggle()\n",
    )
    assert [w.seconds for w in model.waits] == [3.0, 1.5]
    first, second, third = (c.index for c in model.commands)
    assert model.min_seconds_between(first, second) == 3.0
    assert model.min_seconds_between(second, third) == 1.5
    assert model.min_seconds_between(first, third) == 4.5
    assert not model.has_loop


def test_a_wait_inside_a_branch_is_not_counted(tmp_path: Path) -> None:
    model = model_of(
        tmp_path,
        "Ref.SG1.Toggle()\nif Ref.SG1.Output > 0.5:\n    sleep(10)\nRef.SG1.Toggle()\n",
    )
    first, second = (c.index for c in model.commands)
    assert [w.seconds for w in model.waits] == [10.0]
    assert model.min_seconds_between(first, second) == 0.0
    assert [r.channel for r in model.telemetry_reads] == ["Ref.SG1.Output"]


def test_loops_are_detected(tmp_path: Path) -> None:
    model = model_of(
        tmp_path,
        "for i in 0..3:\n    Ref.SG1.Toggle()\n    sleep(1)\n    Ref.SG1.Skip()\n",
    )
    toggle, skip = (c.index for c in model.commands)
    assert model.has_loop and model.loops_over(toggle, skip)


def test_unknown_opcode_is_reported(tmp_path: Path) -> None:
    model = model_of(tmp_path, "Ref.fpySeq.CANCEL()\n")
    assert model.commands[0].name == "Ref.fpySeq.CANCEL"
    # The same bytes read with a dictionary that lacks the sequencer's commands.
    import json

    stripped = json.loads(DICT_A.read_text())
    stripped["commands"] = [c for c in stripped["commands"] if "fpySeq" not in c["name"]]
    reduced = tmp_path / "reduced.json"
    reduced.write_text(json.dumps(stripped))
    call = load_model(tmp_path / "seq.bin", reduced).commands[0]
    assert call.name is None and "not in the dictionary" in (call.problem or "")
    assert call.label.startswith("opcode 0x")


def test_garbage_is_not_bytecode(tmp_path: Path) -> None:
    junk = tmp_path / "junk.bin"
    junk.write_bytes(b"not bytecode at all")
    with pytest.raises(BytecodeError):
        load_model(junk, DICT_A)


def test_decoder_handles_every_command_in_the_real_dictionary() -> None:
    """Zero-filled arguments of the right length decode for all commands without a string or
    enum, which exercises integers, floats, bools, arrays and structs."""
    dictionary = CommandDictionary.load(DICT_A)
    assert dictionary.string_length_type["name"] == "U16"
    decoded = 0
    for command in dictionary.commands_by_name.values():
        for size in range(0, 400):
            try:
                dictionary.decode_args(command, bytes(size))
            except DecodeError:
                continue
            decoded += 1
            break
    assert decoded > 100


def test_min_seconds_ignores_waits_outside_the_range() -> None:
    from gate.bytecode import Wait

    model = SequenceModel(directive_count=10, waits=[Wait(1, 5.0), Wait(5, 2.0), Wait(9, 7.0)])
    assert model.min_seconds_between(2, 8) == 2.0
    model.jumps.append((3, 7))
    assert model.min_seconds_between(2, 8) == 0.0
