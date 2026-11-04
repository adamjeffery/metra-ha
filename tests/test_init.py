"""Tests for setup, the config flows, entities and feed handling."""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import (
    SOURCE_REAUTH,
    SOURCE_USER,
    ConfigEntryState,
    ConfigSubentryData,
)
from homeassistant.const import CONF_API_KEY, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.metra.const import (
    CENTRAL,
    CONF_DIRECTION,
    CONF_IGNORE_TERMS,
    CONF_LINE,
    CONF_NEXT,
    CONF_STATION,
    DOMAIN,
    REALTIME_BASE_URL,
    SCHEDULE_URL,
    SUBENTRY_STATION,
)
from custom_components.metra.coordinator import schedule_cache_path

from .conftest import alerts_feed, build_archive, empty_feed, trip_updates_feed

NOW = datetime(2026, 11, 4, 8, 0, tzinfo=CENTRAL)
FEEDS = ("tripupdates", "positions", "alerts")
STATION_DATA = {
    CONF_LINE: "UP-W",
    CONF_STATION: "LOMBARD",
    CONF_DIRECTION: "inbound",
    CONF_NEXT: 3,
    CONF_IGNORE_TERMS: ["construction"],
}
NEXT_ARRIVAL = "sensor.up_w_lombard_inbound_next_arrival"
NEXT_TRAIN = "sensor.up_w_lombard_inbound_next_train"
NEXT_DELAY = "sensor.up_w_lombard_inbound_next_train_delay"
ALERT = "binary_sensor.up_w_lombard_inbound_service_alert"


@pytest.fixture(autouse=True)
def frozen_now(freezer: FrozenDateTimeFactory) -> None:
    """Fix the time to a weekday morning."""
    freezer.move_to(NOW)


@pytest.fixture(autouse=True)
def isolated_config_dir(hass: HomeAssistant, tmp_path: Path) -> None:
    """Keep each test's cached schedule to itself."""
    hass.config.config_dir = str(tmp_path)


def mock_feeds(aioclient_mock: AiohttpClientMocker, **feeds: Any) -> None:
    """Mock the realtime feeds, each empty unless given."""
    for name in FEEDS:
        feed = feeds.get(name, empty_feed())
        aioclient_mock.get(f"{REALTIME_BASE_URL}/{name}", content=feed.SerializeToString())


def make_entry(*stations: dict[str, Any]) -> MockConfigEntry:
    """Create a config entry holding the given stations."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="Metra",
        data={CONF_API_KEY: "key"},
        subentries_data=[
            ConfigSubentryData(
                data=station,
                subentry_type=SUBENTRY_STATION,
                title=f"UP-W Lombard {station[CONF_DIRECTION].capitalize()}",
                unique_id=f"up-w_lombard_{station[CONF_DIRECTION]}",
            )
            for station in stations
        ],
    )


async def setup_entry(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    *stations: dict[str, Any],
    **feeds: Any,
) -> MockConfigEntry:
    """Set up the integration with mocked feeds."""
    aioclient_mock.get(SCHEDULE_URL, content=build_archive())
    mock_feeds(aioclient_mock, **feeds)
    entry = make_entry(*(stations or (STATION_DATA,)))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities_from_realtime_data(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A station gets sensors for its next train and alerts."""
    late = int(datetime(2026, 11, 4, 9, 7, tzinfo=CENTRAL).timestamp())
    entry = await setup_entry(
        hass,
        aioclient_mock,
        tripupdates=trip_updates_feed(("UP-W_UW38_V1_A", "LOMBARD", late, None)),
        alerts=alerts_feed(
            ("Station construction", "Hidden", [("UP-W", "")]),
            ("Signal problem", "Shown", [("UP-W", "")]),
        ),
    )
    assert entry.state is ConfigEntryState.LOADED

    assert hass.states.get(NEXT_ARRIVAL).state == "2026-11-04T15:07:00+00:00"
    assert hass.states.get(NEXT_TRAIN).state == "38"
    assert hass.states.get(NEXT_DELAY).state == "3"

    alert = hass.states.get(ALERT)
    assert alert.state == "on"
    assert alert.attributes["alerts"] == [{"header": "Signal problem", "description": "Shown"}]

    # The attributes the card reads.
    attributes = hass.states.get(NEXT_ARRIVAL).attributes
    assert attributes["filters"]["route"] == "UP-W"
    assert [trip["train"] for trip in attributes["routes"][0]["trips"]] == ["38", "42", "99"]
    assert attributes["generatedAt"] == "2026-11-04T08:00:00-06:00"


