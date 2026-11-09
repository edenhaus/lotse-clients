# lotse clients

Client libraries for the control API of
[lotse](https://github.com/edenhaus/lotse), the memory-safe media daemon
for IP cameras.

| Language | Package | Source |
|---|---|---|
| Python (asyncio) | [`lotse-client`](https://pypi.org/project/lotse-client/) | [`python/`](python/) |

Each package's README shows how to install and use it.

## Versioning

A client's version is the [lotse release](https://github.com/edenhaus/lotse/releases)
whose API it speaks: `lotse-client` 0.1.0 is the client of lotse 0.1.0, so
pin the daemon and the client together. Until lotse's first release the
version is 0.0.0 and nothing is published.

## How the clients are generated

Every client's models and command methods are generated from
[`schema/api.json`](schema/api.json), the JSON Schema bundle that lotse
generates from its Rust types
([`crates/lotse-api-types/schema/api.json`](https://github.com/edenhaus/lotse/blob/main/crates/lotse-api-types/schema/api.json)),
taken at the lotse commit in [`schema/lotse-commit`](schema/lotse-commit).
When the schema changes on lotse's `main`,
[`sync-schema`](.github/workflows/sync-schema.yml) opens a PR here with the
new bundle and the regenerated code, and CI runs the contract tests against
a daemon built at that commit.

## Development

```sh
mise install                 # uv, prek and the git hooks
mise run check               # every hook on all files, tests included
mise run schema [../lotse]   # sync schema/ with a lotse checkout, regenerate the clients
mise run contract [../lotse] # the contract tests against a daemon built from that checkout
mise run dist [out]          # the Python client's sdist and wheel, checked with twine
```

## Releasing

Setting the version in `python/pyproject.toml` on `main` releases it, once
`schema/api.json` is the bundle at lotse's tag `vX.Y.Z`.
[`release`](.github/workflows/release.yml) builds the sdist and the wheel,
publishes them to PyPI with Trusted Publishing, and tags the commit
`lotse-client-vX.Y.Z` with a GitHub release of the same files. Verify a
file's provenance with
`gh attestation verify <file> --repo edenhaus/lotse-clients`.

## License

MIT or Apache-2.0, at your option.
