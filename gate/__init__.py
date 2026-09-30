"""Uplink gate core library.

Confirms that an Fpy sequence was compiled with the dictionary of the flight software build
a vehicle is actually running, before the sequence may be uplinked. This package has no web
dependency: the CLI, the API and the UI all call it.
"""

from gate.config import GateConfig
from gate.engine import evaluate
from gate.registry import Registry
from gate.results import CheckResult, CheckStatus, GateDecision, Reason, Result

__all__ = [
    "CheckResult",
    "CheckStatus",
    "GateConfig",
    "GateDecision",
    "Reason",
    "Registry",
    "Result",
    "evaluate",
]
