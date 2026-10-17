"""The transport against a scripted fake daemon: framing, ids, results, events, failures."""

import asyncio
import logging
from typing import Any

import aiohttp
import pytest

from lotse_client import (
    API_VERSION,
    ApiVersionError,
    CannotConnectError,
    CommandError,
    ConnectionClosedError,
    LotseClient,
    LotseError,
    ProtocolError,
)
from lotse_client.models import (
    SessionEventAnswer,
    SessionEventClosed,
    SessionEventSession,
    SessionEventState,
    SourceSpec,
    StreamEventStream,
)
from tests.helpers import HELLO, NEXT_MAJOR, STREAM, FakeDaemon

INFO = {
    "version": "0.0.0",
    "build": {"target": "x86_64-unknown-linux-musl", "rustc": "1.90.0", "git_sha": None},
    "schemes": ["rtsp"],
    "outputs": ["webrtc"],
    "features": [],
    "codecs": {"video": ["h264"], "audio": ["opus"]},
    "limits": {
        "max_streams": 256,
        "max_sessions": 256,
        "max_sessions_per_stream": 16,
        "max_connections": 8,
        "session_grace_ms": 10000,
    },
    "sandbox": {
        "mode": "off",
        "uid": 1000,
        "gid": 1000,
        "no_new_privs": False,
        "seccomp": "off",
        "landlock": {"fs": "off", "net": "off", "abi": 0},
    },
}
METRICS = {
    "supervisor": {"pid": 1, "uptime_ms": 5, "demux": None},
    "workers": {
        "c1": {
            "pid": 2,
            "uptime_ms": 4,
            "streams": ["front"],
            "restarts": 0,
            "sessions": 1,
            "send_failures": 0,
            "relay_unbound": 0,
        }
    },
    "worker_restarts": 0,
    "sessions": 1,
    "streams": {"front": {"stalls": 1}},
}
SESSION = {
    "session_id": "s1",
    "stream_id": "front",
    "ice": "new",
    "dtls": "new",
    "answered": True,
    "orphaned": False,
    "since": "2026-10-05T00:00:00.000Z",
}


def failure(command_id: int | None, code: str, **details: object) -> dict[str, Any]:
    """A failed `result` frame."""
    error: dict[str, Any] = {"code": code, "message": f"{code} happened"}
    if details:
        error["details"] = details
    return {"type": "result", "id": command_id, "success": False, "error": error}


def event(subscription: int, payload: dict[str, Any]) -> dict[str, Any]:
    """An `event` frame."""
    return {"type": "event", "id": subscription, "event": payload}


async def test_connect_reads_hello_and_close_ends_cleanly(fake: FakeDaemon) -> None:
    client = LotseClient(fake.path)
    assert not client.connected
    with pytest.raises(ConnectionClosedError):
        _ = client.hello
    with pytest.raises(ConnectionClosedError, match="never connected"):
        await client.wait_closed()
    async with client:
        assert client.connected
        assert client.hello.outputs == ["webrtc"]
        with pytest.raises(LotseError, match="already connected"):
            await client.connect()
    assert not client.connected
    assert await client.wait_closed() is None
    with pytest.raises(ConnectionClosedError, match="not connected"):
        await client.ping()


