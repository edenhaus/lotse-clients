"""The client against a real daemon over its Unix socket and its TLS listener, as a program does.

The API contract layer.
Needs `ROCKO_BIN`, a rocko binary built with the `source-fake` feature;
`mise run contract` builds one from a rocko checkout and sets it.
"""

import asyncio
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, cast

import pytest

from rocko_client import (
    API_VERSION,
    CannotConnectError,
    CommandError,
    PairingCode,
    PairingExpiredError,
    PairingRejectedError,
    RockoClient,
    Subscription,
    certificate_pin,
    generate_identity,
    pair,
)
from rocko_client._base import Model
from rocko_client._commands import NEW
from rocko_client.models import (
    RtspSource,
    SessionEvent,
    SessionEventAnswer,
    SessionEventCandidate,
    SessionEventClosed,
    SessionEventSession,
    SessionEventState,
    SourceSpec,
    StreamEvent,
    StreamEventStream,
    StreamEventStreamRemoved,
    StreamGetCommand,
)
from tests.helpers import BUNDLE

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class FakeSource(Model):
    """The fake source of rocko-core's test fakes: `test-util` builds only, so in no schema."""

    url: str
    protocol: Literal["fake"] = "fake"


FAKE = cast("list[SourceSpec]", [FakeSource(url="fake://127.0.0.1/")])
"""A source that goes live without a camera; `stream/get` reports a protocol no model decodes."""

OFFER = "\r\n".join(
    [
        "v=0",
        "o=- 4611731400430051336 2 IN IP4 127.0.0.1",
        "s=-",
        "t=0 0",
        "a=group:BUNDLE 0",
        "a=msid-semantic: WMS",
        "m=video 9 UDP/TLS/RTP/SAVPF 96 97",
        "c=IN IP4 0.0.0.0",
        "a=rtcp:9 IN IP4 0.0.0.0",
        "a=ice-ufrag:abcd",
        "a=ice-pwd:abcdefghijklmnopqrstuvwx",
        "a=ice-options:trickle",
        "a=fingerprint:sha-256 " + ":".join(["AB"] * 32),
        "a=setup:actpass",
        "a=mid:0",
        "a=recvonly",
        "a=rtcp-mux",
        "a=rtcp-rsize",
        "a=rtpmap:96 H264/90000",
        "a=rtcp-fb:96 nack",
        "a=rtcp-fb:96 nack pli",
        "a=fmtp:96 level-asymmetry-allowed=1;packetization-mode=1;profile-level-id=42e01f",
        "a=rtpmap:97 rtx/90000",
        "a=fmtp:97 apt=96",
        "",
    ]
)
"""A browser's video-only offer, shaped like Chrome's, for a session no browser joins."""

STEP = 30
"""Seconds any one step may take."""


async def until_live(events: Subscription[StreamEvent], stream_id: str) -> None:
    """Read stream events until `stream_id` is live."""
    async for event in events:
        if isinstance(event, StreamEventStream) and (event.stream_id, event.state) == (
            stream_id,
            "live",
        ):
            return
    pytest.fail(f"{stream_id} never went live")


async def next_of[T](events: Subscription[SessionEvent], kind: type[T]) -> T:
    """The next event of type `kind`, skipping others."""
    async for event in events:
        if isinstance(event, kind):
            return event
    pytest.fail(f"no {kind.__name__}")


async def test_streams_round_trip_and_fail_with_stable_codes(daemon: Path) -> None:
    async with asyncio.timeout(STEP), RockoClient(daemon) as client:
        assert client.hello.api == API_VERSION
        await client.ping()
        info = await client.info()
        assert {"fake", "rtsp"} <= set(info.protocols)
        assert info.sandbox.mode == "off"
        assert await client.schema() == json.loads(BUNDLE.read_text()), (
            "the daemon serves the bundle this client was generated from"
        )
        # A typed source round trips: no viewer and no preload, so nothing connects.
        await client.stream.put(stream_id="cam", sources=[RtspSource(url="rtsp://127.0.0.1:9/")])
        cam = await client.stream.get(stream_id="cam")
        assert cam.sources[0].source == RtspSource(url="rtsp://127.0.0.1:9/")
        assert list((await client.stream.list()).streams) == ["cam"]
        await client.stream.delete(stream_id="cam")
        async with await client.stream.subscribe() as everything:
            put = await client.stream.put(stream_id="front", sources=FAKE, preload=True)
            assert put.created
            await until_live(everything, "front")
            stream = await client._request(StreamGetCommand(id=NEW, stream_id="front"))
            assert stream["preload"]
            assert stream["tracks"][0]["codec"] == "h264"
            assert (await client.metrics.get()).supervisor.pid > 0
            again = await client.stream.put(stream_id="front", sources=FAKE, preload=True)
            assert not again.created
            await client.stream.delete(stream_id="front")
            async for event in everything:
                if isinstance(event, StreamEventStreamRemoved):
                    assert event.stream_id == "front"
                    break
        assert everything.ended
        failures = {
            "stream_not_found": client.stream.get(stream_id="front"),
            # protocol_unsupported needs a build without a protocol; this one has them all.
            "invalid_request": client.stream.put(
                stream_id="x", sources=[RtspSource(url="http://cam/")]
            ),
            "invalid_stream_id": client.stream.put(stream_id="no spaces", sources=FAKE),
            "subscription_not_found": client.unsubscribe(subscription=12345),
            "session_not_found": client.session.get(session_id="nope"),
        }
        for code, call in failures.items():
            with pytest.raises(CommandError) as raised:
                await call
            assert raised.value.code == code


