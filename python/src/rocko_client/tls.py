"""Mutual TLS to a remote daemon, and the one-time pairing that lets this client in.

A daemon started with a control listener (`ROCKO_CONTROL_LISTEN`) serves the
same WebSocket API over mutual TLS 1.3. Both ends present a certificate, and
each side trusts exactly the other's: the daemon admits a client by its
certificate's pin (the SHA-256 of its DER encoding), and the client verifies
the daemon against exactly the daemon's certificate, by the DNS name `rocko`
that certificate carries. The daemon ignores a client certificate's
validity, so `generate_identity` makes one that never expires, as the
daemon's own.

Pairing enrolls a client's pin without copying it by hand. `rocko pair` (or
`pairing/create` over the socket) prints a one-time code,
`ROCKO1-<server pin>-<secret>`: the daemon certificate's pin as 64 lowercase
hex digits and a 128-bit secret as 32. `pair` fetches the daemon's
certificate and compares it with the pin before the secret leaves this
process, then sends the secret in exactly one request, `POST /v0/pair`,
over a connection that verifies the daemon against that certificate and
presents this client's own. The daemon enrolls the pin of the certificate
the handshake proved and closes the connection; the client then connects
as an enrolled one (`RockoClient.remote`). A code lives 10 minutes, and
only the newest one works.

Python's `ssl` loads a certificate and its key only from files, so building
an `ssl.SSLContext` for an identity writes them to a private temporary file
and removes it as soon as OpenSSL has read it: blocking file I/O.
`client_ssl_context` is that blocking call; `pair` and `RockoClient` run it
in a worker thread, so nothing blocks an event loop.
"""

import asyncio
import contextlib
import datetime as dt
import hashlib
import hmac
import os
import re
import ssl
import tempfile
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Self

import aiohttp
import orjson
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from rocko_client.errors import (
    CannotConnectError,
    InvalidPairingCodeError,
    PairingError,
    PairingExpiredError,
    PairingRejectedError,
    ServerCertificateError,
    ServerPinMismatchError,
)
from rocko_client.version import API_VERSION

SERVER_NAME = "rocko"
"""The DNS name in the daemon's certificate, which the client verifies whatever host it dials."""

PAIR_PATH = f"/v{API_VERSION.split('.', 1)[0]}/pair"
"""The pairing request's path, which carries the API's major version as `WS_PATH` does."""

CODE_PREFIX = "ROCKO1"
"""A pairing code's first part, which names its format."""

_CODE = re.compile(rf"{CODE_PREFIX}-([0-9a-f]{{64}})-([0-9a-f]{{32}})")
"""A pairing code: the prefix, the server pin and the secret, both lowercase hex."""

_NOT_BEFORE = dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
"""A generated certificate's start, before any clock a device boots with, as the daemon's."""

_NOT_AFTER = dt.datetime(9999, 12, 31, 23, 59, 59, tzinfo=dt.UTC)
"""RFC 5280 §4.1.2.5: 99991231235959Z, "no well-defined expiration date", as the daemon's."""

_CERTIFICATE_UNKNOWN = "SSLV3_ALERT_CERTIFICATE_UNKNOWN"
"""OpenSSL's reason for the `certificate_unknown` alert (RFC 8446 §6.2), which the daemon
sends when it refuses a client certificate: neither enrolled nor let in by an open code."""


def certificate_pin(certificate: str) -> str:
    """The pin of a PEM certificate: the SHA-256 of its DER encoding, as 64 lowercase hex digits.

    It is what `openssl x509 -noout -fingerprint -sha256` prints, without
    the colons and in lowercase, and what the daemon calls a pin.
    """
    return hashlib.sha256(ssl.PEM_cert_to_DER_cert(certificate)).hexdigest()


@dataclass(frozen=True, kw_only=True)
class ClientIdentity:
    """This client's certificate and private key, both PEM, which it presents to the daemon.

    Keep both: the daemon enrolls the certificate's `pin` when pairing, and
    only the key's holder can use it. `repr` leaves the key out.
    """

    certificate: str
    """The self-signed certificate, PEM."""
    private_key: str = field(repr=False)
    """The PKCS #8 private key, PEM; a secret."""

    @property
    def pin(self) -> str:
        """The certificate's pin, which the daemon enrolls and lists (`clients/list`)."""
        return certificate_pin(self.certificate)