async def test_every_command_method_sends_its_frame_and_decodes_its_result(
    fake: FakeDaemon,
) -> None:
    fake.results = {
        "info": INFO,
        "metrics/get": METRICS,
        "schema": {"api": "0.1.0"},
        "stream/put": {"created": True},
        "stream/get": STREAM,
        "stream/list": {"streams": {"front": STREAM}},
        "session/get": SESSION,
        "session/list": {"sessions": [SESSION]},
    }
    async with LotseClient(fake.path) as client:
        await client.ping()
        assert (await client.info()).limits.max_sessions_per_stream == 16
        assert (await client.metrics.get()).streams["front"].stalls == 1
        assert await client.schema() == {"api": "0.1.0"}
        put = await client.stream.put(
            stream_id="front", sources=[SourceSpec(url="rtsp://cam/")], preload=True
        )
        assert put.created
        assert (await client.stream.get(stream_id="front")).state == "live"
        assert list((await client.stream.list()).streams) == ["front"]
        await client.stream.delete(stream_id="front")
        streams = await client.stream.subscribe(stream_id="front")
        offer = await client.webrtc.offer(stream_id="front", sdp="v=0", session_id="s1")
        await client.webrtc.candidate(session_id="s1", candidate="", sdp_mline_index=0)
        assert (await client.session.get(session_id="s1")).answered
        assert (await client.session.list()).sessions[0].session_id == "s1"
        await client.session.close(session_id="s1")
        adopted = await client.session.adopt(session_id="s1")
        for subscription in (streams, offer, adopted):
            await subscription.unsubscribe()
            assert subscription.ended
    sent = [(frame["type"], frame["id"]) for frame in fake.received]
    assert [kind for kind, _ in sent] == [
        "ping",
        "info",
        "metrics/get",
        "schema",
        "stream/put",
        "stream/get",
        "stream/list",
        "stream/delete",
        "stream/subscribe",
        "webrtc/offer",
        "webrtc/candidate",
        "session/get",
        "session/list",
        "session/close",
        "session/adopt",
        "unsubscribe",
        "unsubscribe",
        "unsubscribe",
    ]
    assert [command_id for _, command_id in sent] == list(range(1, len(sent) + 1))
    assert fake.received[4]["sources"] == [{"url": "rtsp://cam/"}]
    assert fake.received[4]["preload"] is True
    assert fake.received[10] == {
        "type": "webrtc/candidate",
        "id": 11,
        "session_id": "s1",
        "candidate": "",
        "sdp_mline_index": 0,
    }
    assert [frame["subscription"] for frame in fake.received[15:]] == [9, 10, 15]


async def test_ids_increase_strictly_however_many_tasks_send(fake: FakeDaemon) -> None:
    async with LotseClient(fake.path) as client:
        await asyncio.gather(*(client.ping() for _ in range(25)))
    assert [frame["id"] for frame in fake.received] == list(range(1, 26))


async def test_a_failed_command_raises_its_code_message_and_details(fake: FakeDaemon) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        if frame["type"] == "stream/get":
            await fake.send(failure(frame["id"], "stream_not_found", stream_id="nope"))
        else:
            await fake.send(failure(frame["id"], "a_code_from_later"))

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        with pytest.raises(CommandError) as raised:
            await client.stream.get(stream_id="nope")
        assert raised.value.code == "stream_not_found"
        assert raised.value.message == "stream_not_found happened"
        assert raised.value.details == {"stream_id": "nope"}
        assert str(raised.value) == "stream_not_found: stream_not_found happened"
        with pytest.raises(CommandError) as later:
            await client.info()
        assert later.value.code == "a_code_from_later"
        assert later.value.details == {}


async def test_an_unreadable_failure_is_a_protocol_error(fake: FakeDaemon) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        await fake.send({"type": "result", "id": frame["id"], "success": False, "error": {}})

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        with pytest.raises(ProtocolError, match="unreadable failure"):
            await client.info()


async def test_a_result_that_is_not_an_object_is_a_protocol_error(fake: FakeDaemon) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        await fake.send({"type": "result", "id": frame["id"], "success": True, "result": [1]})

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        with pytest.raises(ProtocolError, match="not an object"):
            await client.info()


async def test_frames_for_nothing_pending_and_unknown_frames_are_ignored(
    fake: FakeDaemon, caplog: pytest.LogCaptureFixture
) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        for noise in (
            {"type": "result", "id": 999, "success": True, "result": {}},
            failure(None, "invalid_request"),
            {"type": "pong", "id": 999},
            {"type": "pong"},
            event(999, {"type": "stream_removed", "stream_id": "x"}),
            {"type": "event"},
            [1, 2],
            {"type": "added_in_a_later_version"},
        ):
            await fake.send(noise)
        await fake.answer(frame)

    fake.on_frame = on_frame
    with caplog.at_level(logging.DEBUG, logger="lotse_client"):
        async with LotseClient(fake.path) as client:
            await client.ping()
    messages = [record.getMessage() for record in caplog.records]
    assert sum("no pending command" in m for m in messages) == 2
    assert sum("event for no subscription" in m for m in messages) == 2
    assert any("not an object" in m for m in messages)
    assert any("'added_in_a_later_version'" in m for m in messages)


