# Brogue CE agent fork: agent instructions

## What this is

This is a fork of Brogue CE (`tmewett/BrogueCE`, AGPL-3.0) that lets programs play the game. The fork adds a headless **agent hook**: a platform console that writes JSON observations to stdout and reads actions from stdin. A standard-library Python client in `python/brogue_env/` drives it. It is the environment for the Active Inference Lab (`EclipsingLines/active-inference-lab`). The lab installs the client and never copies C code.

The game's rules must stay exactly upstream's. Phase 5 of the lab compares an agent's learned model of the game with the true rules, and that only works if the fork has not changed them.

## Architecture map

- `src/brogue/`: game logic (dungeon generation, turns, items, monsters). **Do not change it** except where a ticket names the exact line.
- `src/variants/`: Rapid and Bullet Brogue constants. Do not change.
- `src/platform/`: the seam between the game and the outside world.
  - `platform.h`: `struct brogueConsole` (`gameLoop`, `pauseForMilliseconds`, `nextKeyOrMouseEvent`, `plotChar`, `remap`, `modifierHeld`, `notifyEvent`, `takeScreenshot`, `setGraphicsMode`).
  - `null-platform.c`: a console that does nothing. Headless replays (`-vn file`) use it.
  - `main.c`: argument parsing and console selection.
  - `platformdependent.c`: shared helpers, for example `glyphToUnicode` and `notifyEvent`.
  - Planned: `agent-platform.c`, the agent hook console, selected with `--agent`.
- `src/brogue/IO.c:2390` `nextBrogueEvent`: the game calls the console's `nextKeyOrMouseEvent` every time it waits for input. That is the hook's observation point.
- `external/cJSON/`: vendored cJSON, already linked into every build. The hook uses it for JSON.
- `python/` (planned): `brogue_env/` (the client package), `pyproject.toml`, `test_*.py`.
- `test/seed_catalogs/`: upstream's dungeon-generation catalogs. The verify command compares the build against them.

## Build and verify (Windows, Git Bash)

The toolchain is MinGW-w64 GCC from WinLibs (`winget install BrechtSanders.WinLibs.POSIX.UCRT`), which provides `gcc`, `mingw32-make` and `windres`. There is no SDL: always build with `GRAPHICS=NO`.

```bash
mingw32-make -j4 SYSTEM=WINDOWS GRAPHICS=NO TERMINAL=NO CC=gcc bin/brogue.exe && out="$(mktemp)" && (cd bin && ./brogue.exe --print-seed-catalog 1 25 40) > "$out" && diff --strip-trailing-cr <(sed 1,4d test/seed_catalogs/seed_catalog_brogue.txt) <(sed 1,4d "$out") && rm -f "$out" && { test ! -d python || py -m unittest discover -s python -p "test_*.py"; }
```

Run it from the repo root. A seed-catalog diff means game logic changed: that is a failure, never a reason to update the catalog.

## Rules

- C99 that compiles cleanly under the Makefile's flags (`-Wall -Wpedantic -Werror=implicit -Wmissing-prototypes`). A function the hook keeps to itself is `static`.
- New C code goes in `src/platform/`. Registering it is a one-line change in `main.c`, `platform.h` or the `Makefile`.
- Everything on the hook's protocol channel goes through one function that prefixes `@@BROGUE `, writes one JSON object and a newline, and flushes. The game's own `printf` noise is left alone; the client skips lines without the prefix.
- Python: 3.11+ syntax, standard library only, `unittest`, tests next to the code (`python/test_*.py`). Tests start the real binary in a fresh temporary working directory and assert behaviour (determinism, turn counting, nutrition), never timing. A read timeout only guards against a hang.
- Tests and runs are seeded: always pass `--seed`.
- Never commit build output (`*.o`, `bin/brogue*`), recordings, saves or high-score files. `.gitignore` covers them.
- Do not enable or edit `.github/workflows/`: they target GitHub-hosted runners that this account cannot use.