def generate_identity(common_name: str = "rocko-client") -> ClientIdentity:
    """A new identity: an ECDSA P-256 key and a self-signed certificate that never expires.

    The daemon accepts ECDSA P-256 and Ed25519 keys only, and never checks a
    client certificate's validity (the pin is the authority, and removing it
    revokes the client), so the certificate is valid from 2000 to 9999 as
    the daemon's own is. `common_name` only helps a human tell certificates
    apart. Generating a key takes a millisecond or so of CPU, no I/O.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_NOT_BEFORE)
        .not_valid_after(_NOT_AFTER)
        .sign(key, hashes.SHA256())
    )
    return ClientIdentity(
        certificate=certificate.public_bytes(serialization.Encoding.PEM).decode(),
        private_key=key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


@dataclass(frozen=True)
class PairingCode:
    """A pairing code, `ROCKO1-<server pin>-<secret>`, as `rocko pair` prints it.

    `repr` and `str` leave the secret out: it lets whoever holds it enroll a
    client until the code expires.
    """

    server_pin: str
    """The daemon certificate's pin, 64 lowercase hex digits."""
    secret: str = field(repr=False)
    """The one-time secret, 32 lowercase hex digits."""

    @classmethod
    def parse(cls, code: str) -> Self:
        """Read a code; whitespace around it, as a paste brings, is ignored.

        Raises:
            InvalidPairingCodeError: It is not `ROCKO1-` followed by 64 and
                32 lowercase hex digits, separated by `-`. The message never
                repeats the input, which may hold the secret.
        """
        match = _CODE.fullmatch(code.strip())
        if match is None:
            msg = f"not a pairing code: expected {CODE_PREFIX}-<64 hex digits>-<32 hex digits>"
            raise InvalidPairingCodeError(msg)
        return cls(server_pin=match[1], secret=match[2])


def client_ssl_context(identity: ClientIdentity, server_certificate: str) -> ssl.SSLContext:
    """A TLS 1.3 client context that presents `identity` and trusts only `server_certificate`.

    The daemon is verified against exactly its certificate (PEM, as `pair`
    returns it) and by the name `rocko`, so the connection must give
    `server_hostname="rocko"` whatever host it dials. A certificate made by
    an older daemon lacks that name and is refused; `rocko identity
    --reissue` replaces it (and the client must pair again).

    Blocking: it writes and removes a temporary file (see the module). Run
    it in an executor from an event loop.
    """
    return _context(identity, server_certificate)


