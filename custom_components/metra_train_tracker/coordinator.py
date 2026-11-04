"""Data coordinator for the Metra Train Tracker integration."""

from __future__ import annotations

import logging
import os
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_API_KEY
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.storage import STORAGE_DIR
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import MetraApi, MetraAuthError, MetraError
from .const import (
    CENTRAL,
    CONF_DIRECTION,
    CONF_FAVORITE_TRAINS,
    CONF_IGNORE_TERMS,
    CONF_LINE,
    CONF_NEXT,
    CONF_STATION,
    DEFAULT_NEXT,
    DIRECTION_IDS,
    DOMAIN,
    SCHEDULE_REFRESH_HOUR,
    SCHEDULE_RETRY_INTERVAL,
    SUBENTRY_STATION,
    UPDATE_INTERVAL,
)
from .gtfs import GtfsSchedule, ScheduleParseError, parse_schedule
from .schedule import (
    RealtimeData,
    ScheduleQuery,
    build_schedule,
    index_realtime,
    trains_at_station,
)

_LOGGER = logging.getLogger(__name__)

SCHEDULE_FILENAME = "metra_schedule.zip"

type MetraConfigEntry = ConfigEntry[MetraCoordinator]


def schedule_cache_path(hass: HomeAssistant) -> Path:
    """Return where the downloaded schedule is kept between restarts."""
    return Path(hass.config.path(STORAGE_DIR, SCHEDULE_FILENAME))


def last_refresh_boundary(now: datetime) -> datetime:
    """Return the most recent time the schedule was due for a refresh."""
    central = now.astimezone(CENTRAL)
    boundary = datetime.combine(central.date(), time(SCHEDULE_REFRESH_HOUR), tzinfo=CENTRAL)
    return boundary if central >= boundary else boundary - timedelta(days=1)


def query_from_subentry(data: dict[str, Any]) -> ScheduleQuery:
    """Convert a station's stored settings into a schedule query."""
    direction = data.get(CONF_DIRECTION)
    direction_id = DIRECTION_IDS.get(direction) if direction else None
    return ScheduleQuery(
        route_id=data[CONF_LINE],
        stop_id=data[CONF_STATION],
        direction=direction if direction_id is not None else None,
        direction_id=direction_id,
        next_trips=int(data.get(CONF_NEXT, DEFAULT_NEXT)),
        ignore_terms=tuple(data.get(CONF_IGNORE_TERMS) or ()),
        favorite_trains=tuple(data.get(CONF_FAVORITE_TRAINS) or ()),
    )


class MetraCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls realtime feeds and builds each station's payload, keyed by subentry ID."""

    config_entry: MetraConfigEntry
    schedule: GtfsSchedule
    _schedule_fetched_at: datetime

    def __init__(self, hass: HomeAssistant, entry: MetraConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=UPDATE_INTERVAL
        )
        self.api = MetraApi(hass, entry.data[CONF_API_KEY])
        self._queries = {
            subentry_id: query_from_subentry(dict(subentry.data))
            for subentry_id, subentry in entry.subentries.items()
            if subentry.subentry_type == SUBENTRY_STATION
        }
        self._retry_schedule_at: datetime | None = None

    async def _async_setup(self) -> None:
        """Load the schedule, preferring a cached copy from since the last refresh."""
        now = dt_util.utcnow()
        cached_at = await self.hass.async_add_executor_job(
            _modified_time, schedule_cache_path(self.hass)
        )
        if cached_at is not None and cached_at >= last_refresh_boundary(now):
            try:
                self.schedule = await self._async_load_cached()
            except (OSError, ScheduleParseError) as err:
                _LOGGER.warning("Ignoring unreadable cached schedule: %s", err)
            else:
                self._schedule_fetched_at = cached_at
                return
        try:
            self.schedule = await self._async_download_schedule()
        except (MetraError, OSError, ScheduleParseError) as err:
            if cached_at is None:
                raise UpdateFailed(f"No schedule available: {err}") from err
            _LOGGER.warning("Using schedule from %s; refresh failed: %s", cached_at, err)
            try:
                self.schedule = await self._async_load_cached()
            except (OSError, ScheduleParseError) as cache_err:
                raise UpdateFailed(f"No schedule available: {cache_err}") from cache_err
            self._schedule_fetched_at = cached_at
            self._retry_schedule_at = now + SCHEDULE_RETRY_INTERVAL
        else:
            self._schedule_fetched_at = now

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Refresh the schedule if due, then rebuild each station's payload."""
        await self._async_refresh_schedule_if_due()
        try:
            feeds = await self.api.async_get_feeds()
        except MetraAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MetraError as err:
            raise UpdateFailed(str(err)) from err
        return await self.hass.async_add_executor_job(self._build_all, *feeds, dt_util.utcnow())

    def _build_all(
        self, trip_updates: Any, positions: Any, alerts: Any, now: datetime
    ) -> dict[str, dict[str, Any]]:
        realtime: RealtimeData = index_realtime(trip_updates, positions, alerts)
        return {
            subentry_id: build_schedule(self.schedule, realtime, query, now)
            for subentry_id, query in self._queries.items()
        }

    async def _async_refresh_schedule_if_due(self) -> None:
        """Replace the schedule daily, keeping the current one if that fails."""
        now = dt_util.utcnow()
        if self._schedule_fetched_at >= last_refresh_boundary(now) or (
            self._retry_schedule_at is not None and now < self._retry_schedule_at
        ):
            return
        try:
            self.schedule = await self._async_download_schedule()
        except (MetraError, OSError, ScheduleParseError) as err:
            _LOGGER.warning("Could not refresh the Metra schedule, keeping current one: %s", err)
            self._retry_schedule_at = now + SCHEDULE_RETRY_INTERVAL
        else:
            self._schedule_fetched_at = now
            self._retry_schedule_at = None

    async def _async_download_schedule(self) -> GtfsSchedule:
        content = await self.api.async_download_schedule()
        # Parse first, so a bad download can't replace a good cached copy.
        schedule = await self._async_parse(content)
        await self.hass.async_add_executor_job(
            _write_atomic, schedule_cache_path(self.hass), content
        )
        return schedule

    async def async_get_station_trains(self, query: ScheduleQuery) -> dict[str, str]:
        """Return the trains to offer as favorites, or nothing if they can't be read."""
        schedule = self.schedule
        # Stop times are only kept for configured stations, so a new one needs a fresh read.
        if query.stop_id.casefold() not in schedule.stop_times_by_stop:
            try:
                content = await self.hass.async_add_executor_job(
                    schedule_cache_path(self.hass).read_bytes
                )
                schedule = await self.hass.async_add_executor_job(
                    parse_schedule, content, {query.stop_id}
                )
            except (OSError, ScheduleParseError) as err:
                _LOGGER.warning("Could not list trains at %s: %s", query.stop_id, err)
                return {}
        return await self.hass.async_add_executor_job(
            trains_at_station, schedule, query, dt_util.now(CENTRAL).date()
        )

    async def _async_load_cached(self) -> GtfsSchedule:
        path = schedule_cache_path(self.hass)
        return await self._async_parse(await self.hass.async_add_executor_job(path.read_bytes))

    async def _async_parse(self, content: bytes) -> GtfsSchedule:
        keep_stops = {query.stop_id for query in self._queries.values()}
        return await self.hass.async_add_executor_job(parse_schedule, content, keep_stops)


def _modified_time(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, CENTRAL)
    except OSError:
        return None


def _write_atomic(path: Path, content: bytes) -> None:
    temporary = path.with_suffix(".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary.write_bytes(content)
    os.replace(temporary, path)
