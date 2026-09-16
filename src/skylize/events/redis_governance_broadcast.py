"""
Redis Pub/Sub implementation of the GovernanceBroadcast port.

Redis Pub/Sub fans every published message out to ALL subscribers (not
one-of-group like Streams), which is exactly the semantics governance
invalidation needs: a kill/revoke on one instance must reach every instance's
snapshot. It is fire-and-forget and self-healing — the DB stays the system of
record and the Authority re-reads it on restart (rehydrate), so a missed message
during a brief disconnect is corrected at the next warm-up.
"""

from __future__ import annotations

import redis.asyncio as redis

from ..app.governance.broadcast import GovernanceInvalidation, InvalidationHandler

_CHANNEL = "skylize:governance:invalidation"


class RedisGovernanceBroadcast:
    def __init__(self, url: str) -> None:
        self._client: redis.Redis = redis.from_url(url, decode_responses=True)

    async def close(self) -> None:
        await self._client.aclose()

    async def publish(self, msg: GovernanceInvalidation) -> None:
        await self._client.publish(_CHANNEL, msg.to_json())

    async def subscribe(self, handler: InvalidationHandler) -> None:
        # `async with` because CANCELLATION IS THE ONLY WAY THIS EVER RETURNS:
        # bootstrap runs it as a background task and stops it by cancelling
        # (`_stop_subscriber`). An `async with` body is unwound on CancelledError
        # like any other exception, so the pubsub is torn down deterministically
        # on exactly the path that matters.
        #
        # Without it the pubsub was ABANDONED mid-read rather than closed: the
        # dedicated connection stayed checked out and still in subscriber mode on
        # the server after `_stop_subscriber` had returned. That made
        # `Container.aclose`'s LIFO invariant -- consumers stop FIRST, pools close
        # last (bootstrap.py, commit 9506c72) -- only half true. The consumer had
        # not actually stopped, so the `redis_broadcast.close()` that ran next
        # tore the socket out from under a live blocked read. `PubSub.aclose()`
        # disconnects (nowait=True, so it cannot deadlock against this reader) and
        # releases the connection back to the pool, so by the time the pool closes
        # there is no in-flight read left to race with.
        async with self._client.pubsub() as pubsub:
            await pubsub.subscribe(_CHANNEL)
            async for message in pubsub.listen():
                if message.get("type") != "message":
                    continue
                data = message.get("data")
                if not data:
                    continue
                await handler(GovernanceInvalidation.from_json(data))
