# CodeMotion

The full specification is in `SPEC.md` (v2: Python video scripts + PySide6 desktop app); read it before changing anything. v1 (code showcase, browser UI) is archived in `docs/SPEC-v1-code-showcase.md`. Build one milestone
at a time (§14) and follow the rules in §15. Recorded decisions and deviations are in
`docs/decisions.md`.

- Check command: `uv run ruff check . && uv run ruff format --check . && uv run mypy src && uv run pytest`
- `mypy --strict` must pass on `src/`. Every new module gets tests in the same milestone.
- Stages after the script run are pure; only the script runner, encoder and desktop UI have side effects.
- Qt is imported only under `src/codemotion/desktop/` (the CLI loads it lazily for `codemotion app`). Desktop tests run on offscreen Qt (`tests/test_desktop.py`).
- User scripts never run in-process: always via `script/runner.py` (child process + timeout).
- No new dependencies, CDNs or runtime network calls without asking.
- User-visible errors go through `UserFacingError` with plain-English messages.
- Design values and limits live in `config.py`, not in scene code.
- After an intended visual change, run `uv run pytest --update-golden` and review the PNGs.
- Packaged app: `uv run python packaging/build.py` (Windows; builds, smoke-tests, zips).
  Code that starts processes must work frozen: see `launcher.py` and `runner.child_command`.
- Performance: `uv run codemotion bench` (SPEC section 10 targets).
