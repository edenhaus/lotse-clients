"""Pairing and the remote client against in-process TLS servers that require client certificates."""

import asyncio
import base64
import contextlib
import datetime as dt
import hashlib
import json
import socket
import ssl
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID, ObjectIdentifier

from rocko_client import (
    CannotConnectError,
    ClientIdentity,
    InvalidPairingCodeError,
    PairingCode,
    PairingError,
    PairingExpiredError,
    PairingRejectedError,
    RockoClient,
    ServerCertificateError,
    ServerPinMismatchError,
    certificate_pin,
    client_ssl_context,
    generate_identity,
    pair,
)
from rocko_client.client import _TlsEndpoint
from rocko_client.tls import PAIR_PATH, authority
from tests.helpers import FakeDaemon

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

HEX32 = "0123456789abcdef0123456789abcdef"
"""The secret of the codes here: 32 lowercase hex digits."""


def certificate(
    common_name: str = "rocko control", *, dns: str | None = "rocko", critical: bool = False
) -> tuple[str, str]:
    """A self-signed P-256 certificate and its key, PEM.

    Shaped like the daemon's (rcgen: the subject alternative name as its only
    extension), naming `dns`, or with no extension when `dns` is `None`;
    `critical` adds an extension nobody knows, marked critical, which
    OpenSSL refuses in a client certificate with the `certificate_unknown`
    alert, the one the daemon sends.
    """
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    builder = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(dt.datetime(2000, 1, 1, tzinfo=dt.UTC))
        .not_valid_after(dt.datetime(9999, 12, 31, tzinfo=dt.UTC))
    )
    if dns is not None:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False
        )
    if critical:
        unknown = x509.UnrecognizedExtension(ObjectIdentifier("1.3.6.1.4.1.55555.1"), b"\x05\x00")
        builder = builder.add_extension(unknown, critical=True)
    pem = builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM).decode()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    return pem, private


def code_for(server_certificate: str, secret: str = HEX32) -> str:
    """The pairing code a daemon with `server_certificate` prints."""
    return f"ROCKO1-{certificate_pin(server_certificate)}-{secret}"


def server_context(directory: Path, server: tuple[str, str], trusts: str) -> ssl.SSLContext:
    """A TLS 1.3 server context presenting `server`, requiring a client certificate in `trusts`."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    chain = Path(tempfile.mkstemp(dir=directory, suffix=".pem")[1])
    chain.write_text(server[1] + server[0])
    context.load_cert_chain(chain)
    context.verify_mode = ssl.CERT_REQUIRED
    context.load_verify_locations(cadata=trusts)
    return context


@pytest.fixture
def identity() -> ClientIdentity:
    return generate_identity()


@pytest.fixture
def daemon_certificate() -> tuple[str, str]:
    return certificate()


@asynccontextmanager
async def tls_daemon(
    directory: Path, server: tuple[str, str], trusts: str
) -> AsyncIterator[tuple[FakeDaemon, int]]:
    """A fake daemon on a free port of 127.0.0.1 with mutual TLS; yields it and its port."""
    fake = FakeDaemon(directory / "unused.sock")
    port = await fake.start_tls(server_context(directory, server, trusts))
    try:
        yield fake, port
    finally:
        await fake.stop()


def free_port() -> int:
    """A TCP port of 127.0.0.1 that nothing listens on."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


# The pairing code.


def test_a_pairing_code_is_read_with_the_whitespace_a_paste_brings_ignored() -> None:
    pin = "ab" * 32
    code = PairingCode.parse(f"  ROCKO1-{pin}-{HEX32}\n")
    assert (code.server_pin, code.secret) == (pin, HEX32)


