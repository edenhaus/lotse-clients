"""The API version: Semantic Versioning, and which daemons a client accepts."""

import pytest

from lotse_client.version import API_VERSION, WS_PATH, accepts, parse


def test_the_client_starts_at_0_1_0_on_the_v0_path() -> None:
    assert API_VERSION == "0.1.0", "unstable until lotse 1.0"
    assert WS_PATH == "/v0/ws"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "1",
        "1.2",
        "1.2.3.4",
        "01.2.3",
        "1.02.3",
        "1.2.03",
        "1.2.x",
        "1..3",
        "1.2.3-rc.1",
        " 1.2.3",
    ],
)
def test_only_semver_section_2_versions_parse(text: str) -> None:
    assert parse(text) is None


def test_versions_parse_into_their_numbers() -> None:
    assert parse("0.1.0") == (0, 1, 0)
    assert parse("10.20.30") == (10, 20, 30)


@pytest.mark.parametrize(
    ("daemon", "client", "accepted"),
    [
        ("0.1.2", "0.1.2", True),
        ("0.1.9", "0.1.2", True),  # a later patch only adds
        ("0.1.1", "0.1.2", False),  # an older daemon lacks what the client knows
        ("0.2.2", "0.1.2", False),  # a 0.x minor is breaking
        ("1.1.2", "0.1.2", False),
        ("1.5.0", "1.2.3", True),  # a later minor only adds
        ("1.2.2", "1.2.3", False),
        ("2.2.3", "1.2.3", False),
        ("0.1", "0.1.0", False),
        ("0.1.0", "x", False),
    ],
)
def test_a_client_accepts_a_compatible_daemon_that_is_not_older(
    daemon: str, client: str, *, accepted: bool
) -> None:
    assert accepts(daemon, client) is accepted
