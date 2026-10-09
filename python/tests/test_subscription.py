"""`Subscription` on its own: queueing, ending, unsubscribing."""

from lotse_client import ConnectionClosedError
from lotse_client.models import StreamEventStreamRemoved
from lotse_client.subscription import Subscription

REMOVED = StreamEventStreamRemoved(stream_id="front")


async def test_events_after_the_end_are_ignored_and_ending_twice_keeps_the_first() -> None:
    sent: list[int] = []

    async def unsubscribe(subscription_id: int) -> None:
        sent.append(subscription_id)

    subscription: Subscription[StreamEventStreamRemoved] = Subscription(unsubscribe)
    subscription.id = 7
    subscription.deliver(REMOVED)
    subscription.finish(None)
    subscription.finish(ConnectionClosedError("later"))
    subscription.deliver(REMOVED)
    assert [e async for e in subscription] == [REMOVED]
    await subscription.unsubscribe()
    assert sent == [], "an ended subscription needs no unsubscribe"


async def test_unsubscribe_on_a_dropped_connection_still_ends_it() -> None:
    async def unsubscribe(_: int) -> None:
        msg = "gone"
        raise ConnectionClosedError(msg)

    subscription: Subscription[StreamEventStreamRemoved] = Subscription(unsubscribe)
    await subscription.unsubscribe()
    assert subscription.ended
    assert [e async for e in subscription] == []