@pytest.mark.parametrize(
    "code",
    [
        "",
        f"ROCKO2-{'ab' * 32}-{HEX32}",
        f"rocko1-{'ab' * 32}-{HEX32}",
        f"ROCKO1-{'AB' * 32}-{HEX32}",
        f"ROCKO1-{'ab' * 32}-{HEX32.upper()}",
        f"ROCKO1-{'ab' * 31}-{HEX32}",
        f"ROCKO1-{'ab' * 32}-{HEX32}00",
        f"ROCKO1-{'ab' * 32}",
        f"ROCKO1-{'ab' * 32}-{HEX32}-00",
        f"ROCKO1-{'ab' * 32} {HEX32}",
        f"ROCKO1-{'ab' * 32}- {HEX32}",
        f"ROCKO1-{'gh' * 32}-{HEX32}",
        f"ROCKO1-{':'.join(['ab'] * 32)}-{HEX32}",
    ],
)
def test_a_malformed_pairing_code_is_refused_without_repeating_it(code: str) -> None:
    with pytest.raises(InvalidPairingCodeError, match="not a pairing code") as raised:
        PairingCode.parse(code)
    assert HEX32 not in str(raised.value)
    assert HEX32.upper() not in str(raised.value)


def test_a_pairing_code_never_shows_its_secret() -> None:
    code = PairingCode.parse(f"ROCKO1-{'ab' * 32}-{HEX32}")
    assert HEX32 not in repr(code)
    assert HEX32 not in str(code)
    assert "ab" * 32 in repr(code)


# The identity.


def test_a_generated_identity_is_a_self_signed_p256_certificate_that_never_expires() -> None:
    identity = generate_identity("home-assistant")
    cert = x509.load_pem_x509_certificate(identity.certificate.encode())
    key = serialization.load_pem_private_key(identity.private_key.encode(), password=None)
    assert isinstance(key, ec.EllipticCurvePrivateKey)
    assert isinstance(key.curve, ec.SECP256R1)
    assert cert.public_key() == key.public_key()
    assert cert.subject == cert.issuer
    assert cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value == "home-assistant"
    cert.verify_directly_issued_by(cert)
    assert cert.not_valid_before_utc == dt.datetime(2000, 1, 1, tzinfo=dt.UTC)
    assert cert.not_valid_after_utc == dt.datetime(9999, 12, 31, 23, 59, 59, tzinfo=dt.UTC)
    assert cert.version is x509.Version.v3
    assert generate_identity().pin != identity.pin, "every identity has a key of its own"


def test_the_pin_is_the_sha256_of_the_der_certificate_as_openssl_fingerprints_it() -> None:
    identity = generate_identity()
    cert = x509.load_pem_x509_certificate(identity.certificate.encode())
    assert identity.pin == cert.fingerprint(hashes.SHA256()).hex()
    body = "".join(identity.certificate.strip().splitlines()[1:-1])
    assert identity.pin == hashlib.sha256(base64.b64decode(body)).hexdigest()
    assert len(identity.pin) == 64
    assert identity.pin == identity.pin.lower()


def test_an_identity_shows_its_certificate_but_never_its_key() -> None:
    identity = generate_identity()
    assert identity.private_key not in repr(identity)
    assert "PRIVATE KEY" not in repr(identity)
    assert repr(identity.certificate) in repr(identity)


# The SSL context.


def test_the_context_presents_the_identity_and_trusts_only_the_daemon_over_tls_1_3(
    identity: ClientIdentity, daemon_certificate: tuple[str, str]
) -> None:
    context = client_ssl_context(identity, daemon_certificate[0])
    assert context.minimum_version is ssl.TLSVersion.TLSv1_3
    assert context.verify_mode is ssl.CERT_REQUIRED
    assert context.check_hostname
    assert context.cert_store_stats()["x509"] == 1, "the daemon's certificate and nothing else"
    assert context.verify_flags & ssl.VERIFY_X509_PARTIAL_CHAIN, "itself the trust anchor"


