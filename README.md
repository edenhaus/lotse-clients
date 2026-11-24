# rocko clients

Client libraries for the control API of
[rocko](https://github.com/edenhaus/rocko), the memory-safe media daemon
for IP cameras.

| Language | Package | Source |
|---|---|---|
| Python (asyncio) | [`rocko-client`](https://pypi.org/project/rocko-client/) | [`python/`](python/) |

Each package's README shows how to install and use it.

## Versioning

A client's version is the [rocko release](https://github.com/edenhaus/rocko/releases)
whose API it speaks: `rocko-client` 0.1.0 is the client of rocko 0.1.0, so
pin the daemon and the client together. Until rocko's first release the
version is 0.0.0 and nothing is published.

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

Setting the version in `python/pyproject.toml` on `main` releases it, once
`schema/api.json` is the bundle at rocko's tag `X.Y.Z`.
[`release`](.github/workflows/release.yml) builds the sdist and the wheel,
publishes them to PyPI with Trusted Publishing, and tags the commit
`X.Y.Z` with a GitHub release of that title and the same files. Verify a
file's provenance with
`gh attestation verify <file> --repo edenhaus/rocko-clients`.

## License

MIT or Apache-2.0, at your option.
