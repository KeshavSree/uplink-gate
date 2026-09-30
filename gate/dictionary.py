"""Load an F Prime dictionary, hash it and read its metadata.

A dictionary is the JSON file each flight software build produces
(`<Deployment>TopologyDictionary.json`). It maps command, telemetry, event and parameter
names to IDs and binary types. The dictionary hash is the SHA-256 of the file's bytes. It is
the true identity of a dictionary: two dictionaries with the same project version label can
still differ, and then their hashes differ.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Prefix of the value the demo builds report in the CustomVersion01 telemetry channel.
DICTIONARY_ID_PREFIX = "dict-sha256:"
#: How many hex characters of the dictionary hash a build reports.
DICTIONARY_ID_HEX_CHARS = 16


class DictionaryError(ValueError):
    """The file is not a usable F Prime dictionary."""


@dataclass(frozen=True)
class DictionaryInfo:
    """Identity and metadata of one dictionary file."""

    hash: str
    deployment_name: str
    project_version: str
    framework_version: str
    spec_version: str
    path: Path

    @property
    def dictionary_id(self) -> str:
        """The short content identity a build reports in telemetry."""
        return dictionary_id_for(self.hash)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def dictionary_id_for(dictionary_hash: str) -> str:
    return f"{DICTIONARY_ID_PREFIX}{dictionary_hash[:DICTIONARY_ID_HEX_CHARS]}"


def hash_prefix_from_dictionary_id(dictionary_id: str) -> str | None:
    """Return the hex prefix inside a reported dictionary ID, or None if it is not one."""
    value = dictionary_id.strip().lower()
    if not value.startswith(DICTIONARY_ID_PREFIX):
        return None
    prefix = value[len(DICTIONARY_ID_PREFIX) :]
    if not prefix or any(c not in "0123456789abcdef" for c in prefix):
        return None
    return prefix


def load_dictionary(path: Path | str) -> DictionaryInfo:
    """Hash a dictionary file and read its metadata."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise DictionaryError(f"cannot read dictionary {path}: {error}") from error
    try:
        document: Any = json.loads(data)
    except ValueError as error:
        raise DictionaryError(f"{path} is not valid JSON: {error}") from error

    metadata = document.get("metadata") if isinstance(document, dict) else None
    if not isinstance(metadata, dict):
        raise DictionaryError(
            f"{path} has no 'metadata' object, so it is not an F Prime dictionary"
        )
    missing = [key for key in ("deploymentName", "projectVersion") if not metadata.get(key)]
    if missing:
        raise DictionaryError(f"{path} metadata is missing {', '.join(missing)}")

    return DictionaryInfo(
        hash=sha256_bytes(data),
        deployment_name=str(metadata["deploymentName"]),
        project_version=str(metadata["projectVersion"]),
        framework_version=str(metadata.get("frameworkVersion", "")),
        spec_version=str(metadata.get("dictionarySpecVersion", "")),
        path=path,
    )


def is_dirty_version(project_version: str) -> bool:
    """True if `git describe` marked the build as having uncommitted or unreadable state."""
    return project_version.endswith(("-dirty", "-broken"))