async def test_a_session_subscription_yields_its_events_and_ends_after_closed(
    fake: FakeDaemon, caplog: pytest.LogCaptureFixture
) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        await fake.answer(frame)
        sub = frame["id"]
        for payload in (
            {"type": "session", "session_id": "s1"},
            {"type": "answer", "sdp": "v=0"},
            {"type": "added_in_a_later_version"},
            {"type": "state"},
            {"type": "state", "ice": "checking", "dtls": "new"},
            {"type": "closed", "code": "peer_closed", "message": "bye"},
            {"type": "state", "ice": "closed", "dtls": "closed"},
        ):
            await fake.send(event(sub, payload))

    fake.on_frame = on_frame
    with caplog.at_level(logging.DEBUG, logger="lotse_client"):
        async with LotseClient(fake.path) as client:
            offer = await client.webrtc.offer(stream_id="front", sdp="v=0")
            events = [e async for e in offer]
            assert offer.ended
            assert [type(e) for e in events] == [
                SessionEventSession,
                SessionEventAnswer,
                SessionEventState,
                SessionEventClosed,
            ]
            assert [e async for e in offer] == [], "an ended subscription stays ended"
            await offer.unsubscribe()
    assert [frame["type"] for frame in fake.received] == ["webrtc/offer"], "nothing to unsubscribe"
    dropped = [r for r in caplog.records if "cannot read" in r.getMessage()]
    assert len(dropped) == 2, "the unknown type and the incomplete state"
    assert any("event for no subscription" in r.getMessage() for r in caplog.records)


async def test_unsubscribe_keeps_an_event_sent_before_its_result(fake: FakeDaemon) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        if frame["type"] == "unsubscribe":
            last = {"type": "stream", "stream_id": "front", "state": "idle", "last_error": None}
            await fake.send(event(frame["subscription"], last))
        await fake.answer(frame)

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        async with await client.stream.subscribe() as streams:
            pass
        assert streams.ended
        assert [e async for e in streams] == [
            StreamEventStream(stream_id="front", state="idle", last_error=None)
        ]
        once = await client.stream.subscribe()
        await asyncio.gather(once.unsubscribe(), once.unsubscribe())
    kinds = [frame["type"] for frame in fake.received]
    assert kinds == ["stream/subscribe", "unsubscribe", "stream/subscribe", "unsubscribe"]


async def test_a_failed_subscribe_raises_and_leaves_no_subscription(fake: FakeDaemon) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        await fake.send(failure(frame["id"], "stream_not_found"))
        await fake.send(event(frame["id"], {"type": "stream_removed", "stream_id": "x"}))

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        with pytest.raises(CommandError, match="stream_not_found"):
            await client.stream.subscribe(stream_id="nope")
        assert client._subscriptions == {}


async def test_the_daemon_closing_fails_what_waits(fake: FakeDaemon) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        if frame["type"] == "ping":
            await fake.close_connection()
        else:
            await fake.answer(frame)

    fake.on_frame = on_frame
    client = LotseClient(fake.path)
    await client.connect()
    streams = await client.stream.subscribe()
    with pytest.raises(ConnectionClosedError, match="the daemon closed the connection"):
        await client.ping()
    with pytest.raises(ConnectionClosedError, match="the daemon closed the connection"):
        await anext(streams)
    error = await client.wait_closed()
    assert isinstance(error, ConnectionClosedError)
    assert not client.connected
    with pytest.raises(ConnectionClosedError, match="not connected"):
        await client.info()
    await streams.unsubscribe()
    await client.close()


async def test_a_shutdown_frame_names_the_reason(fake: FakeDaemon) -> None:
    async def on_frame(_frame: dict[str, Any]) -> None:
        await fake.send({"type": "shutdown"})
        await fake.close_connection()

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        with pytest.raises(ConnectionClosedError, match="the daemon shut down"):
            await client.ping()


