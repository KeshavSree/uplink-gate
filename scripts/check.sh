#!/usr/bin/env bash
# Everything CI runs for the Python side: lint, format, types, unit tests.
set -euo pipefail
cd "$(dirname "$0")/.."
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -q