async def test_a_session_signals_and_is_adopted_after_a_reconnect(daemon: Path) -> None:
    async with asyncio.timeout(STEP), RockoClient(daemon) as first:
        await first.stream.put(stream_id="front", sources=FAKE, preload=True)
        async with await first.stream.subscribe(stream_id="front") as front:
            await until_live(front, "front")
        offer = await first.webrtc.offer(stream_id="front", sdp=OFFER, session_id="s1")
        assert (await next_of(offer, SessionEventSession)).session_id == "s1"
        assert (await next_of(offer, SessionEventAnswer)).sdp.startswith("v=0")
        assert (await next_of(offer, SessionEventCandidate)).candidate.startswith("candidate:")
        await first.webrtc.candidate(
            session_id="s1",
            candidate="candidate:1 1 UDP 2122260223 127.0.0.1 50000 typ host",
            sdp_mid="0",
        )
        session = await first.session.get(session_id="s1")
        assert session.answered
        assert not session.orphaned
        assert [s.session_id for s in (await first.session.list()).sessions] == ["s1"]
    assert offer.ended, "closing the connection ends its subscriptions"

    async with asyncio.timeout(STEP), RockoClient(daemon) as second:
        assert (await second.session.get(session_id="s1")).orphaned
        adopted = await second.session.adopt(session_id="s1")
        assert isinstance(await anext(adopted), SessionEventState), "adopt starts with the state"
        assert not (await second.session.get(session_id="s1")).orphaned
        await second.session.close(session_id="s1")
        closed = await next_of(adopted, SessionEventClosed)
        assert closed.code == "session_closed"
        assert adopted.ended
        assert (await second.session.list()).sessions == []
        await second.stream.delete(stream_id="front")


async def test_a_client_pairs_with_a_code_and_then_connects_over_mutual_tls(
    remote_daemon: tuple[Path, int],
) -> None:
    socket, port = remote_daemon
    async with asyncio.timeout(STEP), RockoClient(socket) as local:
        created = await local.pairing.create()
        assert created.expires_in_ms == 600_000
        code = PairingCode.parse(created.code)
        wrong = PairingCode(server_pin=code.server_pin, secret="0" * 32)
        with pytest.raises(PairingRejectedError):
            await pair("127.0.0.1", port, wrong, generate_identity())
        identity = generate_identity()
        server_certificate = await pair("127.0.0.1", port, created.code, identity)
        assert certificate_pin(server_certificate) == code.server_pin
        with pytest.raises(PairingExpiredError):
            # Used: the daemon refuses an unknown certificate in the handshake.
            await pair("127.0.0.1", port, code, generate_identity())
        (enrolled,) = (await local.clients.list()).clients
        assert enrolled.pin.replace(":", "") == identity.pin
        assert (enrolled.role, enrolled.fixed, enrolled.connected) == ("control", False, False)

        remote = RockoClient.remote(
            "127.0.0.1", port, identity=identity, server_certificate=server_certificate
        )
        async with remote:
            assert remote.hello.api == API_VERSION
            info = await remote.info()
            assert "fake" in info.protocols
            assert (await local.clients.list()).clients[0].connected
            with pytest.raises(CommandError) as refused:
                await remote.pairing.create()
            assert refused.value.code == "not_permitted", "no pairing code over the listener"

    stranger = RockoClient.remote(
        "127.0.0.1", port, identity=generate_identity(), server_certificate=server_certificate
    )
    async with asyncio.timeout(STEP):
        with pytest.raises(CannotConnectError):
            await stranger.connect()
