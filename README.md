# Uplink Gate

Ground software for the [F Prime](https://github.com/nasa/fprime) flight software framework
and its sequencing language, [Fpy](https://github.com/fprime-community/fpy). Before an Fpy
sequence is sent to a spacecraft, the uplink gate confirms that the sequence was compiled
with the dictionary of the flight software build that is **actually running** on that
vehicle, as reported by the vehicle's own telemetry. If the two do not match, it blocks the
upload and says why. Three more checks build on that: the same bytes must have run on a
testbed with the same dictionary, must break no flight rule, and must not drive a small
modeled state somewhere it must not go, starting from live telemetry.

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
| **Flight rule** | A documented way the vehicle must not be operated, written so a program can check it. |

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
differ in one line: the `Frequency` argument of `Ref.SG1.Settings` is a `U32` in build A and
a `U16` in build B. The same sequence compiles to different bytes for each. Both files have
a valid header and a valid checksum.

### What happens without the gate

I uplinked mismatched bytecode to a running vehicle directly, bypassing the gate. In both
directions (bytecode for build B on build A, and the reverse) the Fpy sequencer accepted the
file, ran the first statement, and then the mismatched command was rejected:

```
Ref.fpySeq.LogActivityHi            Sequence /tmp/uplink/forB.bin: signal_setup: configuring SG1
CdhCore.cmdDisp.OpCodeDispatched    Opcode 0x10011000 dispatched to port 12
CdhCore.cmdDisp.OpCodeError         Opcode 0x10011000 completed with error FORMAT_ERROR
Ref.fpySeq.SequenceExitedWithError  Sequence /tmp/uplink/forB.bin exited with error code 17
```

So the result was a sequence that stopped partway, after its earlier statements had already
run. F Prime caught it only because the two argument layouts have different sizes (a command
handler checks that its argument buffer is fully consumed). A change that keeps the size the
same, such as swapping two arguments of the same width or renumbering an enumeration, would
not be caught that way.

## 60-second demo (terminal)

Needs only Python 3.12 and [uv](https://docs.astral.sh/uv/). No Docker, no server.

```console
$ uv sync
$ source .venv/bin/activate

# 1. Tell the registry about the dictionaries of two builds.
$ gate dict add tests/fixtures/dictionaries/A/RefTopologyDictionary.json
dictionary 4bbb2268b356ad9e8ca4c46a2ca666665235f8cc599d6ce11ccf740482bdef37
  deployment       Ref.Ref
  project version  gate-demo-1.0.0
$ gate dict add tests/fixtures/dictionaries/B/RefTopologyDictionary.json

# 2. Vehicle sc1 reports that it runs gate-demo-1.0.0. (By hand here; the service reads telemetry.)
$ gate state set sc1 --project-version gate-demo-1.0.0
sc1: project version gate-demo-1.0.0
  'sc1' is running dictionary 4bbb2268b356 (gate-demo-1.0.0), identified by project version label

# 3. Compile a sequence for sc1. The gate picks the dictionary sc1 is running.
$ gate compile sequences/signal_setup.fpy --vehicle sc1
compiled sequences/signal_setup.bin
  bytecode    3141d3131bdf
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

## Live demo: three vehicles, the service and the 3D UI

Needs Docker. The first `up` builds F Prime's `Ref` deployment twice.

```console
$ docker compose up --build -d
$ open http://localhost:8000          # the UI; Grafana is on http://localhost:3000
```

If a port is taken, set `GATE_PORT`, `GRAFANA_PORT` or `INFLUX_PORT` (for example in `.env`).

This starts three vehicles, each a container running a real build of `Ref` and a real
`fprime-gds`, plus the gate service, PostgreSQL (the registry), InfluxDB and Grafana.

| Vehicle | Kind | Build at start |
|---|---|---|
| `testbed` | testbed | A, `gate-demo-1.0.0` |
| `sc1` | spacecraft | A, `gate-demo-1.0.0` |
| `sc2` | spacecraft | B, `gate-demo-1.1.0` |

The gate service connects to each vehicle's GDS, asks the flight software for its versions
(`CdhCore.version.VERSION`) every 10 seconds, and reads the answer from telemetry: the
project version from `CdhCore.version.ProjectVersion` and the dictionary ID from
`CdhCore.version.CustomVersion01`. No one tells the gate what a vehicle is running.

The UI shows Earth, the Moon and the three vehicles on simple orbits, each coloured by its
gate state. Selecting a vehicle opens its console: the project version and dictionary hash
it reports, how fresh that reading is, the bytecode compiled so far with Check and Uplink
buttons, each check's result, and the flight software's own events. "Compile for all
vehicles" starts one compile per vehicle at once, each with that vehicle's dictionary, in
separate worker processes.

The live swap reflashes a vehicle while everything keeps running:

```console
$ ./scripts/reflash.sh sc1 B
```

Within about ten seconds the gate reads the new build from telemetry, and bytecode that
passed for `sc1` a moment ago is blocked. [`docs/demo.md`](docs/demo.md) is a step-by-step
script for showing all of this.

### Publishing the demo

The vehicles and the gate service need Docker and long-lived connections, so they run on a
machine you control. `scripts/publish.sh` makes that machine's stack public: it opens a free
[Cloudflare quick tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/do-more-with-tunnels/trycloudflare/)
to the gate service and deploys the web UI to Vercel, with `/api` sent through the tunnel.

```console
$ echo "GATE_WRITE_TOKEN=<a password>" >> .env && docker compose up -d
$ ./scripts/publish.sh           # needs cloudflared and a logged-in vercel CLI
$ ./scripts/publish.sh --stop    # close the tunnel
```

With `GATE_WRITE_TOKEN` set, anyone can watch, but compiling, checking and uplinking ask
for the password. A quick tunnel gets a new address when it restarts (for example after the
machine sleeps), so run `publish.sh` again then.

### API

| Method and path | What it does |
|---|---|
| `GET /api/vehicles`, `GET /api/vehicles/{id}` | what each vehicle is running, link status, recent compiles, decisions and events |
| `POST /api/compile`, `POST /api/compile/all` | start compile jobs (`?wait=true` to wait for the result) |
| `POST /api/check` | run the gate for a compile record and a vehicle; returns the decision |
| `POST /api/uplink` | send bytecode and run it; `409` if the gate refuses |
| `GET /api/decisions`, `/api/compiles`, `/api/uplinks`, `/api/testbed-runs`, `/api/rules` | history |
| `WS /api/events` | vehicle state changes, flight software events, compile progress, gate decisions |

`POST /api/uplink` is the only path that sends bytecode. It refuses without a PASS decision
for exactly those bytes on that vehicle that is at most 120 seconds old. It then asks the
vehicle for its versions again and re-runs the checks immediately before sending, so a
vehicle that changed build after the decision is still caught. Bytecode is stored on the
vehicle under its own hash, and the adapter waits for the Fpy sequencer's completion event.

## The checks

A gate decision is BLOCK if any check blocks. Every check runs and reports separately, so a
decision shows all the reasons at once. The CLI runs `build_match` by default (`--checks`
or `GATE_CHECKS` selects others); the service in `docker compose` runs all four.

| Check | Passes when |
|---|---|
| `build_match` | the bytecode was compiled with the dictionary the vehicle is running |
| `testbed_equiv` | these exact bytes already ran successfully on a testbed that was running the same dictionary |
| `flight_rules` | the sequence breaks no flight rule, read with the vehicle's dictionary |
| `simulation` | starting from the vehicle's latest telemetry, the sequence keeps the modeled state within its constraints |

```console
$ gate rules load rules/demo_rules.yaml
$ GATE_SIMULATION_MODEL=rules/demo_model.yaml gate check sequences/signal_setup.bin --vehicle sc1 \
    --checks build_match,testbed_equiv,flight_rules,simulation
BLOCK  signal_setup.bin -> sc1
  PASS  build_match    OK
        compiled with dictionary 4bbb2268b356, which is what 'sc1' is running
  BLOCK testbed_equiv  NO_TESTBED_RUN
        this bytecode has not been run on a testbed running dictionary 4bbb2268b356
  PASS  flight_rules   OK
        3 rule(s) hold for the 2 command(s) in the sequence
  BLOCK simulation     SIMULATION_VIOLATION
        sg1_running is not known when Ref.SG1.Settings is sent, but it must be false
```

(The CLI has no telemetry, so the simulation cannot know the starting state and blocks. The
service passes it the vehicle's live telemetry.)

### build_match: how the gate decides which dictionary a vehicle is running

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

Then the compile record: the registry's records for exactly these bytes, plus the sidecar
next to the file. No record is BLOCK `NO_COMPILE_RECORD`; a sidecar written for other bytes
is BLOCK `BYTECODE_MODIFIED`. The check passes only if one of the recorded dictionary hashes
is the running one. Every BLOCK reason is a stable code (`gate.results.Reason`) plus a human
message; nothing downstream parses the message.

#### Why a project version is not enough

While building the two demo builds I hit this by accident. In F Prime v4.3.0 the version
files are generated by a CMake custom command that has outputs but no dependencies
([`cmake/target/version.cmake`](https://github.com/nasa/fprime/blob/v4.3.0/cmake/target/version.cmake)),
so an incremental build does not refresh them. I changed the source, committed, tagged the
commit `gate-demo-1.1.0` and rebuilt in the same build directory. The result was a build
with the new argument type whose dictionary and binary were both still stamped
`gate-demo-1.0.0`.

That dictionary is kept as a test fixture (`tests/fixtures/dictionaries/B_stale_version/`).
With it in the registry, the project version `gate-demo-1.0.0` maps to two different
dictionaries, and the gate blocks with `AMBIGUOUS_BUILD`. A dictionary ID removes the
ambiguity, because it is derived from the dictionary's content. This is the practice I
would recommend: **have the flight software report a hash of its own dictionary, and match
on that.** The demo builds do it with a 20-line component (`Ref.DictionaryId`, see
[`vehicles/`](vehicles/README.md)) that sets a `Svc.Version` custom version at startup.

### testbed_equiv

A testbed run is evidence only for the bytes that ran and the dictionary the testbed was
running at the time. The gate records a `testbed_run` by itself when the testbed's Fpy
sequencer reports that a sequence ended (`SequenceDone`, or a warning that names the file).
The uplink adapter stores bytecode on a vehicle under its hash, so the event identifies the
bytes. After a vehicle changes build, earlier testbed runs stop counting for it, and a
testbed still on the old build cannot produce new evidence.

### flight_rules

The rule format is small and declarative ([`rules/demo_rules.yaml`](rules/demo_rules.yaml),
described in [`gate/rules.py`](gate/rules.py)) and has three rule types: a forbidden
command, a range or set of allowed values for a command argument, and a minimum time between
two commands. These are the rule archetypes described by JPL's
[FRESH](https://github.com/NASA-JPL-Teamtools-Studio/tts_fresh) flight rule checker, which
is where the idea comes from. Like FRESH, the check is static: it reads the commands,
constant arguments and constant waits out of the bytecode
([`gate/bytecode.py`](gate/bytecode.py), using the Fpy package's own deserializer) without
running the sequence. An argument computed while the sequence runs cannot be verified and
counts as a violation. A wait inside a branch or a loop is not trusted.

Because the bytecode is decoded with the dictionary of the target vehicle, this check also
notices a build mismatch on its own: the argument bytes of `Ref.SG1.Settings` compiled for
one build do not fit the other build's definition, and the check blocks with
`BYTECODE_UNREADABLE`.

### simulation

Static rules cannot say "do not send command X while the vehicle is in state Y". The
simulation check predicts a few modeled state variables: their starting values come from the
vehicle's latest telemetry, each modeled command applies a declared effect, and constraints
are checked against the predicted state just before each command. The model is a short YAML
file ([`rules/demo_model.yaml`](rules/demo_model.yaml), format in
[`gate/simulation.py`](gate/simulation.py)).

The demo model tracks whether signal generator 1 is running. `SignalGen` has no channel that
says so, but it only sends `Ref.SG1.Output` while it runs, so a recent `Output` reading
means it is running. `Ref.SG1.Toggle` flips it, and `Ref.SG1.Settings` may only be sent
while it is stopped. `signal_setup` passes on a vehicle whose generator is stopped; right
after it has run, the same kind of sequence is blocked, because the generator is now
running and the toggle would switch it off instead of on.

It assumes every command succeeds, commands run in file order and nothing else changes the
modeled state. A sequence that loops or branches on telemetry is reported as unpredictable.
Unknown starting state counts as a violation.

## What is in this repository

```
gate/        the gate core, a plain Python library with no web dependency
cli/         the `gate` command
api/         the gate service (FastAPI): REST, WebSocket, compile workers
web/         the 3D UI (Vite, React, TypeScript, react-three-fiber)
vehicles/    Dockerfile and patches that build the two demo builds of Ref
sequences/   example Fpy sequences
rules/       example flight rules and the simulation model
ops/         Grafana provisioning and dashboard
scripts/     reflash.sh (the live swap), check.sh (lint, types, tests), publish.sh
tests/       pytest suite; dictionaries under tests/fixtures are from real Ref builds
docs/        demo script and design notes
```

## Tests

```console
$ ./scripts/check.sh                 # ruff, mypy, 170+ unit tests; no network, no Docker
$ (cd web && npm ci && npm test)     # orbit propagator and UI state helpers
$ docker compose up --build -d && uv run pytest -m integration   # against the live vehicles
```

The integration tests compile, check and uplink through the HTTP API and watch the real
flight software run the sequence; they cover the block on a mismatched vehicle, the flight
rule and simulation blocks, concurrent compiles, and the live swap. CI (GitHub Actions) runs
lint, types and unit tests on every push, and the integration tests on pushes to `main`.

## Status

- [x] Phase 1: gate core and CLI
- [x] Phase 2: real vehicles in Docker, telemetry ingest, API, uplink
- [x] Phase 3: testbed equivalence and flight rule checks
- [x] Phase 4: 3D web UI
- [x] Phase 5: simulation check

[`docs/design.md`](docs/design.md) lists the design decisions and the known limits.

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
