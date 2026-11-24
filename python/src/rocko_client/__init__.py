"""Async client for the control API of rocko, the memory-safe media daemon for IP cameras.

`RockoClient` speaks the API over the daemon's Unix socket, or with
`RockoClient.remote` over mutual TLS to a daemon elsewhere, which `pair`
enrolls this client with once; the payload types are in
`rocko_client.models`, generated with the command methods from the daemon's
JSON Schema.
"""

from rocko_client.client import RockoClient
from rocko_client.errors import (
    ApiVersionError,
    CannotConnectError,
    CommandError,
    ConnectionClosedError,
    InvalidPairingCodeError,
    PairingError,
    PairingExpiredError,
    PairingRejectedError,
    ProtocolError,
    RockoError,
    ServerCertificateError,
    ServerPinMismatchError,
)
from rocko_client.subscription import Subscription
from rocko_client.tls import (
    ClientIdentity,
    PairingCode,
    certificate_pin,
    client_ssl_context,
    generate_identity,
    pair,
)
from rocko_client.version import API_VERSION

__all__ = [
    "API_VERSION",
    "ApiVersionError",
    "CannotConnectError",
    "ClientIdentity",
    "CommandError",
    "ConnectionClosedError",
    "InvalidPairingCodeError",
    "PairingCode",
    "PairingError",
    "PairingExpiredError",
    "PairingRejectedError",
    "ProtocolError",
    "RockoClient",
    "RockoError",
    "ServerCertificateError",
    "ServerPinMismatchError",
    "Subscription",
    "certificate_pin",
    "client_ssl_context",
    "generate_identity",
    "pair",
]