@pytest.mark.parametrize(
    ("frame", "error", "match"),
    [
        (b"\x00", ProtocolError, "unexpected BINARY frame"),
        ("not json", ProtocolError, "not JSON"),
        ("x" * (4 * 1024 * 1024 + 1), ConnectionClosedError, "connection failed"),
    ],
)
async def test_a_frame_outside_the_contract_ends_the_connection(
    fake: FakeDaemon, frame: str | bytes, error: type[LotseError], match: str
) -> None:
    async def on_frame(_: dict[str, Any]) -> None:
        await fake.send(frame)

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        with pytest.raises(error, match=match):
            await client.ping()
        assert isinstance(await client.wait_closed(), error)


@pytest.mark.parametrize(
    ("hello", "error", "match"),
    [
        (b"\x00", ProtocolError, "expected hello, got a BINARY frame"),
        ("not json", ProtocolError, "expected hello"),
        ({"type": "event", "id": 1, "event": {}}, ProtocolError, "expected hello"),
        ({**HELLO, "api": NEXT_MAJOR}, ApiVersionError, f"daemon speaks API version {NEXT_MAJOR}"),
    ],
)
async def test_a_wrong_greeting_fails_connect(
    fake: FakeDaemon, hello: object, error: type[LotseError], match: str
) -> None:
    fake.hello = hello
    client = LotseClient(fake.path)
    with pytest.raises(error, match=match) as raised:
        await client.connect()
    assert not client.connected
    assert client._session is None, "the client's own session is closed again"
    if isinstance(raised.value, ApiVersionError):
        assert (raised.value.daemon, raised.value.client) == (NEXT_MAJOR, API_VERSION)


async def test_a_missing_socket_cannot_connect(fake: FakeDaemon) -> None:
    client = LotseClient(fake.path.with_name("absent.sock"))
    with pytest.raises(CannotConnectError, match=r"absent\.sock"):
        await client.connect()
    assert client._session is None


async def test_a_session_passed_in_stays_open_and_the_client_reconnects(fake: FakeDaemon) -> None:
    async with aiohttp.ClientSession(
        connector=aiohttp.UnixConnector(path=str(fake.path))
    ) as session:
        client = LotseClient(fake.path, session=session)
        await client.connect()
        await client.ping()
        await client.close()
        assert not session.closed
        await client.connect()
        await client.ping()
        await client.close()
        await client.close()
    assert [frame["id"] for frame in fake.received] == [1, 1], "ids start over per connection"
    assert fake.connections == 2


async def test_a_failed_send_is_a_closed_connection(
    fake: FakeDaemon, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with LotseClient(fake.path) as client:
        assert client._ws is not None

        async def broken(_: str) -> None:
            msg = "reset"
            raise ConnectionResetError(msg)

        monkeypatch.setattr(client._ws, "send_str", broken)
        with pytest.raises(ConnectionClosedError, match="sending failed: reset"):
            await client.stream.subscribe()
        assert client._pending == {}
        assert client._subscriptions == {}
        monkeypatch.undo()


async def test_a_cancelled_command_is_forgotten(fake: FakeDaemon) -> None:
    async def on_frame(frame: dict[str, Any]) -> None:
        if frame["type"] == "ping":
            await fake.answer(frame)

    fake.on_frame = on_frame
    async with LotseClient(fake.path) as client:
        waiting = asyncio.create_task(client.info())
        await fake.frame_received.wait()
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert client._pending == {}
        await client.ping()


async def test_an_answer_for_a_command_already_given_up_is_dropped(fake: FakeDaemon) -> None:
    # The window between a cancelled wait and `_send` forgetting it.
    async with LotseClient(fake.path) as client:
        for command_id, frame in (
            (90, {"type": "result", "id": 90, "success": True, "result": {}}),
            (91, {"type": "pong", "id": 91}),
        ):
            given_up: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
            given_up.cancel()
            client._pending[command_id] = given_up
            client._dispatch(frame)
            assert command_id not in client._pending


async def test_close_fails_a_command_still_waiting(fake: FakeDaemon) -> None:
    async def on_frame(_: dict[str, Any]) -> None:
        pass

    fake.on_frame = on_frame
    client = LotseClient(fake.path)
    await client.connect()
    waiting = asyncio.create_task(client.info())
    await fake.frame_received.wait()
    given_up: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    given_up.cancel()
    client._pending[99] = given_up
    await client.close()
    with pytest.raises(ConnectionClosedError, match="connection closed"):
        await waiting
    assert await client.wait_closed() is None
