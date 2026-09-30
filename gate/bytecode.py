"""Read Fpy bytecode statically: which commands it sends, with which arguments, and when.

The bytes are deserialized with the Fpy package's own reader (the one `fprime-fpy-disasm`
uses). Command opcodes and argument bytes are then decoded with a dictionary, which gives
the view of the sequence that the flight rule check and the simulation check work on.

This is a static reading. It follows the order of the directives in the file and does not
execute them. The limits that follow from that are stated on `SequenceModel`.
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class BytecodeError(ValueError):
    """The file is not readable Fpy bytecode."""


class DecodeError(ValueError):
    """Argument bytes do not fit the command's arguments in this dictionary."""


@dataclass(frozen=True)
class CommandCall:
    """One command the sequence sends."""

    #: Index of the directive in the bytecode.
    index: int
    opcode: int | None
    #: Full command name in the dictionary, or None if the opcode is not in it.
    name: str | None
    #: Decoded arguments by name. None when they are not constants or do not decode.
    args: dict[str, Any] | None
    #: True when the arguments (or the opcode) are computed while the sequence runs.
    dynamic: bool = False
    #: Why the call could not be fully decoded, if it could not.
    problem: str | None = None

    @property
    def label(self) -> str:
        if self.name:
            return self.name
        return f"opcode 0x{self.opcode:x}" if self.opcode is not None else "unknown command"


@dataclass(frozen=True)
class Wait:
    """A relative wait. `seconds` is None when the duration is not a constant."""

    index: int
    seconds: float | None


@dataclass(frozen=True)
class TelemetryRead:
    """The sequence reads a telemetry channel (and may branch on it)."""

    index: int
    channel_id: int
    channel: str | None


@dataclass
class SequenceModel:
    """What a bytecode file does, read in directive order.

    Limits of this view:
    * Arguments built from variables are reported as dynamic, with no values.
    * Branches are not evaluated. `jumps` lists them so callers can be conservative.
    * Absolute waits and waits with a computed duration have no known length.
    """

    directive_count: int
    commands: list[CommandCall] = field(default_factory=list)
    waits: list[Wait] = field(default_factory=list)
    #: (index of the jumping directive, index it can jump to)
    jumps: list[tuple[int, int]] = field(default_factory=list)
    telemetry_reads: list[TelemetryRead] = field(default_factory=list)
    mnemonics: list[str] = field(default_factory=list)

    @property
    def has_loop(self) -> bool:
        return any(target <= source for source, target in self.jumps)

    def min_seconds_between(self, first_index: int, second_index: int) -> float:
        """The least time that certainly passes between two directives in file order.

        Counts constant relative waits lying between them, except a wait that a forward
        jump starting at or after `first_index` can skip.
        """
        total = 0.0
        for wait in self.waits:
            if not first_index < wait.index < second_index or wait.seconds is None:
                continue
            skippable = any(
                first_index <= source < wait.index < target for source, target in self.jumps
            )
            if not skippable:
                total += wait.seconds
        return total

    def loops_over(self, first_index: int, second_index: int) -> bool:
        """True if a backward jump encloses both directives, so either can follow the other."""
        low, high = sorted((first_index, second_index))
        return any(target <= low and source >= high for source, target in self.jumps)


_STRUCT_CODES = {
    ("integer", 8, False): ">B",
    ("integer", 8, True): ">b",
    ("integer", 16, False): ">H",
    ("integer", 16, True): ">h",
    ("integer", 32, False): ">I",
    ("integer", 32, True): ">i",
    ("integer", 64, False): ">Q",
    ("integer", 64, True): ">q",
    ("float", 32, None): ">f",
    ("float", 64, None): ">d",
}


