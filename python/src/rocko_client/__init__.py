"""Async client for the control API of rocko, the memory-safe media daemon for IP cameras.

`RockoClient` speaks the API over the daemon's Unix socket; the payload types
are in `rocko_client.models`, generated with the command methods from the
daemon's JSON Schema.
"""

from rocko_client.client import RockoClient
from rocko_client.errors import (
    ApiVersionError,
    CannotConnectError,
    CommandError,
    ConnectionClosedError,
    ProtocolError,
    RockoError,
)
from rocko_client.subscription import Subscription
from rocko_client.version import API_VERSION

__all__ = [
    "API_VERSION",
    "ApiVersionError",
    "CannotConnectError",
    "CommandError",
    "ConnectionClosedError",
    "ProtocolError",
    "RockoClient",
    "RockoError",
    "Subscription",
]
