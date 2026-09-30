# Uplink Gate

Ground software for the [F Prime](https://github.com/nasa/fprime) flight software framework
and its sequencing language, [Fpy](https://github.com/fprime-community/fpy). Before an Fpy
sequence is sent to a spacecraft, the uplink gate confirms that the sequence was compiled
with the dictionary of the flight software build that is **actually running** on that
vehicle, as reported by the vehicle's own telemetry. If the two do not match, it blocks the
upload and says why.

## Terms

These words are used with exactly these meanings in the code, the docs and the UI.

| Term | Meaning |
|---|---|
| **Build** | One compiled version of the F Prime flight software. |
| **Dictionary** | The JSON file each build produces (`<Deployment>TopologyDictionary.json`). It maps command, telemetry, event and parameter names to IDs and binary types. |
| **Dictionary hash** | The SHA-256 of the dictionary file's bytes. This is the true identity of a dictionary. |
| **Project version** | The `git describe --tags --always --dirty --broken` string F Prime stamps into a build. It is a label, not a hash. |
| **Dictionary ID** | A prefix of the dictionary hash that a build reports in telemetry (`dict-sha256:<16 hex chars>`). The demo builds in this repository report one. |
| **Sequence** | An `.fpy` source file. |
| **Bytecode** | The compiled `.bin` file the spacecraft runs. |
| **Vehicle** | Anything that runs a build and sends telemetry: `testbed`, `sc1`, `sc2`. |
| **Registry** | The database of dictionaries, vehicles, compiles and gate decisions. |
| **Compile record** | The gate's own record that bytecode X was compiled from sequence Y with dictionary hash Z. |
| **Sidecar** | The file `<out>.bin.gate.json` written next to every bytecode file. It holds the compile record, so the record travels with the file. |
| **Gate decision** | PASS or BLOCK, with a list of check results and reasons. |

## The problem

The Fpy compiler turns a sequence into bytecode using a dictionary: it looks up each command
name and writes the command's numeric opcode and its arguments in the binary layout the
dictionary describes. If the flight software on the vehicle was built from different source,
its dictionary can differ, and then the same bytes mean something else on board.

Nothing in the bytecode says which dictionary it was compiled with. The Fpy bytecode header
holds the compiler version, a schema version, an argument count, a statement count and a
body size, followed by a checksum
([`assembler.py`](https://github.com/fprime-community/fpy/blob/v0.6.0/src/fpy/bytecode/assembler.py),
`HEADER_FORMAT = "!BBBBBHI"`). The sequencer on board checks the schema version and the
checksum. Neither one changes when a command argument changes type.

This repository contains two real builds of F Prime's reference deployment (`Ref`) that
differ in one line: the `Frequency` argument of `Ref.SG1.Settings` is a `U32` in one build
and a `U16` in the other. The same sequence compiles to 236 bytes for the first build and
234 bytes for the second. Both files have a valid header and a valid checksum.

## 60-second demo

Needs only Python 3.12 and [uv](https://docs.astral.sh/uv/). No Docker, no web server.

```console
$ uv sync
$ source .venv/bin/activate

# 1. Tell the registry about the dictionaries of two builds.
$ gate dict add tests/fixtures/dictionaries/A/RefTopologyDictionary.json
dictionary 4bbb2268b356ad9e8ca4c46a2ca666665235f8cc599d6ce11ccf740482bdef37
  deployment       Ref.Ref
  project version  gate-demo-1.0.0
$ gate dict add tests/fixtures/dictionaries/B/RefTopologyDictionary.json
dictionary d6c09e7f3db838a98a4548a05c443bfc1aebfb86dc58b6cc23ae20d93243e2f8
  deployment       Ref.Ref
  project version  gate-demo-1.1.0

# 2. Vehicle sc1 reports that it runs gate-demo-1.0.0. (Here by hand. Phase 2 reads telemetry.)
$ gate state set sc1 --project-version gate-demo-1.0.0
sc1: project version gate-demo-1.0.0
  'sc1' is running dictionary 4bbb2268b356 (gate-demo-1.0.0), identified by project version label

# 3. Compile a sequence for sc1. The gate picks the dictionary sc1 is running.
$ gate compile sequences/signal_setup.fpy --vehicle sc1
compiled sequences/signal_setup.bin
  bytecode    335aca577057
  dictionary  4bbb2268b356 (gate-demo-1.0.0)
  compiler    fprime-fpy 0.6.0

# 4. Check it before uplink.
$ gate check sequences/signal_setup.bin --vehicle sc1; echo "exit code $?"
PASS  signal_setup.bin -> sc1
  PASS  build_match    OK
        compiled with dictionary 4bbb2268b356, which is what 'sc1' is running
exit code 0

# 5. sc1 is reflashed with the other build. The same bytecode is now wrong for it.
$ gate state set sc1 --project-version gate-demo-1.1.0
$ gate check sequences/signal_setup.bin --vehicle sc1; echo "exit code $?"
BLOCK  signal_setup.bin -> sc1
  BLOCK build_match    DICTIONARY_MISMATCH
        compiled with dictionary 4bbb2268b356 (gate-demo-1.0.0) but 'sc1' is running d6c09e7f3db8 (gate-demo-1.1.0)
exit code 2
```

`gate check` exits with 0 on PASS, 2 on BLOCK and 1 on a usage error, so it works as a CI
step. `--json` prints the decision for machines. A reading made with `gate state set` goes
stale after 60 seconds, like a telemetry reading, so step 4 must follow step 2 within a
minute (or pass `--max-age`).

## How the gate decides which dictionary a vehicle is running

1. Read the project version the vehicle reports.
2. Find the registry dictionaries whose `metadata.projectVersion` equals it.
   - Exactly one: that is the running dictionary.
   - None: BLOCK `UNKNOWN_BUILD`.
   - More than one: BLOCK `AMBIGUOUS_BUILD`, unless step 3 settles it.
3. If the vehicle also reports a dictionary ID, find the dictionary whose hash starts with
   it. The dictionary is then identified by content. The project version is still compared,
   and a disagreement is BLOCK `BUILD_IDENTITY_CONFLICT`.
4. A project version ending in `-dirty` or `-broken` is BLOCK `DIRTY_BUILD` by default.
5. A reading older than 60 seconds (configurable) is BLOCK `STALE_BUILD_IDENTITY`.

Every BLOCK reason is a stable code (`gate.results.Reason`) plus a human message. Nothing
downstream parses the message.

### Why a project version is not enough

While building the two demo builds I hit this by accident. In F Prime v4.3.0 the version
files are generated by a CMake custom command that has outputs but no dependencies
([`cmake/target/version.cmake`](https://github.com/nasa/fprime/blob/v4.3.0/cmake/target/version.cmake)),
so an incremental build does not refresh them. I changed the source, committed, tagged the
commit `gate-demo-1.1.0` and rebuilt in the same build directory. The result was a build
with the new argument type whose dictionary and binary were both still stamped
`gate-demo-1.0.0`.

That dictionary is kept as a test fixture
(`tests/fixtures/dictionaries/B_stale_version/`). With it in the registry, the project
version `gate-demo-1.0.0` maps to two different dictionaries, and the gate blocks with
`AMBIGUOUS_BUILD`. A dictionary ID removes the ambiguity, because it is derived from the
dictionary's content. This is the practice I would recommend: **have the flight software
report a hash of its own dictionary, and match on that.**

## What is in this repository

```
gate/        the gate core, a plain Python library with no web dependency
cli/         the `gate` command
vehicles/    Dockerfile and patches that build the two demo builds of Ref
sequences/   example Fpy sequences
tests/       pytest suite; dictionaries under tests/fixtures are from real Ref builds
```

Run the tests with `uv run pytest`. They use SQLite in a temporary directory and make no
network calls.

## Status

- [x] Phase 1: gate core and CLI
- [ ] Phase 2: real vehicles in Docker, telemetry ingest, API, uplink
- [ ] Phase 3: testbed equivalence and flight rule checks
- [ ] Phase 4: 3D web UI
- [ ] Phase 5: simulation check

## Scope and honesty

- This is a personal portfolio project. It is not used in flight by anyone.
- Commits authored from `fireflyspace.com` addresses have been merged into
  [nasa/fprime](https://github.com/nasa/fprime/commit/2291597e29),
  [fprime-gds](https://github.com/fprime-community/fprime-gds/pull/358) and
  [fpy](https://github.com/fprime-community/fpy/commit/7eae8d55a0), so Firefly Aerospace is
  building on F Prime and Fpy. I do not know what any Firefly vehicle flies, and this
  project claims nothing about that.
- [fprime-gds PR #358](https://github.com/fprime-community/fprime-gds/pull/358) fixed a bug
  of the same class on the ground side: `fprime-prm-write` wrote string length prefixes with
  a fixed 2-byte width instead of the width the dictionary declares, so "a U32 FSW" could
  not read them.
- I found no public example of a build-matched uplink gate for F Prime or Fpy. Private and
  internal tools cannot be seen, so that is all I can say.
- There is no authentication and no protection against a forged sidecar file. The gate
  guards against mistakes, not against an attacker.

Related tools that do neighbouring jobs:
[FRESH](https://github.com/NASA-JPL-Teamtools-Studio/tts_fresh) (flight rule checking) and
[Tower](https://github.com/NASA-JPL-Teamtools-Studio/tts_tower) (rule checking orchestrator)
from JPL's Teamtools Studio,
[PlanDev and SeqDev](https://github.com/NASA-AMMOS/plandev) from NASA AMMOS (planning and
sequence authoring), [Hermes](https://github.com/nasa/hermes) (commanding and telemetry
processing) and [Yamcs](https://github.com/yamcs/yamcs) (mission control framework).

## License

Apache-2.0. F Prime and Fpy are separate projects with their own licenses.