class CommandDictionary:
    """The parts of a dictionary needed to decode command calls out of bytecode."""

    def __init__(self, document: dict[str, Any]) -> None:
        self.commands_by_opcode: dict[int, dict[str, Any]] = {
            int(c["opcode"]): c for c in document.get("commands", [])
        }
        self.commands_by_name: dict[str, dict[str, Any]] = {
            c["name"]: c for c in document.get("commands", [])
        }
        self.channels_by_id: dict[int, str] = {
            int(c["id"]): c["name"] for c in document.get("telemetryChannels", [])
        }
        self.types: dict[str, dict[str, Any]] = {
            t["qualifiedName"]: t for t in document.get("typeDefinitions", [])
        }
        size_store = self.types.get("FwSizeStoreType", {}).get("underlyingType")
        #: The type of the length prefix in front of every serialized string.
        self.string_length_type: dict[str, Any] = size_store or {
            "name": "U16",
            "kind": "integer",
            "size": 16,
            "signed": False,
        }

    @classmethod
    def load(cls, path: Path | str) -> CommandDictionary:
        return cls(json.loads(Path(path).read_bytes()))

    def command_names(self) -> list[str]:
        return sorted(self.commands_by_name)

    def decode_args(self, command: dict[str, Any], data: bytes) -> dict[str, Any]:
        """Decode the argument bytes of a command. Raises DecodeError if they do not fit."""
        offset = 0
        values: dict[str, Any] = {}
        for parameter in command.get("formalParams", []):
            values[parameter["name"]], offset = self._decode(parameter["type"], data, offset)
        if offset != len(data):
            raise DecodeError(
                f"{len(data) - offset} bytes left over after the last argument of {command['name']}"
            )
        return values

    def _decode(self, type_: dict[str, Any], data: bytes, offset: int) -> tuple[Any, int]:
        kind = type_["kind"]
        if kind in ("integer", "float"):
            code = _STRUCT_CODES.get((kind, type_["size"], type_.get("signed")))
            if code is None:
                raise DecodeError(f"unsupported {kind} type {type_.get('name')}")
            return self._unpack(code, data, offset)
        if kind == "bool":
            value, offset = self._unpack(">B", data, offset)
            return bool(value), offset
        if kind == "string":
            length, offset = self._decode(self.string_length_type, data, offset)
            end = offset + int(length)
            if end > len(data):
                raise DecodeError("string runs past the end of the arguments")
            return data[offset:end].decode("utf-8", errors="replace"), end
        if kind == "qualifiedIdentifier":
            definition = self.types.get(type_["name"])
            if definition is None:
                raise DecodeError(f"type {type_['name']} is not in the dictionary")
            return self._decode_defined(definition, data, offset)
        raise DecodeError(f"unsupported type kind {kind}")

    def _decode_defined(
        self, definition: dict[str, Any], data: bytes, offset: int
    ) -> tuple[Any, int]:
        kind = definition["kind"]
        if kind == "alias":
            return self._decode(definition["underlyingType"], data, offset)
        if kind == "enum":
            raw, offset = self._decode(definition["representationType"], data, offset)
            for constant in definition.get("enumeratedConstants", []):
                if constant["value"] == raw:
                    return constant["name"], offset
            raise DecodeError(f"{raw} is not a value of enum {definition['qualifiedName']}")
        if kind == "array":
            items = []
            for _ in range(int(definition["size"])):
                item, offset = self._decode(definition["elementType"], data, offset)
                items.append(item)
            return items, offset
        if kind == "struct":
            members = sorted(definition["members"].items(), key=lambda m: m[1]["index"])
            value: dict[str, Any] = {}
            for name, member in members:
                count = member.get("size")
                if count is None:
                    value[name], offset = self._decode(member["type"], data, offset)
                else:
                    value[name] = []
                    for _ in range(int(count)):
                        item, offset = self._decode(member["type"], data, offset)
                        value[name].append(item)
            return value, offset
        raise DecodeError(f"unsupported type definition kind {kind}")

    @staticmethod
    def _unpack(code: str, data: bytes, offset: int) -> tuple[Any, int]:
        size = struct.calcsize(code)
        if offset + size > len(data):
            raise DecodeError("arguments are shorter than the command's argument list")
        return struct.unpack_from(code, data, offset)[0], offset + size


