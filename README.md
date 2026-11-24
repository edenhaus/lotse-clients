# rocko clients

Client libraries for the control API of
[rocko](https://github.com/edenhaus/rocko), the memory-safe media daemon
for IP cameras.

| Language | Package | Source |
|---|---|---|
| Python (asyncio) | [`rocko-client`](https://pypi.org/project/rocko-client/) | [`python/`](python/) |

Each package's README shows how to install and use it.

## Versioning

A client has versions of its own, picked by its changes: a breaking change
raises the minor version while it is 0.x, a feature the minor, a fix the
patch. Each release's notes name the
[rocko release](https://github.com/edenhaus/rocko/releases) whose API it
speaks, and a client is only released for an API a published rocko release
has. The repository keeps the version 0.0.0; only a release build sets it.

## How the clients are generated

Every client's models and command methods are generated from
[`schema/api.json`](schema/api.json), the JSON Schema bundle that rocko
generates from its Rust types
([`crates/rocko-api-types/schema/api.json`](https://github.com/edenhaus/rocko/blob/main/crates/rocko-api-types/schema/api.json)),
taken at the rocko commit in [`schema/rocko-commit`](schema/rocko-commit).
When the schema changes on rocko's `main`,
[`sync-schema`](.github/workflows/sync-schema.yml) opens a PR here with the
new bundle and the regenerated code, and CI runs the contract tests against
a daemon built at that commit.

## Development

```sh
mise install                 # uv, prek and the git hooks
mise run check               # every hook on all files, tests included
mise run schema [../rocko]   # sync schema/ with a rocko checkout, regenerate the clients
mise run contract [../rocko] # the contract tests against a daemon built from that checkout
mise run dist [out]          # the Python client's sdist and wheel, checked with twine
```

## Releasing

A release is publishing the draft that Release Drafter keeps on GitHub
(`ci.yml`'s release-notes job, `.github/release-drafter.yml`): the merged
PRs' Conventional Commit titles (`pr-title.yml`) sort its notes and pick the
version, and publishing it tags `main` with that version, `X.Y.Z`, and starts
[`release`](.github/workflows/release.yml). That run sets the version from
the tag (`mise run set-version X.Y.Z`), checks that `schema/api.json` is the
API bundle of a published rocko release, builds the sdist and the wheel,
publishes them to PyPI with Trusted Publishing, and attaches them to the
release with the rocko release in its notes. Never bump the version in the
repository and never tag by hand. Verify a file's provenance with
`gh attestation verify <file> --repo edenhaus/rocko-clients`.

## License

MIT or Apache-2.0, at your option.