async def test_large_attributes_not_recorded(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The schedule stays out of the history database, replacing a recorder exclude."""
    await setup_entry(hass, aioclient_mock)
    entity = hass.data["entity_components"]["sensor"].get_entity(NEXT_ARRIVAL)
    assert {"routes", "generatedAt", "filters"} <= entity._state_info["unrecorded_attributes"]


async def test_no_trains_gives_unknown_not_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """With nothing left to show the entities stay available with no value."""
    freezer.move_to(datetime(2026, 11, 7, 13, 0, tzinfo=CENTRAL))  # After Saturday's only train
    await setup_entry(hass, aioclient_mock)
    assert hass.states.get(NEXT_ARRIVAL).state == "unknown"
    assert hass.states.get(NEXT_ARRIVAL).attributes["routes"] == []
    assert hass.states.get(ALERT).state == "off"


async def test_feed_failure_uses_recent_copy_then_goes_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """A brief outage is invisible; a long one makes entities unavailable."""
    entry = await setup_entry(hass, aioclient_mock)
    assert hass.states.get(NEXT_TRAIN).state == "38"

    aioclient_mock.clear_requests()
    for name in FEEDS:
        aioclient_mock.get(f"{REALTIME_BASE_URL}/{name}", status=500)

    freezer.tick(timedelta(minutes=10))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(NEXT_TRAIN).state == "38"

    freezer.tick(timedelta(hours=1))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get(NEXT_TRAIN).state == STATE_UNAVAILABLE


async def test_feeds_not_refetched_within_ttl(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """A poll inside the cache time doesn't call Metra again."""
    entry = await setup_entry(hass, aioclient_mock)
    feed_calls = aioclient_mock.call_count - 1  # minus the schedule download
    assert feed_calls == len(FEEDS)

    freezer.tick(timedelta(seconds=61))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert aioclient_mock.call_count - 1 == feed_calls

    freezer.tick(timedelta(seconds=60))  # Past the trip update and position TTLs.
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert aioclient_mock.call_count - 1 == feed_calls + 2


async def test_rejected_key_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A 401 asks the user for a new key instead of retrying forever."""
    aioclient_mock.get(SCHEDULE_URL, content=build_archive())
    for name in FEEDS:
        aioclient_mock.get(f"{REALTIME_BASE_URL}/{name}", status=401)
    entry = make_entry(STATION_DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress()
    assert [flow["context"]["source"] for flow in flows] == [SOURCE_REAUTH]


async def test_setup_retries_when_schedule_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With no schedule, and no cached copy, setup retries later."""
    aioclient_mock.get(SCHEDULE_URL, status=500)
    mock_feeds(aioclient_mock)
    entry = make_entry(STATION_DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_schedule_cached_between_restarts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A reload the same day reads the cached file instead of downloading."""
    entry = await setup_entry(hass, aioclient_mock)
    path = schedule_cache_path(hass)
    assert path.exists()
    # Freezing time doesn't affect the file's modified time.
    os.utime(path, (NOW.timestamp(), NOW.timestamp()))

    aioclient_mock.clear_requests()
    aioclient_mock.get(SCHEDULE_URL, status=500)
    mock_feeds(aioclient_mock)
    await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert not [call for call in aioclient_mock.mock_calls if str(call[1]) == SCHEDULE_URL]


async def test_old_cached_schedule_used_when_download_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """An outdated schedule beats none when Metra is unreachable at startup."""
    path = schedule_cache_path(hass)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(build_archive())
    old = (NOW - timedelta(days=3)).timestamp()
    os.utime(path, (old, old))

    aioclient_mock.get(SCHEDULE_URL, status=500)
    mock_feeds(aioclient_mock)
    entry = make_entry(STATION_DATA)
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get(NEXT_TRAIN).state == "38"


async def test_schedule_refreshes_daily_and_survives_failure(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    """The next 5 AM refresh keeps the old schedule on failure, then retries in an hour."""
    entry = await setup_entry(hass, aioclient_mock)

    aioclient_mock.clear_requests()
    aioclient_mock.get(SCHEDULE_URL, status=500)
    mock_feeds(aioclient_mock)
    freezer.move_to(datetime(2026, 11, 5, 5, 1, tzinfo=CENTRAL))
    await entry.runtime_data.async_refresh()
    assert aioclient_mock.call_count > 0
    downloads = [c for c in aioclient_mock.mock_calls if str(c[1]) == SCHEDULE_URL]
    assert len(downloads) == 1
    assert hass.states.get(NEXT_TRAIN).state != STATE_UNAVAILABLE

    # Not again until the retry time.
    freezer.tick(timedelta(minutes=5))
    await entry.runtime_data.async_refresh()
    downloads = [c for c in aioclient_mock.mock_calls if str(c[1]) == SCHEDULE_URL]
    assert len(downloads) == 1

    # A good download after the retry time is stored for later.
    aioclient_mock.clear_requests()
    aioclient_mock.get(SCHEDULE_URL, content=build_archive())
    mock_feeds(aioclient_mock)
    freezer.tick(timedelta(hours=1))
    await entry.runtime_data.async_refresh()
    downloads = [c for c in aioclient_mock.mock_calls if str(c[1]) == SCHEDULE_URL]
    assert len(downloads) == 1
    assert entry.runtime_data._retry_schedule_at is None


async def test_remove_deletes_cached_schedule(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Removing the integration leaves nothing behind."""
    entry = await setup_entry(hass, aioclient_mock)
    path: Path = schedule_cache_path(hass)
    assert path.exists()
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert not path.exists()


# Config flow.


@pytest.mark.parametrize(
    ("status", "error"), [(401, "invalid_auth"), (403, "invalid_auth"), (500, "cannot_connect")]
)
async def test_user_flow_errors_then_success(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int, error: str
) -> None:
    """The key is checked against Metra, and the form allows another attempt."""
    aioclient_mock.get(f"{REALTIME_BASE_URL}/tripupdates", status=status)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "bad"}
    )
    assert result["errors"] == {"base": error}

    aioclient_mock.clear_requests()
    aioclient_mock.get(SCHEDULE_URL, content=build_archive())
    mock_feeds(aioclient_mock)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "good"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: "good"}
    assert aioclient_mock.mock_calls[0][3]["Authorization"] == "Bearer good"


