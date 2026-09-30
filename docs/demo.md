# Demo script (2 to 3 minutes)

What the demo shows: three live vehicles, compiles for all of them at once, a live build swap
that the gate notices from telemetry alone, and a BLOCK that stops a wrong sequence before it
leaves the ground.

## Before you start

```console
$ docker compose down -v                                  # start from empty state
$ GATE_COMPILE_PACING_S=1.5 docker compose up --build -d  # pacing makes the compiles visibly overlap
```

Open http://localhost:8000 and wait until all three vehicles in the top-right list show a
project version (about 15 seconds). Keep a terminal open in the repository.

## Steps

1. **Three vehicles, identified from telemetry.** Point at the list: `testbed` and `sc1` run
   `gate-demo-1.0.0`, `sc2` runs `gate-demo-1.1.0`. Select `sc1`. The console says which
   dictionary hash it runs, "matched by content" (the flight software reports a hash of its
   own dictionary), and how fresh that reading is.

2. **Compile for all vehicles.** Choose `signal_setup` and press "Compile for all vehicles".
   Three jobs appear and run at the same time in different worker processes. `sc2`'s job
   uses a different dictionary, because `sc2` runs a different build.

3. **A BLOCK: wrong vehicle.** With `sc2` selected, press Check on the bytecode compiled for
   `sc1`. Every check reports separately: build match blocks (compiled for a different
   dictionary), flight rules block too (the arguments do not decode with `sc2`'s dictionary),
   and there is no testbed run on `sc2`'s build. Press Uplink: refused, nothing is sent.

4. **Testbed first.** Select `testbed`, press Check then Uplink on the same bytecode. It runs
   on the testbed's flight software; the console shows the sequencer's events. The gate
   records that run as testbed evidence.

5. **Uplink to sc1.** Select `sc1`, press Check (all four checks pass), then Uplink. The
   green line from the ground to `sc1` marks the uplink; the events show the sequence ran.

6. **Live swap.** In the terminal:

   ```console
   $ ./scripts/reflash.sh sc1 B
   ```

   Within about ten seconds `sc1` reports `gate-demo-1.1.0`. Its earlier PASS is shown as
   history ("It has changed build since, so check again"). Press Check on the same bytecode:
   BLOCK, dictionary mismatch. Nothing was restarted on the ground.

7. **State matters too (optional).** Select `testbed` again and Check a fresh `signal_setup`
   compile. The simulation check now blocks: signal generator 1 is running since step 4, and
   this sequence may only change its settings while it is stopped.

To reset: `./scripts/reflash.sh sc1 A`, or `docker compose down -v`.
