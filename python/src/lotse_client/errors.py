"""The exceptions the client raises."""

from typing import Any


class LotseError(Exception):
    """Base class of every error of this client."""


class CannotConnectError(LotseError):
    """The control socket could not be opened, or the WebSocket upgrade failed."""


class ConnectionClosedError(LotseError):
    """The connection closed, or was never open, while a command or subscription needed it."""


class ApiVersionError(LotseError):
    """The daemon speaks an API version this client cannot use (`version.accepts`)."""

    def __init__(self, daemon: str, client: str) -> None:
        """Record both versions."""
        super().__init__(f"daemon speaks API version {daemon}, this client version {client}")
        self.daemon = daemon
        self.client = client


class ProtocolError(LotseError):
    """The daemon sent something the contract does not allow."""


class CommandError(LotseError):
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
