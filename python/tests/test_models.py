"""The generated models against the contract's examples."""

import pytest
from mashumaro.codecs.basic import BasicDecoder
from mashumaro.exceptions import MissingField

from lotse_client._commands import SESSION_EVENTS, STREAM_EVENTS
from lotse_client.models import (
    Hello,
    IceServer,
    SessionEventCandidate,
    SessionEventClosed,
    SourceSpec,
    Stream,
    StreamEventStream,
    StreamEventStreamRemoved,
    StreamPutCommand,
    StreamState,
    StreamSubscribeCommand,
    WebrtcOfferCommand,
)
from tests.helpers import STREAM


def test_a_stream_decodes_with_nested_types_and_defaults() -> None:
    stream = Stream.from_dict(STREAM)
    assert stream.state == "live"
    assert stream.sources[0].connection.worker is not None
    assert stream.sources[0].connection.worker.pid == 4711
    assert stream.tracks[0].audio_delay_ms == 88
    assert stream.stats.keyframe_requests == 3
    assert stream.stats.stalls == 0, "a counter the daemon left out is its default"


def test_fields_a_newer_daemon_adds_are_ignored() -> None:
    stream = Stream.from_dict({**STREAM, "added_in_a_later_version": {"x": 1}})
    assert stream.stream_id == "front"


def test_a_value_outside_a_listed_set_is_an_error() -> None:
    # A deployment pins the daemon and this client together, so the sets match;
    # the transport still reads a failure's code as a string.
    with pytest.raises(ValueError, match="state"):
        Stream.from_dict({**STREAM, "state": "hibernating"})
    assert BasicDecoder(StreamState).decode("live") == "live"


def test_a_missing_required_field_is_an_error() -> None:
    with pytest.raises(MissingField):
        Stream.from_dict({k: v for k, v in STREAM.items() if k != "stream_id"})


def test_events_decode_into_the_variant_their_type_names() -> None:
    candidate = SESSION_EVENTS.decode({"type": "candidate", "candidate": "", "sdp_mid": "0"})
    assert isinstance(candidate, SessionEventCandidate)
    assert candidate.sdp_mid == "0"
    closed = SESSION_EVENTS.decode({"type": "closed", "code": "peer_closed", "message": "m"})
    assert isinstance(closed, SessionEventClosed)
    removed = STREAM_EVENTS.decode({"type": "stream_removed", "stream_id": "front"})
    assert removed == StreamEventStreamRemoved(stream_id="front")
    state = STREAM_EVENTS.decode(
        {
            "type": "stream",
            "stream_id": "front",
            "state": "reconnecting",
            "last_error": {
                "code": "source_timeout",
                "message": "stall",
                "details": {"protocol": "rtsp"},
                "at": "2026-09-28T11:02:03.000Z",
            },
        }
    )
    assert isinstance(state, StreamEventStream)
    assert state.last_error is not None
    assert state.last_error.details == {"protocol": "rtsp"}


def test_an_event_type_this_client_does_not_know_is_an_error() -> None:
    with pytest.raises(ValueError, match="added_later"):
        SESSION_EVENTS.decode({"type": "added_later"})


def test_hello_checks_its_tag() -> None:
    hello = Hello.from_dict(
        {"type": "hello", "api": "0.1.0", "version": "0.1.0", "outputs": ["webrtc"], "features": []}
    )
    assert hello.version == "0.1.0"
    with pytest.raises(ValueError, match="type"):
        Hello.from_dict(
            {"type": "event", "api": "0.1.0", "version": "0.1.0", "outputs": [], "features": []}
        )


def test_commands_encode_like_the_contract_and_leave_out_none() -> None:
    # A `null` where the Rust type has a default but no Option is rejected
    # by the daemon (`options` of a source), so absent fields are left out.
    put = StreamPutCommand(id=1, stream_id="front", sources=[SourceSpec(url="rtsp://cam/h264")])
    assert put.to_dict() == {
        "id": 1,
        "type": "stream/put",
        "stream_id": "front",
        "sources": [{"url": "rtsp://cam/h264"}],
        "audio": "auto",
        "orientation": "no_transform",
        "preload": False,
    }
    assert StreamSubscribeCommand(id=2).to_dict() == {"id": 2, "type": "stream/subscribe"}
    offer = WebrtcOfferCommand(
        id=3,
        stream_id="front",
        sdp="v=0",
        ice_servers=[IceServer(urls=["turn:turn.example:3478"], username="u", credential="c")],
    )
    assert offer.to_dict() == {
        "id": 3,
        "type": "webrtc/offer",
        "stream_id": "front",
        "sdp": "v=0",
        "ice_servers": [{"urls": ["turn:turn.example:3478"], "username": "u", "credential": "c"}],
    }