def _context(identity: ClientIdentity, server_certificate: str | None) -> ssl.SSLContext:
    """`client_ssl_context`, or with `None` one that verifies nothing, to read a certificate."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # The daemon offers TLS 1.3 alone (rocko's crates/rocko-api/src/tls.rs).
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    if server_certificate is None:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context.load_verify_locations(cadata=server_certificate)
        # The self-signed certificate is the trust anchor itself, a chain of one; Python's own
        # `create_default_context` sets this flag too (from 3.13).
        context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
    _load_identity(context, identity)
    return context


def _load_identity(context: ssl.SSLContext, identity: ClientIdentity) -> None:
    """Load `identity` into `context` through a private temporary file, removed at once.

    `SSLContext.load_cert_chain` takes paths only (as of Python 3.14), so
    the key and certificate go to a file `mkstemp` creates with mode 0600
    and `O_EXCL`, which is unlinked as soon as OpenSSL has read it, whether
    that worked or not.
    """
    descriptor, path = tempfile.mkstemp(prefix="rocko-identity-", suffix=".pem")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(f"{identity.private_key.strip()}\n{identity.certificate.strip()}\n")
        context.load_cert_chain(path)
    finally:
        Path(path).unlink()


def authority(host: str, port: int) -> str:
    """`host:port` for a URL, with an IPv6 address in brackets (RFC 3986 §3.2.2)."""
    return f"[{host}]:{port}" if ":" in host else f"{host}:{port}"


async def pair(
    host: str,
    port: int,
    code: PairingCode | str,
    identity: ClientIdentity,
    *,
    session: aiohttp.ClientSession | None = None,
) -> str:
    """Enroll `identity` with the daemon at `host`:`port` and return the daemon's certificate, PEM.

    Keep the certificate with the identity: `RockoClient.remote` trusts
    exactly it. `host` is a name or an IP address (an IPv6 one without
    brackets). The request goes through `session` when given (any TCP
    session; the TLS settings are per request), else through one of its
    own. Nothing here times out on its own: wrap it in `asyncio.timeout`.

    Raises:
        InvalidPairingCodeError: `code` is a string that is not a code.
        CannotConnectError: `identity` is not a certificate and its key, the
            daemon cannot be reached, or the TLS connection failed.
        ServerPinMismatchError: The certificate at `host`:`port` is not the
            one the code names; the secret was not sent.
        ServerCertificateError: The certificate lacks the DNS name `rocko`;
            the secret was not sent.
        PairingRejectedError: The secret is wrong (HTTP 403).
        PairingExpiredError: No code is open: it expired, was used or was
            replaced by a newer one (HTTP 410, or the daemon refused the
            client's certificate in the handshake).
        PairingError: Any other answer.
    """
    if isinstance(code, str):
        code = PairingCode.parse(code)
    address = authority(host, port)
    try:
        probe = await asyncio.to_thread(_context, identity, None)
    except ssl.SSLError as err:
        msg = f"cannot load the client identity: {err}"
        raise CannotConnectError(msg) from err
    der = await _fetch_certificate(host, port, probe, address)
    if not hmac.compare_digest(hashlib.sha256(der).hexdigest(), code.server_pin):
        msg = f"the certificate at {address} is not the one the pairing code names"
        raise ServerPinMismatchError(msg)
    _check_server_name(der)
    certificate = ssl.DER_cert_to_PEM_cert(der)
    context = await asyncio.to_thread(_context, identity, certificate)
    url = f"https://{address}{PAIR_PATH}"
    own = session is None
    client = aiohttp.ClientSession() if session is None else session
    try:
        async with client.post(
            url, json={"secret": code.secret}, ssl=context, server_hostname=SERVER_NAME
        ) as response:
            status = response.status
            body = await response.read()
    except (aiohttp.ClientError, OSError) as err:
        if _refused_certificate(err):
            msg = "the daemon refused this client: no pairing code is open (it expired or was used)"
            raise PairingExpiredError(msg) from err
        msg = f"cannot pair with {address}: {err}"
        raise CannotConnectError(msg) from err
    finally:
        if own:
            await client.close()
    if status == HTTPStatus.NO_CONTENT:
        return certificate
    detail = _message_of(body)
    if status == HTTPStatus.FORBIDDEN:
        msg = f"the daemon refused the pairing secret: {detail}"
        raise PairingRejectedError(msg)
    if status == HTTPStatus.GONE:
        msg = f"no pairing code is open on the daemon: {detail}"
        raise PairingExpiredError(msg)
    msg = f"pairing failed with HTTP {status}: {detail}"
    raise PairingError(msg)


async def _fetch_certificate(host: str, port: int, context: ssl.SSLContext, address: str) -> bytes:
    """The DER certificate the server at `host`:`port` presents; it is sent nothing at all."""
    try:
        _, writer = await asyncio.open_connection(
            host, port, ssl=context, server_hostname=SERVER_NAME
        )
    except OSError as err:
        msg = f"cannot connect to {address}: {err}"
        raise CannotConnectError(msg) from err
    # A TLS server always sends its certificate; `None` would fail the pin check anyway.
    der: bytes = writer.get_extra_info("ssl_object").getpeercert(binary_form=True) or b""
    writer.close()
    with contextlib.suppress(OSError):
        await writer.wait_closed()
    return der


def _check_server_name(der: bytes) -> None:
    """Fail unless the daemon's certificate names `rocko`, which the verification needs.

    An identity made by a daemon before it put the name in has none; the
    connection would fail its hostname check, so this says why first.
    """
    certificate = x509.load_der_x509_certificate(der)
    try:
        names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        dns_names: list[str] = []
    else:
        dns_names = names.value.get_values_for_type(x509.DNSName)
    if SERVER_NAME not in dns_names:
        msg = (
            f"the daemon's certificate does not name {SERVER_NAME!r}, as one made by an older "
            "daemon does not: `rocko identity --reissue` replaces it, then pair again"
        )
        raise ServerCertificateError(msg)


def _refused_certificate(err: BaseException) -> bool:
    """Whether `err` comes from the daemon's `certificate_unknown` alert: it refused the client."""
    cause: BaseException | None = err
    while cause is not None:
        if isinstance(cause, ssl.SSLError) and cause.reason == _CERTIFICATE_UNKNOWN:
            return True
        cause = cause.__cause__
    return False


def _message_of(body: bytes) -> str:
    """The `message` of the daemon's JSON error body, else the start of the body as it is."""
    try:
        error = orjson.loads(body)
    except orjson.JSONDecodeError:
        error = None
    message = error.get("message") if isinstance(error, dict) else None
    return message if isinstance(message, str) else repr(body[:200])
