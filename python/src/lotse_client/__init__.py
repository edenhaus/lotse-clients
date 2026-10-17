"""Async client for the control API of lotse, the memory-safe media daemon for IP cameras.

`LotseClient` speaks the API over the daemon's Unix socket; the payload types
are in `lotse_client.models`, generated with the command methods from the
daemon's JSON Schema.
"""

from lotse_client.client import LotseClient
from lotse_client.errors import (
    ApiVersionError,
    CannotConnectError,
    CommandError,
    ConnectionClosedError,
    LotseError,
    ProtocolError,
)
from lotse_client.subscription import Subscription
from lotse_client.version import API_VERSION

__all__ = [
    "API_VERSION",
    "ApiVersionError",
    "CannotConnectError",
    "CommandError",
    "ConnectionClosedError",
    "LotseClient",
    "LotseError",
    "ProtocolError",
    "Subscription",
]
