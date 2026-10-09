"""A subscription: the events the daemon pushes after a subscribing command.

`stream/subscribe`, `webrtc/offer` and `session/adopt` subscribe; the
command's id is the subscription's id, and every `event` frame carrying it
belongs here. The daemon sends no
frame when a subscription ends on its side, except that a session's
`closed` event is always its last, so iteration stops after an event of
type `closed`, after `unsubscribe`, or with `ConnectionClosedError` when
the connection drops.
"""

import asyncio
from typing import TYPE_CHECKING, Self

from lotse_client.errors import ConnectionClosedError, LotseError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from types import TracebackType


class Subscription[E]:
    """The events of one subscription, as an async iterator.

    Events are queued as they arrive, so none are lost between the
    subscribing command's result and the first `__anext__`. Use it as an
    async context manager to unsubscribe when leaving the block.
    """

    def __init__(self, unsubscribe: Callable[[int], Awaitable[None]]) -> None:
        """Start empty; `unsubscribe` sends the `unsubscribe` command for an id."""
        self.id = 0
        """The subscribing command's id, set by the transport as it sends the command."""
        self._unsubscribe = unsubscribe
        self._queue: asyncio.Queue[E | None] = asyncio.Queue()
        """The events, then `None` after the last."""
        self._ended = False
        self._unsubscribing = False
        self._error: LotseError | None = None

    @property
    def ended(self) -> bool:
        """No event will be added: the last one arrived, or the subscription was ended."""
        return self._ended

    def deliver(self, event: E) -> None:
        """Queue an event; one of type `closed` is the last. Called by the transport."""
        if self._ended:
            return
        self._queue.put_nowait(event)
        if getattr(event, "type", None) == "closed":
            self.finish(None)

    def finish(self, error: LotseError | None) -> None:
        """End iteration once the queued events are read, then raise `error` if set.

        Called by the transport when the subscription ends without
        `unsubscribe`: a failed subscribing command, or the connection ending.
        """
        if self._ended:
            return
        self._ended = True
        self._error = error
        self._queue.put_nowait(None)

    def __aiter__(self) -> Self:
        """Iterate over the events."""
        return self

    async def __anext__(self) -> E:
        """The next event; stops at the end, raises if the connection dropped."""
        item = await self._queue.get()
        if item is None:
            self._queue.put_nowait(None)
            if self._error is not None:
                raise self._error
            raise StopAsyncIteration
        return item

    async def unsubscribe(self) -> None:
        """End the subscription on the daemon, then here.

        An event the daemon sends before the `unsubscribe` result (a
        session's last state, for one) is still queued. Does nothing once
        the subscription has ended or while another call is unsubscribing.
        """
        if self._ended or self._unsubscribing:
            return
        self._unsubscribing = True
        try:
            await self._unsubscribe(self.id)
        except ConnectionClosedError:
            pass
        finally:
            self.finish(None)

    async def __aenter__(self) -> Self:
        """Use it as a context manager."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Unsubscribe when leaving the block."""
        await self.unsubscribe()
