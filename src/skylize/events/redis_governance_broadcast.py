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
        pubsub = self._client.pubsub()
        await pubsub.subscribe(_CHANNEL)
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            data = message.get("data")
            if not data:
                continue
            await handler(GovernanceInvalidation.from_json(data))
