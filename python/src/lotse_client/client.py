"""The WebSocket transport to the daemon's control socket.

One long-lived WebSocket over the Unix socket, at `/v0/ws`: the daemon
greets with `hello`, every command carries a strictly increasing `id` and
gets exactly one `result` (`pong` for `ping`), and a subscribing command's
`event`s carry its id. The typed command methods and their namespaces
(`client.stream`, ...) come from the generated `Commands` base class; this
module only moves frames.
"""

import asyncio
import logging
import os
from typing import TYPE_CHECKING, Any, Self

import aiohttp
import orjson

from lotse_client._commands import Commands
from lotse_client.errors import (
    ApiVersionError,
    CannotConnectError,
    CommandError,
    ConnectionClosedError,
    LotseError,
    ProtocolError,
)
from lotse_client.models import Hello
from lotse_client.subscription import Subscription
from lotse_client.version import API_VERSION, WS_PATH, accepts

if TYPE_CHECKING:
    from types import TracebackType

    from mashumaro.codecs.basic import BasicDecoder

    from lotse_client._base import Model

type Frame = dict[str, Any]
"""One JSON object received or sent on the WebSocket."""

_LOGGER = logging.getLogger(__package__)

WS_URL = f"http://lotse{WS_PATH}"
"""The upgrade URL; the host is ignored on a Unix socket."""

_DECODE_ERRORS = (LookupError, TypeError, ValueError)
"""What mashumaro raises for a payload that does not fit the type."""


def _id_of(frame: Frame) -> int | None:
    """A frame's command id; `None` when it has none (an unaddressed failure)."""
    command_id = frame.get("id")
    return command_id if type(command_id) is int else None


