"""Fixtures for the Metra tests: a small GTFS archive and realtime feed builders."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Generator
from unittest.mock import patch

import pytest
from google.transit import gtfs_realtime_pb2

ROUTES = """route_id, route_short_name, route_long_name, route_desc, agency_id, route_type, route_color, route_text_color
UP-W, UP-W, Union Pacific West, , METRA, 2, FE8D81, 000000
BNSF, BNSF, Burlington Northern, , METRA, 2, ,
"""
STOPS = """stop_id, stop_name, stop_desc, stop_lat, stop_lon
LOMBARD, Lombard, ,41.88,-88.01
WHEATON, Wheaton, ,41.86,-88.10
OTC, Chicago OTC, ,41.88,-87.64
AURORA, Aurora, ,41.76,-88.31
"""
TRIPS = """route_id, service_id, trip_id, trip_headsign, block_id, shape_id, direction_id
UP-W, WK, UP-W_UW42_V1_A, Chicago OTC, , S1, 1
UP-W, WK, UP-W_UW38_V1_A, Chicago OTC, , S1, 1
UP-W, WK, UP-W_UW39_V1_A, Elburn, , S2, 0
UP-W, WK, UP-W_UW99_V1_A, Chicago OTC, , S1, 1
UP-W, WE, UP-W_UW50_V1_A, Chicago OTC, , S1, 1
BNSF, WK, BNSF_BN1_V1_A, Chicago Union Station, , S3, 1
"""
STOP_TIMES = """trip_id, arrival_time, departure_time, stop_id, stop_sequence
UP-W_UW42_V1_A,  09:30:00,  09:30:00,  WHEATON,  1
UP-W_UW42_V1_A,  10:04:00,  10:05:00,  LOMBARD,  2
UP-W_UW42_V1_A,  10:53:00,  10:53:00,  OTC,  3
UP-W_UW38_V1_A,  08:25:00,  08:25:00,  WHEATON,  1
UP-W_UW38_V1_A,  09:04:00,  09:05:00,  LOMBARD,  2
UP-W_UW38_V1_A,  09:53:00,  09:53:00,  OTC,  3
UP-W_UW39_V1_A,  09:10:00,  09:10:00,  LOMBARD,  1
UP-W_UW39_V1_A,  09:40:00,  09:40:00,  WHEATON,  2
UP-W_UW99_V1_A,  24:50:00,  24:50:00,  WHEATON,  1
UP-W_UW99_V1_A,  25:30:00,  25:30:00,  LOMBARD,  2
UP-W_UW99_V1_A,  26:10:00,  26:10:00,  OTC,  3
UP-W_UW50_V1_A,  12:00:00,  12:00:00,  LOMBARD,  1
BNSF_BN1_V1_A,  09:00:00,  09:00:00,  AURORA,  1
"""
CALENDAR = """service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date
WK, 1, 1, 1, 1, 1, 0, 0, 20200101, 20301231
WE, 0, 0, 0, 0, 0, 1, 1, 20200101, 20301231
"""
CALENDAR_DATES = """service_id, date, exception_type
WK, 20261126, 2
WE, 20261126, 1
"""


def build_archive(**overrides: str) -> bytes:
    """Build a GTFS zip, with files replaced by name such as trips="..."."""
    files = {
        "routes": ROUTES,
        "stops": STOPS,
        "trips": TRIPS,
        "stop_times": STOP_TIMES,
        "calendar": CALENDAR,
        "calendar_dates": CALENDAR_DATES,
    } | overrides
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(f"{name}.txt", content)
    return buffer.getvalue()


def trip_updates_feed(
    *updates: tuple[str, str, int | None, int | None],
) -> gtfs_realtime_pb2.FeedMessage:
    """Build a feed from (trip_id, stop_id, arrival, departure) POSIX times."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for index, (trip_id, stop_id, arrival, departure) in enumerate(updates):
        entity = feed.entity.add(id=str(index))
        entity.trip_update.trip.trip_id = trip_id
        stop = entity.trip_update.stop_time_update.add(stop_id=stop_id)
        if arrival is not None:
            stop.arrival.time = arrival
        if departure is not None:
            stop.departure.time = departure
    return feed


def alerts_feed(
    *alerts: tuple[str, str, list[tuple[str, str]]],
) -> gtfs_realtime_pb2.FeedMessage:
    """Build a feed from (header, description, [(route_id, trip_id)]) alerts."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for index, (header, description, targets) in enumerate(alerts):
        entity = feed.entity.add(id=str(index))
        entity.alert.header_text.translation.add(text=header)
        entity.alert.description_text.translation.add(text=description)
        for route_id, trip_id in targets:
            informed = entity.alert.informed_entity.add(route_id=route_id)
            if trip_id:
                informed.trip.trip_id = trip_id
    return feed


def empty_feed() -> gtfs_realtime_pb2.FeedMessage:
    """Build a valid feed with no entities."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    return feed


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Allow Home Assistant to load the integration under test."""


@pytest.fixture(autouse=True)
def skip_card_setup() -> Generator[None]:
    """Skip frontend startup; tests that need the card serve it themselves."""
    with patch("custom_components.metra_train_tracker.add_extra_js_url"):
        yield
