# Design notes

## The core is a library

`gate/` has no web dependency. The CLI (`cli/`), the service (`api/`) and the UI (through
the service) all call the same functions: `gate.engine.evaluate` makes every decision and
`gate.uplink.UplinkAdapter` is the only code that sends bytecode. The service adds HTTP,
a WebSocket, telemetry ingest threads and a compile process pool around them.

## Identity: content, not labels

A project version is whatever `git describe` said when the build's version files were
generated. The demo build uncovered that F Prime v4.3.0 does not regenerate them on an
incremental build, so a label can describe a different build than the one it is attached to.
The gate therefore prefers a dictionary ID (a prefix of the dictionary's SHA-256) reported by
the flight software, and uses the label only as a cross-check or a fallback. 16 hex characters
(64 bits) are enough to tell dictionaries apart in one registry; the gate blocks if a prefix
ever matches two.

The compile side works the same way: the compile record is keyed by the bytecode's SHA-256.
Identical bytes compiled with two dictionaries (a sequence that touches nothing that
differs) have a record for each, and pass on a vehicle running either.

## Bootstrapping telemetry decoding

To read a vehicle's identity the gate has to decode its telemetry, which needs a dictionary.
Before the identity is known the ingest decodes with any registered dictionary, then switches
to the one the vehicle resolves to (and switches again after a reflash). This relies on one
contract between builds: the version channels keep their IDs and types. That contract is
far smaller than "the whole dictionary matches", but it is a contract, and a project that
adopts this gate should state it.

fprime-gds keeps type widths (for example `FwSizeStoreType`) in one process-wide
`ConfigManager`. All demo builds agree on them. Vehicles whose builds disagree would need one
ingest process per configuration.

## Failure is a BLOCK

Every path that cannot establish a fact blocks: no reading, a stale reading, an unknown or
ambiguous build, a missing compile record, an argument that cannot be decoded, a rule that
needs a value computed at run time, a sequence the simulation cannot predict, a check that
raises. The uplink adapter re-checks immediately before sending and refuses if the vehicle
does not confirm its identity within a few seconds.

## Known limits

- No authentication, and sidecar files can be forged. The gate guards against mistakes.
- Testbed runs are recorded from the sequencer's events and tied to bytecode by the file
  name the adapter uses (`<sha prefix>.bin`). A file uploaded under that name by other means
  would be trusted.
- The flight rule check and the simulation read the bytecode statically in directive order.
  Waits inside branches are not counted, loops make timing and state unprovable, and both
  are reported as violations rather than guessed.
- The simulation assumes every command succeeds and nothing else changes the modeled state.
- The orbits in the UI are made-up Keplerian elements, not ephemeris, and not to scale.
- The demo registry stores dictionaries by hash in `GATE_HOME`; a real deployment would get
  them from the build system rather than from the vehicle image.

## Possible next steps

- Put the dictionary hash into the Fpy bytecode header itself (an upstream Fpy change), so
  the flight software could refuse mismatched bytecode on board as well.
- A per-configuration ingest process instead of the shared `ConfigManager`.
- A Django version of the service, if a team standardises on it; `api/` is a thin layer.
