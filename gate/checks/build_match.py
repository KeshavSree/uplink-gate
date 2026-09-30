"""build_match: was this bytecode compiled with the dictionary the vehicle is running?

The check needs two facts and blocks if either is missing:

1. A compile record for exactly these bytecode bytes, from the registry or from the sidecar
   file next to the bytecode. It says which dictionary hash the bytecode was compiled with.
2. The dictionary hash the target vehicle is running right now, resolved from its latest
   build identity reading (see gate.resolve).

It passes only when the two hashes are equal.
"""

from __future__ import annotations

from gate.checks import CheckContext, blocked, passed
from gate.compile import SidecarError, read_sidecar, sidecar_path
from gate.resolve import resolve_running_dictionary
from gate.results import CheckResult, Reason

NAME = "build_match"


def build_match(ctx: CheckContext) -> CheckResult:
    # Resolve the vehicle first so later checks can use the result even if this one blocks
    # for a reason on the compile record side.
    resolution = resolve_running_dictionary(ctx.registry, ctx.vehicle_id, ctx.now, ctx.config)
    ctx.resolution = resolution

    try:
        sidecar = read_sidecar(ctx.bytecode_path)
    except SidecarError as error:
        return blocked(NAME, Reason.NO_COMPILE_RECORD, str(error))

    if sidecar is not None and sidecar.bytecode_sha256 != ctx.bytecode_sha256:
        return blocked(
            NAME,
            Reason.BYTECODE_MODIFIED,
            f"{ctx.bytecode_path.name} does not match its compile record: the file's SHA-256 is "
            f"{ctx.bytecode_sha256[:12]} but {sidecar_path(ctx.bytecode_path).name} was written "
            f"for {sidecar.bytecode_sha256[:12]}",
            bytecode_sha256=ctx.bytecode_sha256,
            sidecar_bytecode_sha256=sidecar.bytecode_sha256,
        )

    # The same bytes can be the result of compiling against more than one dictionary (a
    # sequence that does not touch anything that differs between them). Every record counts.
    records = ctx.registry.compiles_for_bytecode(ctx.bytecode_sha256)
    compiled_with: dict[str, str] = {}
    for record in records:
        dictionary = ctx.registry.get_dictionary(record.dictionary_hash)
        compiled_with[record.dictionary_hash] = dictionary.project_version if dictionary else ""
    if sidecar is not None:
        compiled_with.setdefault(sidecar.dictionary_hash, sidecar.project_version)

    if not compiled_with:
        return blocked(
            NAME,
            Reason.NO_COMPILE_RECORD,
            f"no compile record for {ctx.bytecode_path.name}: it has no sidecar file and the "
            "registry has never seen these bytes, so the dictionary it was compiled with "
            "is unknown",
            bytecode_sha256=ctx.bytecode_sha256,
        )

    if not resolution.ok:
        return blocked(NAME, resolution.reason, resolution.message, **resolution.details())

    running = resolution.dictionary_hash
    assert running is not None
    details = resolution.details() | {
        "compiled_with": [
            {"dictionary_hash": h, "project_version": v} for h, v in compiled_with.items()
        ],
    }

    if running in compiled_with:
        ctx.matching_compile = next((r for r in records if r.dictionary_hash == running), None)
        return passed(
            NAME,
            f"compiled with dictionary {running[:12]}, which is what '{ctx.vehicle_id}' is running",
            **details,
        )

    running_dictionary = ctx.registry.get_dictionary(running)
    running_version = running_dictionary.project_version if running_dictionary else "?"
    compiled_text = ", ".join(f"{h[:12]} ({v or '?'})" for h, v in compiled_with.items())
    return blocked(
        NAME,
        Reason.DICTIONARY_MISMATCH,
        f"compiled with dictionary {compiled_text} but '{ctx.vehicle_id}' is running "
        f"{running[:12]} ({running_version})",
        **details,
    )
