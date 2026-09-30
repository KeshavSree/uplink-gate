"""Shared fixtures. Unit tests use SQLite in a temporary directory and make no network calls.

The dictionaries under tests/fixtures/dictionaries are real: they were produced by building
F Prime's Ref deployment (see vehicles/). `A` and `B` differ in the type of one command
argument. `B_stale_version` has B's content but A's project version, which is what an
incremental F Prime v4.3.0 build produces.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from gate.compile import compile_sequence
from gate.config import GateConfig
from gate.registry import Compile, Dictionary, Registry

FIXTURES = Path(__file__).parent / "fixtures"
DICT_A = FIXTURES / "dictionaries" / "A" / "RefTopologyDictionary.json"
DICT_B = FIXTURES / "dictionaries" / "B" / "RefTopologyDictionary.json"
DICT_B_STALE = FIXTURES / "dictionaries" / "B_stale_version" / "RefTopologyDictionary.json"
SEQUENCES = Path(__file__).parent.parent / "sequences"

VERSION_A = "gate-demo-1.0.0"
VERSION_B = "gate-demo-1.1.0"
NOW = datetime(2026, 9, 30, 12, 0, 0, tzinfo=UTC)


@pytest.fixture
def config(tmp_path: Path) -> GateConfig:
    return GateConfig(home=tmp_path / "gate-home")


@pytest.fixture
def registry(config: GateConfig) -> Registry:
    return Registry(config)


@pytest.fixture
def dict_a(registry: Registry) -> Dictionary:
    return registry.add_dictionary(DICT_A)


@pytest.fixture
def dict_b(registry: Registry) -> Dictionary:
    return registry.add_dictionary(DICT_B)


@pytest.fixture
def sequence(tmp_path: Path) -> Path:
    """A private copy of the demo sequence, so compiled output lands in the temp directory."""
    path = tmp_path / "signal_setup.fpy"
    path.write_text((SEQUENCES / "signal_setup.fpy").read_text())
    return path


@pytest.fixture
def compiled_a(registry: Registry, dict_a: Dictionary, sequence: Path) -> Compile:
    """The demo sequence compiled with dictionary A."""
    return compile_sequence(registry, sequence, dict_a.hash)
