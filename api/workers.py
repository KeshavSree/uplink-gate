"""Functions that run inside the compile process pool. They must stay importable and picklable."""

from __future__ import annotations

import os
import time
from typing import Any

from gate.compile import CompileError, run_compiler


def compile_worker(
    sequence_path: str, dictionary_path: str, output_path: str, pacing_s: float = 0.0
) -> dict[str, Any]:
    """Compile one sequence. Returns a plain dict so nothing custom crosses the process boundary."""
    started = time.perf_counter()
    if pacing_s > 0:
        # Demo pacing only: the real compile takes a fraction of a second, which is too quick
        # to watch three of them overlap. Off unless GATE_COMPILE_PACING_S is set.
        time.sleep(pacing_s)
    result: dict[str, Any] = {"pid": os.getpid()}
    try:
        result["output"] = run_compiler(sequence_path, dictionary_path, output_path)
    except CompileError as error:
        result["error"] = str(error)
        result["log"] = error.output
    result["duration_ms"] = round((time.perf_counter() - started) * 1000)
    return result
