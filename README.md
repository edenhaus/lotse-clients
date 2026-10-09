# lotse clients

Client libraries for the control API of
[lotse](https://github.com/edenhaus/lotse), the media daemon for RTSP
cameras and WebRTC viewers. One directory per language:

| Directory | Package | Used by |
|---|---|---|
| [`python/`](python/) | `lotse-client` (PyPI) | any Python program, async (asyncio) |

Every client's models and command methods are generated from the same JSON Schema bundle,
[`schema/api.json`](schema/api.json): lotse's
`crates/lotse-api-types/schema/api.json`, which lotse generates from its Rust
types, at the lotse commit in [`schema/lotse-commit`](schema/lotse-commit).
Nobody copies it by hand: a push to lotse's `main` that changes the schema
dispatches [`sync-schema`](.github/workflows/sync-schema.yml) here, which
opens a PR with the new schema and the regenerated code, or updates the
open one. CI builds the daemon at the pinned commit and runs the contract
tests against it.

## Development

```sh
mise install                # uv, prek and the git hooks
mise run check              # every hook on all files, tests included
mise run schema [../lotse]   # sync schema/ with a lotse checkout's commit, regenerate the clients
mise run contract [../lotse] # the contract tests against a daemon built from that checkout
mise run dist [out]          # the Python client's sdist and wheel, checked with twine
```

## Releasing

The Python client's version is the lotse release whose API bundle
`schema/api.json` is: `lotse-client` 0.1.0 is the client of lotse 0.1.0,
so a deployment pins the daemon and the client together.
It stays 0.0.0 until lotse's first release. To release the client of lotse
`X.Y.Z`:

1. Sync the schema from lotse's tag: run `sync-schema` with ref `vX.Y.Z`
   (or `mise run schema` in a checkout at that tag) and merge its PR, if
   `main` does not match that release already.
2. Set `version = "X.Y.Z"` in `python/pyproject.toml`, run `uv lock` in
   `python/`, and merge that as `chore(release): lotse-client X.Y.Z`.

The [`release`](.github/workflows/release.yml) workflow runs on every push
to `main` and releases a version that has no `lotse-client-vX.Y.Z` tag yet.
It checks that `schema/api.json` is the bundle at lotse's `vX.Y.Z`, builds
the sdist and the wheel (`mise run dist`), attests their provenance,
uploads them to PyPI with Trusted Publishing, and then tags the commit and
creates a GitHub release with the same files. No one tags by hand. A
failed run is resumed with "Re-run failed jobs". Check a file with
`gh attestation verify <file> --repo edenhaus/lotse-clients`.

One-time setup before the first release:

1. On PyPI, a pending trusted publisher for the project `lotse-client`
   (Account settings, Publishing): owner `edenhaus`, repository
   `lotse-clients`, workflow `release.yml`, environment `pypi`. The first
   upload creates the project; add the other maintainers to it then.
2. On GitHub, the environment `pypi` in this repository's settings, with
   deployment branches limited to `main` (and required reviewers, if a
   release should wait for a person's approval). The publish job is the
   only one that runs in it and the only one with an OIDC token.

## Automation setup

Both workflows act as one GitHub App, because `GITHUB_TOKEN` reaches only its
own repository and a PR it opens runs no CI. The app needs *Contents: write*
and *Pull requests: write*, installed on lotse-clients. Both repositories need
its client ID as the variable `LOTSE_BOT_CLIENT_ID` and its private key as the
secret `LOTSE_BOT_PRIVATE_KEY`.

## License

MIT or Apache-2.0, at your option.
