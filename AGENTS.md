# AGENTS.md

Client libraries for the rocko control API, one directory per language. The
daemon and the API's Rust types live in
[rocko](https://github.com/edenhaus/rocko); this repository only consumes
the API.

## Commands

Every check is a prek hook in `.pre-commit-config.yaml`; `mise run check`
runs all of them. The zizmor hook runs offline; on `main` and weekly, CI's
zizmor job runs the same pinned zizmor with its online audits and sends the
findings to code scanning.

| Command | What it does |
|---|---|
| `mise run check` | every hook on all files, tests included (CI) |
| `mise run schema [rocko]` | sync `schema/` with a rocko checkout's committed bundle (default `../rocko`) and regenerate every client's models and command methods |
| `mise run contract [rocko]` | build a daemon with the fake source from a rocko checkout and run the contract tests against it |
| `mise run dist [out]` | the Python client's sdist and wheel (`uv build`), checked with `twine check --strict` |
| `cd python && uv run pytest` | the Python client's tests; `ROCKO_BIN=<rocko>/target/debug/rocko` adds the contract tests |

## Layout

- `schema/api.json`: the API's JSON Schema bundle, rocko's
  `crates/rocko-api-types/schema/api.json` at the commit in
  `schema/rocko-commit`. CI builds the daemon at that commit for the
  contract tests. Never edit either by hand: `sync-schema.yml` opens a PR
  when rocko's schema changes on `main` (rocko's `ci.yml` dispatches
  it), and `mise run schema` does the same from a local checkout.
- `python/`: the `rocko-client` package. Its models (`models.py`) are
  generated from the bundle by datamodel-code-generator, and its command
  methods (`_commands.py`) from the bundle and those models by
  `python/scripts/generate_commands.py`: a command `domain/verb` is
  `client.domain.verb(...)`, one without a slash a method of the client.
  The same script keeps `API_VERSION` in `version.py` at the bundle's `api`.
  Only the WebSocket transport is hand-written.

A new language gets its own directory, generates its models and methods
from `schema/api.json`, and adds its hooks to `.pre-commit-config.yaml` and
its regeneration to the `schema` task.

## Conventions

- An API change lands in rocko first; the sync PR then brings the bundle
  and every client's regenerated models and methods in one commit. Hooks
  fail on stale output. When the change needs hand-written code (a new
  kind of frame in the transport), push it to the sync PR's branch; the
  next sync replaces that branch, so merge first.
- Python: never edit `python/src/rocko_client/models.py`, `_commands.py`
  or `API_VERSION` by hand; a method's shape follows from the bundle, so
  change the Rust types or the generator. Code passes `mypy --strict` and every ruff rule (the few exceptions and
  why are in `python/pyproject.toml`) with 100 % line and branch coverage;
  test names state the behavior. A ruff bump changes the hook's `rev` and
  the pin in `python/pyproject.toml` together.
- Python: the package's version is the rocko release whose bundle
  `schema/api.json` is (0.0.0 before the first). Changing it on `main` is
  the release: `release.yml` publishes to PyPI and tags
  `<version>`; never tag by hand (README.md#releasing).
- Conventional Commits, enforced on `commit-msg`.
