"""What several test modules share: the bundle, contract examples and a scripted fake daemon."""

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from aiohttp import web

from lotse_client.version import WS_PATH

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

BUNDLE = Path(__file__).resolve().parents[2] / "schema" / "api.json"
"""The JSON Schema bundle, copied from lotse, that the models are generated from."""

HELLO = {"type": "hello", "api": "0.1.0", "version": "0.0.0", "outputs": ["webrtc"], "features": []}
"""The greeting the fake daemon sends unless a test changes it."""


class FakeDaemon:
    """The daemon's side of the WebSocket, scripted by a test.

    It greets with `hello` (or whatever `hello` is set to: a dict is sent as
    JSON, `str` and `bytes` as they are), records every frame it receives,
    and answers each with `on_frame`, which by default sends a `pong` for
    `ping` and a successful `result` from `results` for everything else.
    """

    def __init__(self, path: Path) -> None:
        """Serve nothing yet."""
        self.path = path
        self.hello: object = HELLO
        self.results: dict[str, Any] = {}
        self.received: list[dict[str, Any]] = []
        self.frame_received = asyncio.Event()
        self.on_frame: Callable[[dict[str, Any]], Awaitable[None]] = self.answer
        self.ws: web.WebSocketResponse | None = None
        self.connections = 0
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        """Listen on the socket."""
        app = web.Application()
        app.router.add_get(WS_PATH, self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.UnixSite(self._runner, str(self.path)).start()

    async def stop(self) -> None:
        """Close every connection and stop listening."""
        if self._runner is not None:
            await self._runner.cleanup()

    async def _handle(self, request: web.Request) -> web.WebSocketResponse:
        ws = web.WebSocketResponse()
        await ws.prepare(request)
        self.ws = ws
        self.connections += 1
        await self.send(self.hello)
        async for message in ws:
            frame = json.loads(message.data)
            self.received.append(frame)
            self.frame_received.set()
            await self.on_frame(frame)
        return ws

    async def send(self, frame: object) -> None:
        """Send a frame: a dict as JSON, `str` and `bytes` as they are."""
        assert self.ws is not None
        if isinstance(frame, bytes):
            await self.ws.send_bytes(frame)
        elif isinstance(frame, str):
            await self.ws.send_str(frame)
        else:
            await self.ws.send_str(json.dumps(frame))

    async def answer(self, frame: dict[str, Any]) -> None:
        """`pong` for `ping`, else a successful `result` from `results`."""
        if frame["type"] == "ping":
            await self.send({"type": "pong", "id": frame["id"]})
        else:
            result = self.results.get(frame["type"], {})
            await self.send(
                {"type": "result", "id": frame["id"], "success": True, "result": result}
            )

    async def close_connection(self) -> None:
        """Close the current WebSocket from the daemon's side."""
        assert self.ws is not None
        await self.ws.close()


STREAM = {
    "stream_id": "front",
    "state": "live",
    "preload": False,
    "since": "2026-09-28T11:02:03.000Z",
    "last_error": None,
    "sources": [
        {
            "url": "rtsp://****@192.168.1.10:554/h264",
            "protocol": "rtsp",
            "state": "live",
            "reconnects": 2,
            "connection": {
                "id": "c3",
                "shared_by": ["front_duplicate"],
                "worker": {"pid": 4711, "restarts": 0, "last_crash": None, "pss_bytes": 5242880},
            },
        }
    ],
    "tracks": [
        {
            "id": "a1",
            "kind": "audio",
            "codec": "opus",
            "derived_from": "a0",
            "sync": "sender_reports",
            "audio_delay_ms": 88,
            "frames": 3,
            "bytes": 4,
        }
    ],
    "sessions": ["01J8"],
    "stats": {"keyframe_requests": 3},
}
"""`stream/get`'s example result, trimmed."""
