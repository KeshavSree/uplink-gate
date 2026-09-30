# Demo vehicles

Each demo vehicle is a container that runs one build of F Prime's reference deployment
(`Ref`) and a headless ground data system (`fprime-gds`). A build is one compiled version of
the flight software; the dictionary is the JSON file that build produces.

## What is built

| Build | Project version | Source | Dictionary hash (SHA-256) |
|---|---|---|---|
| A | `gate-demo-1.0.0` | F Prime `v4.3.0` + `build_variants/base.patch` | `4bbb2268b356ad9e…` |
| B | `gate-demo-1.1.0` | build A + `build_variants/variant_b.patch` | `d6c09e7f3db838a9…` |

`base.patch` changes only the `Ref` deployment under `TestDeploymentsProject/`:

- adds an `Svc.FpySequencer` instance, `Ref.fpySeq`, so the deployment can run Fpy bytecode
  (`Ref` at `v4.3.0` has none);
- adds a small passive component, `Ref.DictionaryId`, that reports the dictionary ID as
  custom version 0 of `Svc.Version`. It shows up in the `CdhCore.version.CustomVersion01`
  telemetry channel as `dict-sha256:<first 16 hex chars of the dictionary hash>`.

`variant_b.patch` changes the type of the `Frequency` argument of the `SignalGen` command
`Settings` from `U32` to `U16`.

Nothing in the F Prime framework itself is modified.

## Exact build steps

`Dockerfile` does this:

1. `git clone --depth 1 --branch v4.3.0 https://github.com/nasa/fprime.git`
2. `pip install -r fprime/requirements.txt` (pins `fprime-tools`, `fprime-fpp`, …)
3. `build_variants/build.sh A gate-demo-1.0.0 base.patch`
4. `build_variants/build.sh B gate-demo-1.1.0 variant_b.patch`

`build.sh` applies the patch, commits it and tags the commit, so that
`git describe --tags --always --dirty --broken` (which F Prime stamps into the build as the
project version) returns exactly the tag. It then runs `fprime-util generate` and
`fprime-util build` in `TestDeploymentsProject/`.

The build runs in two passes. The first pass produces the dictionary. The script writes the
dictionary's SHA-256 into `Ref/DictionaryId/DictionaryIdValue.hpp` and builds again, which
compiles that value into the flight software. The dictionary is generated from the FPP model
only, so the second pass must not change it, and the script fails if it does.

Two things found while writing this:

- A `Release` build of `v4.3.0` fails with GCC 13 on a `-Werror=stringop-overflow` warning in
  `Os/Generic/PriorityMemQueue.cpp`, so the default build type is used.
- F Prime generates its version files once and does not refresh them on an incremental
  build. `build.sh` deletes them before every build. Without that, build B carried build A's
  project version.

## Running one by hand

```console
$ docker build -t uplink-gate-vehicle vehicles/
$ docker run --rm -e VEHICLE_BUILD=A -p 50050:50050 uplink-gate-vehicle
$ docker exec <container> reflash B        # switch the flight software to build B
```

Port 50050 is the GDS's TCP server, the same one `fprime-gds` tools connect to.

## Getting just the dictionaries

```console
$ docker build --target dictionaries --output type=local,dest=out vehicles/
```

This is how `tests/fixtures/dictionaries/A` and `B` were produced.