def test_the_identity_goes_through_a_private_file_that_is_removed_at_once(
    identity: ClientIdentity,
    daemon_certificate: tuple[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    modes: list[int] = []
    load = ssl.SSLContext.load_cert_chain

    def spy(self: ssl.SSLContext, certfile: str) -> None:
        modes.append(Path(certfile).stat().st_mode & 0o777)
        assert Path(certfile).parent == tmp_path
        load(self, certfile)

    monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", spy)
    client_ssl_context(identity, daemon_certificate[0])
    assert modes == [0o600]
    assert list(tmp_path.iterdir()) == []
    broken = ClientIdentity(certificate=identity.certificate, private_key="not a key")
    with pytest.raises(ssl.SSLError):
        client_ssl_context(broken, daemon_certificate[0])
    assert list(tmp_path.iterdir()) == [], "removed when loading fails too"


def test_an_ipv6_address_is_bracketed_in_urls_and_a_name_is_not() -> None:
    assert authority("::1", 18557) == "[::1]:18557"
    assert authority("fd00::5", 1) == "[fd00::5]:1"
    assert authority("192.168.1.10", 18557) == "192.168.1.10:18557"
    assert authority("rocko.local", 18557) == "rocko.local:18557"


# Pairing.


async def test_pairing_checks_the_pin_sends_the_secret_once_and_returns_the_certificate(
    identity: ClientIdentity, daemon_certificate: tuple[str, str], tmp_path: Path
) -> None:
    async with tls_daemon(tmp_path, daemon_certificate, identity.certificate) as (fake, port):
        returned = await pair("127.0.0.1", port, code_for(daemon_certificate[0]), identity)
    assert certificate_pin(returned) == certificate_pin(daemon_certificate[0])
    assert returned.startswith("-----BEGIN CERTIFICATE-----")
    assert [json.loads(body) for body in fake.pair_requests] == [{"secret": HEX32}]


async def test_pairing_through_a_session_passed_in_leaves_it_open(
    identity: ClientIdentity, daemon_certificate: tuple[str, str], tmp_path: Path
) -> None:
    code = PairingCode.parse(code_for(daemon_certificate[0]))
    async with (
        tls_daemon(tmp_path, daemon_certificate, identity.certificate) as (fake, port),
        aiohttp.ClientSession() as session,
    ):
        await pair("127.0.0.1", port, code, identity, session=session)
        assert not session.closed
    assert len(fake.pair_requests) == 1


@pytest.mark.parametrize(
    "answer",
    [
        (
            403,
            b'{"code":"not_permitted","message":"the secret is not the open code\'s"}',
            PairingRejectedError,
            "refused the pairing secret: the secret is not the open code's",
        ),
        (
            410,
            b'{"code":"not_permitted","message":"no pairing code is open: it expired or was used"}',
            PairingExpiredError,
            "no pairing code is open on the daemon: no pairing code is open: it expired",
        ),
        (
            400,
            b'{"code":"invalid_request","message":"the body must be a secret"}',
            PairingError,
            "pairing failed with HTTP 400: the body must be a secret",
        ),
        (500, b"<html>oops</html>", PairingError, r"HTTP 500: b'<html>oops</html>'"),
        (404, b"", PairingError, r"HTTP 404: b''"),
        (418, b'{"code":"teapot"}', PairingError, r"HTTP 418: b'\{\"code\":\"teapot\"\}'"),
        (418, b'["message"]', PairingError, r"HTTP 418: b'\[\"message\"\]'"),
    ],
)
async def test_a_refused_pairing_raises_by_the_daemons_status(
    identity: ClientIdentity,
    daemon_certificate: tuple[str, str],
    tmp_path: Path,
    answer: tuple[int, bytes, type[PairingError], str],
) -> None:
    status, body, error, match = answer
    async with tls_daemon(tmp_path, daemon_certificate, identity.certificate) as (fake, port):
        fake.pair_answer = (status, body)
        with pytest.raises(error, match=match) as raised:
            await pair("127.0.0.1", port, code_for(daemon_certificate[0]), identity)
    assert type(raised.value) is error
    assert HEX32 not in str(raised.value)


async def test_pairing_answers_keep_their_meaning_through_a_session_that_raises_for_status(
    identity: ClientIdentity, daemon_certificate: tuple[str, str], tmp_path: Path
) -> None:
    async with tls_daemon(tmp_path, daemon_certificate, identity.certificate) as (fake, port):
        fake.pair_answer = (
            403,
            b'{"code":"not_permitted","message":"the secret is not the open code\'s"}',
        )
        async with aiohttp.ClientSession(raise_for_status=True) as session:
            with pytest.raises(PairingRejectedError):
                await pair(
                    "127.0.0.1", port, code_for(daemon_certificate[0]), identity, session=session
                )


async def test_a_server_with_another_certificate_than_the_codes_is_never_sent_the_secret(
    identity: ClientIdentity, daemon_certificate: tuple[str, str], tmp_path: Path
) -> None:
    impostor = certificate()
    async with tls_daemon(tmp_path, impostor, identity.certificate) as (fake, port):
        with pytest.raises(ServerPinMismatchError, match="not the one the pairing code names"):
            await pair("127.0.0.1", port, code_for(daemon_certificate[0]), identity)
    assert fake.pair_requests == []


@pytest.mark.parametrize("dns", [None, "rocko.example"])
async def test_a_daemon_certificate_without_the_name_rocko_is_refused_before_the_secret_is_sent(
    identity: ClientIdentity, tmp_path: Path, dns: str | None
) -> None:
    legacy = certificate(dns=dns)
    async with tls_daemon(tmp_path, legacy, identity.certificate) as (fake, port):
        with pytest.raises(ServerCertificateError, match="rocko identity --reissue"):
            await pair("127.0.0.1", port, code_for(legacy[0]), identity)
    assert fake.pair_requests == []


async def test_pairing_with_nothing_listening_cannot_connect(identity: ClientIdentity) -> None:
    port = free_port()
    with pytest.raises(CannotConnectError, match=f"cannot connect to 127.0.0.1:{port}"):
        await pair("127.0.0.1", port, code_for(certificate()[0]), identity)


async def test_a_broken_identity_fails_pairing_before_anything_is_dialed(
    identity: ClientIdentity,
) -> None:
    key = identity.private_key.replace("MI", "XX")
    broken = ClientIdentity(certificate=identity.certificate, private_key=key)
    with pytest.raises(CannotConnectError, match="cannot load the client identity") as raised:
        await pair("127.0.0.1", free_port(), code_for(certificate()[0]), broken)
    assert key not in str(raised.value)


async def test_a_malformed_code_fails_before_anything_is_dialed(identity: ClientIdentity) -> None:
    with pytest.raises(InvalidPairingCodeError):
        await pair("127.0.0.1", free_port(), "ROCKO1-nope", identity)


class RefusingServer:
    """A TLS 1.3 server whose OpenSSL refuses the client certificate after each handshake.

    OpenSSL sends the alert its verification error maps to and the server
    then closes its side cleanly, so the client reads the alert: a client
    certificate in `trusts` with an unknown critical extension gets
    `certificate_unknown`, the alert the daemon (rustls) sends to an unknown
    client while no code is open; one not in `trusts` gets `unknown_ca`.
    """

    def __init__(self, context: ssl.SSLContext, connections: int) -> None:
        """Listen on a free port of 127.0.0.1, for `connections` connections."""
        self._context = context
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port: int = self._listener.getsockname()[1]
        self.refusals: list[str] = []
        self._thread = threading.Thread(target=self._serve, args=(connections,))
        self._thread.start()

    def _serve(self, connections: int) -> None:
        with self._listener:
            for _ in range(connections):
                raw, _ = self._listener.accept()
                with (
                    self._context.wrap_socket(
                        raw, server_side=True, do_handshake_on_connect=False
                    ) as tls,
                    contextlib.suppress(OSError),
                ):
                    try:
                        tls.do_handshake()
                    except ssl.SSLError as err:
                        self.refusals.append(str(err.reason))
                    # Without the TLS layer now: our side closed, the client's read to its end.
                    tls.shutdown(socket.SHUT_WR)
                    while tls.recv(4096):
                        pass

    async def join(self) -> None:
        await asyncio.to_thread(self._thread.join)


@pytest.mark.parametrize(
    "refusal",
    [
        ("certificate_unknown", PairingExpiredError, "refused this client: no pairing code"),
        ("unknown_ca", CannotConnectError, "UNKNOWN_CA"),
    ],
)
async def test_a_client_certificate_refused_in_the_handshake_means_no_code_is_open(
    daemon_certificate: tuple[str, str], tmp_path: Path, refusal: tuple[str, type[Exception], str]
) -> None:
    alert, error, match = refusal
    cert, key = certificate("client", dns=None, critical=True)
    refused = ClientIdentity(certificate=cert, private_key=key)
    trusts = cert if alert == "certificate_unknown" else certificate()[0]
    server = RefusingServer(server_context(tmp_path, daemon_certificate, trusts), connections=2)
    with pytest.raises(error, match=match):
        await pair("127.0.0.1", server.port, code_for(daemon_certificate[0]), refused)
    await server.join()
    assert len(server.refusals) == 2, "the probe and the request were both refused"


# The remote client.


def remote(
    port: int,
    identity: ClientIdentity,
    server_certificate: str,
    host: str = "127.0.0.1",
    session: aiohttp.ClientSession | None = None,
) -> RockoClient:
    return RockoClient.remote(
        host, port, identity=identity, server_certificate=server_certificate, session=session
    )


async def test_a_remote_client_connects_over_mutual_tls_and_reconnects(
    identity: ClientIdentity, daemon_certificate: tuple[str, str], tmp_path: Path
) -> None:
    async with tls_daemon(tmp_path, daemon_certificate, identity.certificate) as (fake, port):
        client = remote(port, identity, daemon_certificate[0])
        async with client:
            assert client.connected
            await client.ping()
        assert client._session is None, "its own session is closed with it"
        endpoint = client._endpoint
        assert isinstance(endpoint, _TlsEndpoint)
        context = endpoint._context
        assert context is not None
        await client.connect()
        assert endpoint._context is context, "the context is built once"
        await client.ping()
        await client.close()
    assert fake.connections == 2
    assert [frame["type"] for frame in fake.received] == ["ping", "ping"]


async def test_a_remote_client_leaves_a_session_passed_in_open(
    identity: ClientIdentity, daemon_certificate: tuple[str, str], tmp_path: Path
) -> None:
    async with (
        tls_daemon(tmp_path, daemon_certificate, identity.certificate) as (_, port),
        aiohttp.ClientSession() as session,
    ):
        async with remote(port, identity, daemon_certificate[0], session=session) as client:
            await client.ping()
        assert not session.closed


@pytest.mark.parametrize("case", ["another certificate", "no name", "another name"])
async def test_a_remote_client_trusts_exactly_the_paired_certificate_by_the_name_rocko(
    identity: ClientIdentity, tmp_path: Path, case: str
) -> None:
    if case == "another certificate":
        served, paired = certificate(), certificate()[0]
    else:
        served = certificate(dns=None if case == "no name" else "rocko.example")
        paired = served[0]
    async with tls_daemon(tmp_path, served, identity.certificate) as (fake, port):
        client = remote(port, identity, paired)
        with pytest.raises(CannotConnectError, match="CERTIFICATE_VERIFY_FAILED"):
            await client.connect()
        assert not client.connected
        assert client._session is None
    assert fake.connections == 0


async def test_a_remote_client_with_a_broken_identity_cannot_connect_and_never_shows_the_key(
    identity: ClientIdentity, daemon_certificate: tuple[str, str]
) -> None:
    key = identity.private_key.replace("MI", "XX")
    broken = ClientIdentity(certificate=identity.certificate, private_key=key)
    client = remote(free_port(), broken, daemon_certificate[0])
    with pytest.raises(CannotConnectError) as raised:
        await client.connect()
    assert key not in str(raised.value)
    assert "PRIVATE KEY" not in str(raised.value)
    assert client._session is None


async def test_a_remote_client_with_an_empty_daemon_certificate_cannot_connect(
    identity: ClientIdentity,
) -> None:
    client = remote(free_port(), identity, "")
    with pytest.raises(CannotConnectError, match="the daemon certificate does not load"):
        await client.connect()
    assert client._session is None


async def test_a_remote_client_names_the_url_it_could_not_reach_with_ipv6_in_brackets(
    identity: ClientIdentity, daemon_certificate: tuple[str, str]
) -> None:
    port = free_port()
    client = remote(port, identity, daemon_certificate[0], host="::1")
    with pytest.raises(CannotConnectError, match=rf"cannot open wss://\[::1\]:{port}/v0/ws: "):
        await client.connect()


def test_the_pair_path_carries_the_major_version_as_the_websocket_path_does() -> None:
    assert PAIR_PATH == "/v0/pair"