async def test_only_one_entry_allowed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Stations share one entry, so a second is refused."""
    await setup_entry(hass, aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "single_instance_allowed"


async def test_reauth_flow_replaces_key(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A new key is saved and the integration reloads."""
    entry = await setup_entry(hass, aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: "new"}
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "new"


async def _start_station_flow(
    hass: HomeAssistant, entry: MockConfigEntry, line: str = "UP-W"
) -> dict[str, Any]:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, SUBENTRY_STATION), context={"source": SOURCE_USER}
    )
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_LINE: line}
    )
    assert result["step_id"] == "station"
    return result


async def test_add_station_creates_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Adding a station from the flow creates its entities without a restart."""
    aioclient_mock.get(SCHEDULE_URL, content=build_archive())
    mock_feeds(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, title="Metra", data={CONF_API_KEY: "key"})
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.async_all("sensor") == []

    result = await _start_station_flow(hass, entry)
    # Only stations on the chosen line are offered.
    options = result["data_schema"].schema[CONF_STATION].config["options"]
    assert [option["label"] for option in options] == ["Chicago OTC", "Lombard", "Wheaton"]

    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            CONF_STATION: "LOMBARD",
            CONF_DIRECTION: "inbound",
            CONF_NEXT: 2.0,
            CONF_IGNORE_TERMS: ["construction"],
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "UP-W Lombard Inbound"
    assert result["data"][CONF_NEXT] == 2

    assert hass.states.get(NEXT_TRAIN).state == "38"
    assert len(hass.states.get(NEXT_ARRIVAL).attributes["routes"][0]["trips"]) == 2


async def test_duplicate_station_rejected(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The same line, station and direction can't be added twice."""
    entry = await setup_entry(hass, aioclient_mock)
    subentry = next(iter(entry.subentries.values()))
    hass.config_entries.async_update_subentry(entry, subentry, unique_id="up-w_lombard_inbound")
    await hass.async_block_till_done()

    result = await _start_station_flow(hass, entry)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_STATION: "LOMBARD", CONF_DIRECTION: "inbound", CONF_NEXT: 3},
    )
    assert result["errors"] == {"base": "already_configured"}


async def test_reconfigure_station(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """Changing a station keeps its entities and applies the new settings."""
    entry = await setup_entry(hass, aioclient_mock)
    subentry = next(iter(entry.subentries.values()))

    result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
    assert result["step_id"] == "user"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"], {CONF_LINE: "UP-W"}
    )
    assert result["step_id"] == "station"
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {CONF_STATION: "LOMBARD", CONF_DIRECTION: "outbound", CONF_NEXT: 1},
    )
    await hass.async_block_till_done()
    assert result["reason"] == "reconfigure_successful"

    updated = entry.subentries[subentry.subentry_id]
    assert updated.title == "UP-W Lombard Outbound"
    assert updated.data[CONF_DIRECTION] == "outbound"
    assert hass.states.get(NEXT_TRAIN).state == "39"


async def test_removing_station_removes_entities(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entity_registry: er.EntityRegistry
) -> None:
    """Deleting a station deletes its entities."""
    entry = await setup_entry(hass, aioclient_mock)
    subentry = next(iter(entry.subentries.values()))
    assert entity_registry.async_get(NEXT_TRAIN) is not None

    hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get(NEXT_TRAIN) is None


async def test_card_is_served(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_client_no_auth: Any
) -> None:
    """The card is available with no manual install, and registered with a version."""
    assert await async_setup_component(hass, "http", {})
    with patch("custom_components.metra.add_extra_js_url") as add_js:
        await setup_entry(hass, aioclient_mock)
    url = add_js.call_args.args[1]
    assert url.startswith("/metra_static/metra-card.js?v=")

    client = await hass_client_no_auth()
    response = await client.get(url)
    assert response.status == 200
    text = await response.text()
    assert 'customElements.define("metra-card"' in text
    # Must not register until the frontend's final registry exists: see the card's comment.
    assert text.index('whenDefined("home-assistant")') < text.index(
        'customElements.define("metra-card"'
    )
