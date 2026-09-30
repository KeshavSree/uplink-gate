from __future__ import annotations

import json
from pathlib import Path

import pytest

from gate.dictionary import (
    DictionaryError,
    dictionary_id_for,
    hash_prefix_from_dictionary_id,
    is_dirty_version,
    load_dictionary,
)
from tests.conftest import DICT_A, DICT_B, DICT_B_STALE, FIXTURES, VERSION_A, VERSION_B


def test_load_reads_metadata_and_hashes_file_bytes() -> None:
    info = load_dictionary(DICT_A)
    built = json.loads((FIXTURES / "dictionaries" / "A" / "build.json").read_text())
    assert info.hash == built["dictionary_sha256"]
    assert info.project_version == VERSION_A
    assert info.deployment_name == "Ref.Ref"
    assert info.dictionary_id == built["dictionary_id"]


def test_real_builds_differ_only_in_one_argument_type() -> None:
    a = json.loads(DICT_A.read_text())
    b = json.loads(DICT_B.read_text())
    assert load_dictionary(DICT_A).hash != load_dictionary(DICT_B).hash
    assert load_dictionary(DICT_B).project_version == VERSION_B

    def frequency_type(document: dict) -> str:
        command = next(c for c in document["commands"] if c["name"] == "Ref.SG1.Settings")
        return command["formalParams"][0]["type"]["name"]

    assert frequency_type(a) == "U32"
    assert frequency_type(b) == "U16"


def test_stale_version_build_shares_a_label_with_a_different_dictionary() -> None:
    a = load_dictionary(DICT_A)
    stale = load_dictionary(DICT_B_STALE)
    assert stale.project_version == a.project_version
    assert stale.hash != a.hash


def test_whitespace_change_is_a_different_dictionary(tmp_path: Path) -> None:
    copy = tmp_path / "copy.json"
    copy.write_bytes(DICT_A.read_bytes() + b"\n")
    assert load_dictionary(copy).hash != load_dictionary(DICT_A).hash


@pytest.mark.parametrize(
    "content",
    ["not json", "[]", "{}", '{"metadata": {"deploymentName": "Ref.Ref"}}'],
)
def test_rejects_files_that_are_not_dictionaries(tmp_path: Path, content: str) -> None:
    path = tmp_path / "bad.json"
    path.write_text(content)
    with pytest.raises(DictionaryError):
        load_dictionary(path)


def test_missing_file_is_a_dictionary_error(tmp_path: Path) -> None:
    with pytest.raises(DictionaryError):
        load_dictionary(tmp_path / "nope.json")


def test_dictionary_id_round_trip() -> None:
    full = "4bbb2268b356ad9e8ca4c46a2ca666665235f8cc599d6ce11ccf740482bdef37"
    assert dictionary_id_for(full) == "dict-sha256:4bbb2268b356ad9e"
    assert hash_prefix_from_dictionary_id(dictionary_id_for(full)) == "4bbb2268b356ad9e"


@pytest.mark.parametrize(
    "value", ["", "no_ver", "dict-sha256:", "dict-sha256:unknown", "sha1:abcd"]
)
def test_values_that_are_not_dictionary_ids(value: str) -> None:
    assert hash_prefix_from_dictionary_id(value) is None


@pytest.mark.parametrize(
    ("version", "dirty"),
    [
        ("gate-demo-1.0.0", False),
        ("v4.3.0-12-gabc1234", False),
        ("gate-demo-1.0.0-dirty", True),
        ("v4.3.0-12-gabc1234-broken", True),
    ],
)
def test_dirty_versions(version: str, dirty: bool) -> None:
    assert is_dirty_version(version) is dirty