def read_directives(bytecode_path: Path | str) -> list[Any]:
    """Deserialize a bytecode file into Fpy directive objects."""
    from fpy.bytecode.assembler import deserialize_directives

    try:
        directives, _ = deserialize_directives(Path(bytecode_path).read_bytes())
    except Exception as error:  # the Fpy reader raises several unrelated exception types
        raise BytecodeError(
            f"cannot read {Path(bytecode_path).name} as Fpy bytecode: {error}"
        ) from error
    return list(directives)


def analyse(directives: list[Any], dictionary: CommandDictionary) -> SequenceModel:
    """Build the static model of a sequence from its directives."""
    model = SequenceModel(directive_count=len(directives))
    for index, directive in enumerate(directives):
        kind = type(directive).__name__
        model.mnemonics.append(kind.removesuffix("Directive"))

        if kind == "ConstCmdDirective":
            model.commands.append(_const_command(index, directive, dictionary))
        elif kind == "StackCmdDirective":
            model.commands.append(_stack_command(index, directives, dictionary))
        elif kind == "WaitRelDirective":
            model.waits.append(Wait(index, _constant_wait(index, directives)))
        elif kind == "WaitAbsDirective":
            model.waits.append(Wait(index, None))
        elif kind == "GotoDirective":
            model.jumps.append((index, int(directive.dir_idx)))
        elif kind == "IfDirective":
            model.jumps.append((index, int(directive.false_goto_dir_index)))
        elif kind == "PushTlmValDirective":
            channel_id = int(directive.chan_id)
            model.telemetry_reads.append(
                TelemetryRead(index, channel_id, dictionary.channels_by_id.get(channel_id))
            )
    return model


def _const_command(index: int, directive: Any, dictionary: CommandDictionary) -> CommandCall:
    opcode = int(directive.cmd_opcode)
    command = dictionary.commands_by_opcode.get(opcode)
    if command is None:
        return CommandCall(
            index, opcode, None, None, problem=f"opcode 0x{opcode:x} is not in the dictionary"
        )
    try:
        args = dictionary.decode_args(command, bytes(directive.args))
    except DecodeError as error:
        return CommandCall(index, opcode, command["name"], None, problem=str(error))
    return CommandCall(index, opcode, command["name"], args)


def _stack_command(index: int, directives: list[Any], dictionary: CommandDictionary) -> CommandCall:
    """A command whose arguments come from the stack. The compiler pushes the opcode last,
    as a constant, so the command is usually still identifiable."""
    previous: Any = directives[index - 1] if index > 0 else None
    if type(previous).__name__ == "PushValDirective" and len(previous.val) == 4:
        opcode = int.from_bytes(previous.val, "big")
        command = dictionary.commands_by_opcode.get(opcode)
        name = command["name"] if command else None
        problem = None if command else f"opcode 0x{opcode:x} is not in the dictionary"
        return CommandCall(index, opcode, name, None, dynamic=True, problem=problem)
    return CommandCall(
        index, None, None, None, dynamic=True, problem="opcode is computed while running"
    )


def _constant_wait(index: int, directives: list[Any]) -> float | None:
    """`wait_rel` pops seconds then microseconds. Constant if both were just pushed."""
    if index < 2:
        return None
    seconds, useconds = directives[index - 2], directives[index - 1]
    for directive in (seconds, useconds):
        if type(directive).__name__ != "PushValDirective" or len(directive.val) != 4:
            return None
    return int.from_bytes(seconds.val, "big") + int.from_bytes(useconds.val, "big") / 1e6


def load_model(bytecode_path: Path | str, dictionary_path: Path | str) -> SequenceModel:
    """Read a bytecode file and decode it with a dictionary."""
    return analyse(read_directives(bytecode_path), CommandDictionary.load(dictionary_path))