class LotseClient(Commands):
    """A connection to the lotse daemon's control API.

    Use it as an async context manager, or call `connect` and `close`::

        async with LotseClient("/run/lotse/lotse.sock") as client:
            info = await client.info()
            async with await client.stream.subscribe() as events:
                async for event in events:
                    ...

    Without a `session`, the client opens one on the socket and closes it
    with the connection. A `session` passed in must already be bound to
    the socket (an `aiohttp.UnixConnector`), and stays open.
    """

    def __init__(
        self, socket_path: str | os.PathLike[str], *, session: aiohttp.ClientSession | None = None
    ) -> None:
        """Prepare a client; nothing is opened until `connect`."""
        super().__init__()
        self._socket_path = os.fspath(socket_path)
        self._session = session
        self._owns_session = session is None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._hello: Hello | None = None
        self._next_id = 1
        self._send_lock = asyncio.Lock()
        self._pending: dict[int, asyncio.Future[Frame]] = {}
        self._subscriptions: dict[int, tuple[Subscription[Any], BasicDecoder[Any]]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._closing = False
        self._shutdown = False
        self._closed: asyncio.Future[LotseError | None] | None = None

    @property
    def connected(self) -> bool:
        """The WebSocket is open."""
        return self._ws is not None

    @property
    def hello(self) -> Hello:
        """The daemon's greeting on the current connection."""
        if self._hello is None:
            msg = "not connected"
            raise ConnectionClosedError(msg)
        return self._hello

    async def connect(self) -> Hello:
        """Open the WebSocket and read `hello`; fails unless this client accepts its API version."""
        if self._ws is not None:
            msg = "already connected"
            raise LotseError(msg)
        if self._session is None:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.UnixConnector(path=self._socket_path)
            )
        try:
            ws = await self._session.ws_connect(WS_URL)
        except (aiohttp.ClientError, OSError) as err:
            await self._close_session()
            msg = f"cannot open {self._socket_path}: {err}"
            raise CannotConnectError(msg) from err
        try:
            hello = await self._read_hello(ws)
        except LotseError:
            await ws.close()
            await self._close_session()
            raise
        _LOGGER.debug("connected to lotse %s, API %s", hello.version, hello.api)
        self._ws = ws
        self._hello = hello
        self._next_id = 1
        self._closing = False
        self._shutdown = False
        self._closed = asyncio.get_running_loop().create_future()
        self._reader = asyncio.create_task(self._read(ws, self._closed), name="lotse-client-reader")
        return hello

    @staticmethod
    async def _read_hello(ws: aiohttp.ClientWebSocketResponse) -> Hello:
        """The first frame, which must be `hello` of this client's API version."""
        message = await ws.receive()
        if message.type is not aiohttp.WSMsgType.TEXT:
            msg = f"expected hello, got a {message.type.name} frame"
            raise ProtocolError(msg)
        try:
            hello = Hello.from_dict(orjson.loads(message.data))
        except (orjson.JSONDecodeError, *_DECODE_ERRORS) as err:
            msg = f"expected hello, got {message.data[:200]!r}"
            raise ProtocolError(msg) from err
        if not accepts(hello.api):
            raise ApiVersionError(hello.api, API_VERSION)
        return hello

    async def close(self) -> None:
        """Close the connection; pending commands fail, subscriptions stop."""
        ws = self._ws
        if ws is not None:
            self._closing = True
            await ws.close()
        if self._reader is not None:
            await self._reader
            self._reader = None
        await self._close_session()

    async def wait_closed(self) -> LotseError | None:
        """Wait until the connection ends; the error that ended it, `None` after `close`."""
        if self._closed is None:
            msg = "never connected"
            raise ConnectionClosedError(msg)
        return await asyncio.shield(self._closed)

    async def _close_session(self) -> None:
        """Close the session this client opened."""
        if self._owns_session and self._session is not None:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        """Connect."""
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Close."""
        await self.close()

    async def _request(self, command: Model) -> Frame:
        """Send `command`; return its `result`, or `{}` for `pong`."""
        return await self._send(command, None)

    async def _subscribe[E](self, command: Model, events: BasicDecoder[E]) -> Subscription[E]:
        """Send a subscribing `command`; decode its events with `events`.

        The subscription is registered before the command is sent, so the
        events that follow the result are queued however soon they come.
        """
        subscription: Subscription[E] = Subscription(self._unsubscribe_id)
        await self._send(command, (subscription, events))
        return subscription

    async def _unsubscribe_id(self, subscription_id: int) -> None:
        """Send `unsubscribe` for a subscription and forget it."""
        try:
            await self.unsubscribe(subscription=subscription_id)
        finally:
            self._subscriptions.pop(subscription_id, None)

    async def _send(
        self, command: Model, subscription: tuple[Subscription[Any], BasicDecoder[Any]] | None
    ) -> Frame:
        """Send `command` with the next id and wait for its answer.

        Ids are assigned and sent under one lock, so they reach the daemon
        strictly increasing however many tasks send at once. A
        `subscription` gets the id and is registered before the frame goes
        out; a failed subscribing command forgets it.
        """
        ws = self._ws
        if ws is None or self._closing:
            msg = "not connected"
            raise ConnectionClosedError(msg)
        async with self._send_lock:
            command_id = self._next_id
            self._next_id += 1
            future: asyncio.Future[Frame] = asyncio.get_running_loop().create_future()
            self._pending[command_id] = future
            if subscription is not None:
                subscription[0].id = command_id
                self._subscriptions[command_id] = subscription
            frame = command.to_dict()
            frame["id"] = command_id
            try:
                await ws.send_str(orjson.dumps(frame).decode())
            except (aiohttp.ClientError, ConnectionError) as err:
                self._pending.pop(command_id, None)
                self._subscriptions.pop(command_id, None)
                msg = f"sending failed: {err}"
                raise ConnectionClosedError(msg) from err
        try:
            return await future
        except BaseException:
            self._pending.pop(command_id, None)
            self._subscriptions.pop(command_id, None)
            raise

    async def _read(
        self, ws: aiohttp.ClientWebSocketResponse, closed: asyncio.Future[LotseError | None]
    ) -> None:
        """Dispatch frames until the connection ends, then fail what still waits.

        `closed` gets the error that ended the connection, `None` after `close`.
        """
        error: LotseError | None = None
        try:
            async for message in ws:
                if message.type is aiohttp.WSMsgType.TEXT:
                    try:
                        frame = orjson.loads(message.data)
                    except orjson.JSONDecodeError:
                        error = ProtocolError(f"not JSON: {message.data[:200]!r}")
                        break
                    self._dispatch(frame)
                elif message.type is aiohttp.WSMsgType.ERROR:
                    error = ConnectionClosedError(f"connection failed: {ws.exception()}")
                    break
                else:
                    error = ProtocolError(f"unexpected {message.type.name} frame")
                    break
        finally:
            await ws.close()
            if error is None and not self._closing:
                reason = (
                    "the daemon shut down" if self._shutdown else "the daemon closed the connection"
                )
                error = ConnectionClosedError(reason)
            self._finish(error)
            closed.set_result(error)

    def _finish(self, error: LotseError | None) -> None:
        """Fail pending commands and end subscriptions; `error` is `None` after `close`."""
        if error is not None:
            _LOGGER.warning("connection to lotse ended: %s", error)
        self._ws = None
        self._hello = None
        pending, self._pending = self._pending, {}
        for future in pending.values():
            if not future.done():
                future.set_exception(error or ConnectionClosedError("connection closed"))
        subscriptions, self._subscriptions = self._subscriptions, {}
        for subscription, _ in subscriptions.values():
            subscription.finish(error)

    def _dispatch(self, frame: object) -> None:
        """Route one frame by its `type`; unknown types are ignored for newer daemons."""
        if not isinstance(frame, dict):
            _LOGGER.warning("ignoring a frame that is not an object: %r", frame)
            return
        kind = frame.get("type")
        if kind == "result":
            self._answer(frame)
        elif kind == "pong":
            command_id = _id_of(frame)
            future = None if command_id is None else self._pending.pop(command_id, None)
            if future is not None and not future.done():
                future.set_result({})
        elif kind == "event":
            self._event(frame)
        elif kind == "shutdown":
            _LOGGER.info("lotse is shutting down")
            self._shutdown = True
        else:
            _LOGGER.debug("ignoring a frame of type %r", kind)

    def _answer(self, frame: Frame) -> None:
        """Resolve the command a `result` answers."""
        command_id = _id_of(frame)
        future = None if command_id is None else self._pending.pop(command_id, None)
        if command_id is None or future is None:
            _LOGGER.warning("ignoring a result for no pending command: %r", frame)
            return
        if future.done():
            return
        if frame.get("success") is True:
            result = frame.get("result")
            if isinstance(result, dict):
                future.set_result(result)
            else:
                future.set_exception(ProtocolError(f"a result that is not an object: {frame!r}"))
            return
        error = frame.get("error")
        code = error.get("code") if isinstance(error, dict) else None
        message = error.get("message") if isinstance(error, dict) else None
        details = error.get("details", {}) if isinstance(error, dict) else None
        if (
            not isinstance(code, str)
            or not isinstance(message, str)
            or not isinstance(details, dict)
        ):
            future.set_exception(ProtocolError(f"unreadable failure: {frame!r}"))
            return
        subscription = self._subscriptions.pop(command_id, None)  # a failed subscribe
        if subscription is not None:
            subscription[0].finish(None)
        future.set_exception(CommandError(code, message, details))

    def _event(self, frame: Frame) -> None:
        """Queue an event on its subscription; one this client cannot read is logged and dropped."""
        command_id = _id_of(frame)
        entry = None if command_id is None else self._subscriptions.get(command_id)
        if entry is None:
            _LOGGER.debug("ignoring an event for no subscription: %r", frame)
            return
        subscription, events = entry
        try:
            event = events.decode(frame["event"])
        except _DECODE_ERRORS as err:
            _LOGGER.warning("dropping an event this client cannot read: %r: %s", frame, err)
            return
        subscription.deliver(event)
        if subscription.ended:
            self._subscriptions.pop(subscription.id, None)
