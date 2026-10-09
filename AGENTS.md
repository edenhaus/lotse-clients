# AGENTS.md

Client libraries for the lotse control API, one directory per language. The
daemon and the API's Rust types live in
[lotse](https://github.com/edenhaus/lotse); this repository only consumes
the API.

## Commands

Every check is a prek hook in `.pre-commit-config.yaml`; `mise run check`
runs all of them.

| Command | What it does |
|---|---|
| `mise run check` | every hook on all files, tests included (CI) |
| `mise run schema [lotse]` | sync `schema/` with a lotse checkout's committed bundle (default `../lotse`) and regenerate every client's models and command methods |
| `mise run contract [lotse]` | build a daemon with the fake source from a lotse checkout and run the contract tests against it |
| `mise run dist [out]` | the Python client's sdist and wheel (`uv build`), checked with `twine check --strict` |
| `cd python && uv run pytest` | the Python client's tests; `LOTSE_BIN=<lotse>/target/debug/lotse` adds the contract tests |

## Layout

- `schema/api.json`: the API's JSON Schema bundle, lotse's
  `crates/lotse-api-types/schema/api.json` at the commit in
  `schema/lotse-commit`. CI builds the daemon at that commit for the
  contract tests. Never edit either by hand: `sync-schema.yml` opens a PR
  when lotse's schema changes on `main` (lotse's `ci.yml` dispatches
  it), and `mise run schema` does the same from a local checkout.
- `python/`: the `lotse-client` package. Its models (`models.py`) are
  generated from the bundle by datamodel-code-generator, and its command
  methods (`_commands.py`) from the bundle and those models by
  `python/scripts/generate_commands.py`: a command `domain/verb` is
  `client.domain.verb(...)`, one without a slash a method of the client.
  Only the WebSocket transport is hand-written.

A new language gets its own directory, generates its models and methods
from `schema/api.json`, and adds its hooks to `.pre-commit-config.yaml` and
its regeneration to the `schema` task.

## Conventions

- An API change lands in lotse first; the sync PR then brings the bundle
  and every client's regenerated models and methods in one commit. Hooks
  fail on stale output. When the change needs hand-written code (a new
  kind of frame in the transport), push it to the sync PR's branch; the
  next sync replaces that branch, so merge first.
- Python: never edit `python/src/lotse_client/models.py` or `_commands.py`
  by hand; a method's shape follows from the bundle, so change the Rust
  types or the generator. Code passes `mypy --strict` and every ruff rule (the few exceptions and
  why are in `python/pyproject.toml`) with 100 % line and branch coverage;
  test names state the behavior. A ruff bump changes the hook's `rev` and
  the pin in `python/pyproject.toml` together.
- Python: the package's version is the lotse release whose bundle
  `schema/api.json` is (0.0.0 before the first). Changing it on `main` is
  the release: `release.yml` publishes to PyPI and tags
  `lotse-client-v<version>`; never tag by hand (README.md#releasing).
- Conventional Commits, enforced on `commit-msg`.
