"""Client for Metra's schedule and realtime feeds."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from http import HTTPStatus
from time import monotonic
from typing import Any

import aiohttp
from google.protobuf.message import DecodeError
from google.transit import gtfs_realtime_pb2
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import REALTIME_BASE_URL, SCHEDULE_URL

_LOGGER = logging.getLogger(__name__)

FEED_TIMEOUT = aiohttp.ClientTimeout(total=30)
SCHEDULE_TIMEOUT = aiohttp.ClientTimeout(total=300)

# Seconds a feed is reused before asking Metra again.
FEED_TTL = {"tripupdates": 120, "positions": 120, "alerts": 300}
STALE_MAX_AGE = 3600
STALE_RETRY_DELAY = 30


class MetraError(Exception):
    """Metra's feeds could not be read."""


class MetraAuthError(MetraError):
    """Metra rejected the API key."""


@dataclass(slots=True)
class _CachedFeed:
    feed: Any
    fetched_at: float
    fresh_until: float


class MetraApi:
    """Fetches feeds, caching them and falling back to a recent copy on failure."""

    def __init__(self, hass: HomeAssistant, api_key: str) -> None:
        """Initialize the client."""
        self._hass = hass
        self._session = async_get_clientsession(hass)
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._cache: dict[str, _CachedFeed] = {}

    async def async_validate_key(self) -> None:
        """Raise MetraAuthError or MetraError if the feeds can't be read with this key."""
        await self._async_download_feed("tripupdates")

    async def async_get_feeds(self) -> tuple[Any, Any, Any]:
        """Return the trip update, position and alert feeds."""
        feeds = await asyncio.gather(
            self._async_get_feed("tripupdates"),
            self._async_get_feed("positions"),
            self._async_get_feed("alerts"),
        )
        return feeds[0], feeds[1], feeds[2]

    async def async_download_schedule(self) -> bytes:
        """Download the static schedule archive. This feed needs no API key."""
        try:
            async with self._session.get(SCHEDULE_URL, timeout=SCHEDULE_TIMEOUT) as response:
                response.raise_for_status()
                return await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MetraError(f"Could not download schedule: {err}") from err

    async def _async_get_feed(self, name: str) -> Any:
        now = monotonic()
        cached = self._cache.get(name)
        if cached is not None and now < cached.fresh_until:
            return cached.feed

        try:
            feed = await self._async_download_feed(name)
        except MetraAuthError:
            raise
        except MetraError as err:
            if cached is None or now - cached.fetched_at > STALE_MAX_AGE:
                raise
            _LOGGER.debug("Using recent copy of %s feed: %s", name, err)
            # Retry shortly rather than on every update.
            cached.fresh_until = now + STALE_RETRY_DELAY
            return cached.feed

        self._cache[name] = _CachedFeed(feed, now, now + FEED_TTL[name])
        return feed

    async def _async_download_feed(self, name: str) -> Any:
        url = f"{REALTIME_BASE_URL}/{name}"
        try:
            async with self._session.get(
                url, headers=self._headers, timeout=FEED_TIMEOUT
            ) as response:
                if response.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    raise MetraAuthError(f"Metra rejected the API key: HTTP {response.status}")
                response.raise_for_status()
                content = await response.read()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise MetraError(f"Could not read {name} feed: {err}") from err

        try:
            return await self._hass.async_add_executor_job(_parse_feed, content)
        except DecodeError as err:
            raise MetraError(f"Invalid {name} feed: {err}") from err


def _parse_feed(content: bytes) -> Any:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(content)
    return feed
