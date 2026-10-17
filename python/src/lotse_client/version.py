"""The API version this client speaks, and which daemon versions it accepts.

The API follows Semantic Versioning 2.0.0, separately from the daemon's own
version. Until lotse
1.0 its major version is 0, and as Cargo reads a 0.x version, a minor bump
is breaking and a patch bump additive. This mirrors
`lotse_api_types::version` in the Rust code.
"""

import re

API_VERSION = "0.3.0"
"""The bundle's `api`, which `scripts/generate_commands.py` writes here with the command methods."""

_SEMVER = re.compile(r"(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)")
"""`MAJOR.MINOR.PATCH` without leading zeros (Semantic Versioning 2.0.0 §2)."""


def parse(version: str) -> tuple[int, int, int] | None:
    """`MAJOR.MINOR.PATCH` as three integers; `None` for anything else."""
    match = _SEMVER.fullmatch(version)
    if match is None:
        return None
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def accepts(daemon: str, client: str = API_VERSION) -> bool:
    """Whether a client built for `client` can use a daemon speaking `daemon`.

    The same major, the same minor while the major is 0 (a 0.x minor is
    breaking), and the daemon not older, so every command and field the
    client knows exists.
    """
    ours, theirs = parse(client), parse(daemon)
    if ours is None or theirs is None:
        return False
    same_range = theirs[0] == ours[0] and (ours[0] != 0 or theirs[1] == ours[1])
    return same_range and theirs >= ours


WS_PATH = f"/v{API_VERSION.split('.', 1)[0]}/ws"
"""The WebSocket path, which carries the major version."""
