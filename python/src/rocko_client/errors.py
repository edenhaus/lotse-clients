"""The exceptions the client raises."""

from typing import Any


class RockoError(Exception):
    """Base class of every error of this client."""


class CannotConnectError(RockoError):
    """The daemon could not be reached (its socket, or its TLS listener), or the upgrade failed."""


class ConnectionClosedError(RockoError):
    """The connection closed, or was never open, while a command or subscription needed it."""


class ApiVersionError(RockoError):
    """The daemon speaks an API version this client cannot use (`version.accepts`)."""

    def __init__(self, daemon: str, client: str) -> None:
        """Record both versions."""
        super().__init__(f"daemon speaks API version {daemon}, this client version {client}")
        self.daemon = daemon
        self.client = client


class ProtocolError(RockoError):
    """The daemon sent something the contract does not allow."""


class CommandError(RockoError):
    """A command's `result` was a failure.

    `code` is the stable error code. It is read from the frame as a string
    rather than decoded into the generated `ErrorCode`, so a code newer than
    this client still arrives.
    """

    def __init__(self, code: str, message: str, details: dict[str, Any]) -> None:
        """Keep the daemon's error."""
        super().__init__(f"{code}: {message}")
        self.code = code
        """The stable error code."""
        self.message = message
        """For humans; it may change between versions."""
        self.details = details
        """Structured detail, per code; empty when there is none."""


class InvalidPairingCodeError(RockoError):
    """A pairing code is not `ROCKO1-<64 hex digits>-<32 hex digits>`."""


class ServerPinMismatchError(RockoError):
    """The daemon's certificate is not the one the pairing code names; the secret was not sent.

    Another server answers at that address, or the code is another daemon's.
    """


class PairingError(RockoError):
    """Pairing failed: the daemon did not enroll this client."""


class PairingRejectedError(PairingError):
    """The daemon refused the pairing code's secret (HTTP 403); the code stays open."""


class PairingExpiredError(PairingError):
    """No pairing code is open on the daemon: it expired, was used or was replaced (HTTP 410).

    Also raised when the daemon refuses the client's certificate in the
    handshake, which it does to an unknown client while no code is open.
    """


class ServerCertificateError(PairingError):
    """The daemon's certificate lacks the DNS name `rocko`, which the client verifies.

    A certificate made by an older daemon has none; `rocko identity
    --reissue` replaces it with one that has, and the client pairs again.
    """
