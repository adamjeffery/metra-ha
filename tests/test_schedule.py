"""Tests for the schedule logic, ported from the original .NET service's tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.metra_train_tracker.const import CENTRAL
from custom_components.metra_train_tracker.gtfs import ScheduleParseError, parse_gtfs_time, parse_schedule
from custom_components.metra_train_tracker.schedule import (
    RealtimeData,
    ScheduleQuery,
    build_schedule,
    delay_minutes,
    extract_train,
    index_realtime,
)

from .conftest import alerts_feed, build_archive, empty_feed, trip_updates_feed

# A Wednesday.
NOW = datetime(2026, 11, 4, 8, 0, tzinfo=CENTRAL)
INBOUND = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1)
STOPS = {"LOMBARD", "AURORA"}


def posix(hour: int, minute: int, second: int = 0, day: int = 4) -> int:
    """Return the POSIX time for a Central time in November 2026."""
    return int(datetime(2026, 11, day, hour, minute, second, tzinfo=CENTRAL).timestamp())


def realtime(trip_updates=None, alerts=None) -> RealtimeData:
    """Index feeds, defaulting to empty ones."""
    return index_realtime(trip_updates or empty_feed(), empty_feed(), alerts or empty_feed())


def build(query=INBOUND, now=NOW, data=None, archive=None):
    """Build a payload from the fixture schedule."""
    schedule = parse_schedule(archive or build_archive(), STOPS)
    return build_schedule(schedule, data or realtime(), query, now)


def trains(payload) -> list[str]:
    """Return the payload's train numbers in order."""
    return [trip["train"] for route in payload["routes"] for trip in route["trips"]]


def test_combines_schedule_and_realtime() -> None:
    """A trip carries its schedule, route details and realtime delay."""
    feed = trip_updates_feed(("UP-W_UW38_V1_A", "LOMBARD", posix(9, 7), None))
    payload = build(data=realtime(feed))

    route = payload["routes"][0]
    assert route["routeShortName"] == "UP-W"
    assert route["routeLongName"] == "Union Pacific West"
    trip = route["trips"][0]
    assert trip["train"] == "38"
    assert trip["tripHeadsign"] == "Chicago OTC"
    assert trip["direction"] == "inbound"
    assert trip["scheduledStartTime"] == "2026-11-04T08:25:00-06:00"
    assert trip["scheduledEndTime"] == "2026-11-04T09:53:00-06:00"
    stop = trip["stops"][0]
    assert stop["stopName"] == "Lombard"
    assert stop["arrivalTime"] == "09:04:00"
    assert stop["departureTime"] == "09:05:00"
    assert stop["realtimeArrivalTime"] == "09:07:00"
    assert stop["delayedBy"] == 3
    assert stop["expectedArrival"] == "2026-11-04T09:07:00-06:00"
    assert payload["filters"]["direction"] == "inbound"


def test_no_delay_within_sixty_seconds() -> None:
    """Up to a minute either way counts as on time."""
    feed = trip_updates_feed(("UP-W_UW38_V1_A", "LOMBARD", posix(9, 5), None))
    assert build(data=realtime(feed))["routes"][0]["trips"][0]["stops"][0]["delayedBy"] == 0


@pytest.mark.parametrize(
    ("seconds", "expected"), [(61, 2), (60, 0), (-60, 0), (-61, -2), (180, 3), (181, 4)]
)
def test_delay_rounds_up_whole_minutes(seconds: int, expected: int) -> None:
    """Delays round away from zero, as the service does."""
    scheduled = datetime(2026, 11, 4, 9, 0, tzinfo=CENTRAL)
    actual = datetime.fromtimestamp(scheduled.timestamp() + seconds, CENTRAL)
    assert delay_minutes(scheduled, None, actual, None) == expected


def test_delay_falls_back_to_departure() -> None:
    """A departure-only update still gives a delay."""
    feed = trip_updates_feed(("UP-W_UW38_V1_A", "LOMBARD", None, posix(9, 10)))
    assert build(data=realtime(feed))["routes"][0]["trips"][0]["stops"][0]["delayedBy"] == 5


def test_delay_correct_across_daylight_saving_change() -> None:
    """Delays use elapsed time; same-timezone datetime subtraction would be an hour off."""
    scheduled = datetime(2026, 11, 1, 0, 50, tzinfo=CENTRAL)  # CDT
    actual = datetime.fromtimestamp(scheduled.timestamp() + 7200, CENTRAL)  # CST
    assert delay_minutes(scheduled, None, actual, None) == 120


def test_route_alert_only_at_route_level() -> None:
    """A route alert isn't repeated on each trip."""
    feed = alerts_feed(("Line alert", "Details", [("UP-W", "")]))
    route = build(data=realtime(alerts=feed))["routes"][0]
    assert route["alerts"] == [{"header": "Line alert", "description": "Details"}]
    assert all(trip["alerts"] == [] for trip in route["trips"])


def test_trip_alert_only_at_trip_level() -> None:
    """A trip alert with a route ID belongs to that trip."""
    feed = alerts_feed(("Train alert", "Details", [("UP-W", "UP-W_UW38_V1_A")]))
    route = build(data=realtime(alerts=feed))["routes"][0]
    assert route["alerts"] == []
    assert route["trips"][0]["alerts"][0]["header"] == "Train alert"


def test_duplicate_alerts_removed() -> None:
    """The same alert text only shows once, ignoring case."""
    feed = alerts_feed(("Alert", "Text", [("UP-W", "")]), ("ALERT", "TEXT", [("UP-W", "")]))
    assert len(build(data=realtime(alerts=feed))["routes"][0]["alerts"]) == 1


@pytest.mark.parametrize("term", ["construction", "CONSTRUCTION"])
def test_ignore_terms_filter_alerts(term: str) -> None:
    """Ignored terms match either field, ignoring case."""
    feed = alerts_feed(
        ("Station Construction", "Details", [("UP-W", "")]),
        ("Elevator", "Due to construction", [("UP-W", "")]),
        ("Delays", "Signal problem", [("UP-W", "")]),
    )
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, ignore_terms=(term,))
    payload = build(query=query, data=realtime(alerts=feed))
    assert [alert["header"] for alert in payload["routes"][0]["alerts"]] == ["Delays"]
    assert payload["filters"]["ignoreTerms"] == [term]


def test_direction_filter() -> None:
    """Each direction only returns its own trains."""
    assert "39" not in trains(build())
    outbound = ScheduleQuery("UP-W", "LOMBARD", "outbound", 0)
    assert trains(build(query=outbound)) == ["39"]
    assert build(query=outbound)["routes"][0]["trips"][0]["direction"] == "outbound"


def test_both_directions_when_unset() -> None:
    """With no direction, all trains at the station show."""
    assert set(trains(build(query=ScheduleQuery("UP-W", "LOMBARD")))) == {"38", "39", "42"}


def test_next_limits_trains() -> None:
    """Only the requested number of trains is returned."""
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, next_trips=1)
    assert trains(build(query=query)) == ["38"]


def test_hours_past_24_normalized_to_clock_time() -> None:
    """A 25:30:00 stop shows as 01:30:00 on the next day."""
    payload = build(now=datetime(2026, 11, 4, 23, 0, tzinfo=CENTRAL))
    assert trains(payload)[0] == "99"
    stop = payload["routes"][0]["trips"][0]["stops"][0]
    assert stop["arrivalTime"] == "01:30:00"
    assert stop["expectedArrival"] == "2026-11-05T01:30:00-06:00"


def test_passed_stops_excluded() -> None:
    """A train that has already called is gone."""
    assert trains(build(now=datetime(2026, 11, 4, 9, 30, tzinfo=CENTRAL)))[0] == "42"


def test_late_train_stays_while_realtime_is_upcoming() -> None:
    """A train past its scheduled time remains until its realtime arrival."""
    now = datetime(2026, 11, 4, 9, 30, tzinfo=CENTRAL)
    feed = trip_updates_feed(("UP-W_UW38_V1_A", "LOMBARD", posix(9, 45), None))
    payload = build(now=now, data=realtime(feed))
    assert trains(payload)[0] == "38"
    assert payload["routes"][0]["trips"][0]["stops"][0]["delayedBy"] == 41


# Behaviour deliberately improved over the .NET service.


def test_after_midnight_shows_yesterdays_late_trains() -> None:
    """The .NET service only checked today's service, losing 25:30 trains at midnight."""
    # Saturday 00:30 is still Friday's service; weekday service doesn't run on Saturday.
    now = datetime(2026, 11, 7, 0, 30, tzinfo=CENTRAL)
    payload = build(now=now)
    assert trains(payload)[0] == "99"
    assert payload["routes"][0]["trips"][0]["stops"][0]["expectedArrival"] == (
        "2026-11-07T01:30:00-06:00"
    )


def test_late_train_stays_after_scheduled_end_of_trip() -> None:
    """The .NET service dropped trips once the scheduled end passed, even if running late."""
    now = datetime(2026, 11, 4, 9, 55, tzinfo=CENTRAL)  # 38's scheduled end is 09:53
    feed = trip_updates_feed(("UP-W_UW38_V1_A", "LOMBARD", posix(10, 0), None))
    assert trains(build(now=now, data=realtime(feed)))[0] == "38"


def test_trains_ordered_by_time_at_station() -> None:
    """The .NET service kept file order; the fixture lists train 42 before 38."""
    assert trains(build()) == ["38", "42", "99"]


def test_next_ordered_by_time_at_station_not_trip_start() -> None:
    """A later-starting train that reaches the station first must win the limit."""
    archive = build_archive(
        trips="""route_id, service_id, trip_id, trip_headsign, block_id, shape_id, direction_id
UP-W, WK, UP-W_UW1_V1_A, A, , S, 1
UP-W, WK, UP-W_UW2_V1_A, A, , S, 1
""",
        stop_times="""trip_id, arrival_time, departure_time, stop_id, stop_sequence
UP-W_UW1_V1_A, 08:10:00, 08:10:00, WHEATON, 1
UP-W_UW1_V1_A, 10:00:00, 10:00:00, LOMBARD, 2
UP-W_UW2_V1_A, 09:00:00, 09:00:00, WHEATON, 1
UP-W_UW2_V1_A, 09:30:00, 09:30:00, LOMBARD, 2
""",
    )
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, next_trips=1)
    assert trains(build(query=query, archive=archive)) == ["2"]


def test_route_alerts_kept_when_no_trains_remain() -> None:
    """The .NET service's next filter dropped the route, and its alerts, with no trains."""
    feed = alerts_feed(("Line alert", "Details", [("UP-W", "")]))
    now = datetime(2026, 11, 7, 3, 0, tzinfo=CENTRAL)  # Saturday: no weekday trains
    query = ScheduleQuery("UP-W", "AURORA")  # No UP-W calls here at all.
    payload = build(query=query, now=now, data=realtime(alerts=feed))
    assert payload["routes"][0]["trips"] == []
    assert payload["routes"][0]["alerts"][0]["header"] == "Line alert"


def test_realtime_from_another_days_run_ignored() -> None:
    """Tonight's run of a trip mustn't take the delay from last night's run."""
    now = datetime(2026, 11, 5, 0, 30, tzinfo=CENTRAL)
    feed = trip_updates_feed(("UP-W_UW99_V1_A", "LOMBARD", posix(1, 35, day=5), None))
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, next_trips=10)
    trips = build(query=query, now=now, data=realtime(feed))["routes"][0]["trips"]
    run_times = {
        trip["stops"][0]["expectedArrival"]: trip["stops"][0]["delayedBy"]
        for trip in trips
        if trip["train"] == "99"
    }
    assert run_times["2026-11-05T01:35:00-06:00"] == 5
    assert run_times["2026-11-06T01:30:00-06:00"] == 0


# Service calendar.


def test_service_removed_and_added_by_calendar_dates() -> None:
    """A holiday can swap weekday service for weekend service."""
    thanksgiving = datetime(2026, 11, 26, 8, 0, tzinfo=CENTRAL)
    assert trains(build(now=thanksgiving)) == ["50"]


def test_weekend_service_not_on_weekday() -> None:
    """Weekend-only trains don't show on a weekday."""
    assert "50" not in trains(build())


def test_service_outside_date_range() -> None:
    """A calendar that has ended stops running."""
    archive = build_archive(
        calendar="""service_id, monday, tuesday, wednesday, thursday, friday, saturday, sunday, start_date, end_date
WK, 1, 1, 1, 1, 1, 1, 1, 20200101, 20201231
"""
    )
    assert trains(build(archive=archive)) == ["50"]  # Only WE, which has no calendar row.


# Lookups and parsing.


def test_position_included() -> None:
    """A vehicle position attaches to its trip."""
    positions = empty_feed()
    entity = positions.entity.add(id="1")
    entity.vehicle.trip.trip_id = "up-w_uw38_v1_a"  # IDs match regardless of case
    entity.vehicle.vehicle.label = "38"
    entity.vehicle.position.latitude = 41.86548
    entity.vehicle.position.longitude = -88.102196
    entity.vehicle.timestamp = posix(8, 0)
    data = index_realtime(empty_feed(), positions, empty_feed())

    position = build(data=data)["routes"][0]["trips"][0]["position"]
    assert position["vehicleLabel"] == "38"
    assert position["latitude"] == pytest.approx(41.86548)
    assert position["timestamp"] == "2026-11-04T08:00:00-06:00"


def test_position_found_through_trip_update_vehicle_label() -> None:
    """A position without a trip ID can match through the train's label."""
    updates = trip_updates_feed(("UP-W_UW38_V1_A", "LOMBARD", posix(9, 4), None))
    updates.entity[0].trip_update.vehicle.label = "38"
    positions = empty_feed()
    entity = positions.entity.add(id="1")
    entity.vehicle.vehicle.label = "38"
    data = index_realtime(updates, positions, empty_feed())
    assert build(data=data)["routes"][0]["trips"][0]["position"]["vehicleLabel"] == "38"


@pytest.mark.parametrize(
    ("trip_id", "train"),
    [("UP-W_UW38_V1_AA", "38"), ("BNSF_BN1200_V4_A", "1200"), ("NOPARTS", None), ("A_BCD", None)],
)
def test_extract_train(trip_id: str, train: str | None) -> None:
    """The train number comes from the second part of the trip ID."""
    assert extract_train(trip_id) == train


@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("09:04:00", 32640),
        ("  04:00:01", 14401),
        ("25:30:00", 91800),
        ("9:04", None),
        ("", None),
        (None, None),
        ("aa:bb:cc", None),
        ("09:61:00", None),
        ("09:00:61", None),
        ("-1:00:00", None),
    ],
)
def test_parse_gtfs_time(value: str | None, seconds: int | None) -> None:
    """Times parse with padding and past 24 hours; invalid ones are None."""
    assert parse_gtfs_time(value) == seconds


def test_stop_times_only_kept_for_requested_stops() -> None:
    """Memory use stays small because unused stations are discarded."""
    schedule = parse_schedule(build_archive(), {"lombard"})
    assert set(schedule.stop_times_by_stop) == {"lombard"}
    # Trip start and end still reflect the stations that weren't kept.
    trip = next(
        st.trip for st in schedule.stop_times_by_stop["lombard"] if "UW38" in st.trip.trip_id
    )
    assert (trip.start_seconds, trip.end_seconds) == (30300, 35580)
    # Every station stays available for the config flow.
    assert schedule.stations_for_route("UP-W") == {
        "OTC": "Chicago OTC",
        "LOMBARD": "Lombard",
        "WHEATON": "Wheaton",
    }


@pytest.mark.parametrize("content", [b"not a zip", build_archive(routes="", trips="")])
def test_invalid_archive_rejected(content: bytes) -> None:
    """A bad download raises rather than producing an empty schedule."""
    with pytest.raises(ScheduleParseError):
        parse_schedule(content, STOPS)


def test_unknown_route_or_station_is_empty() -> None:
    """Unknown values give no trains rather than an error."""
    assert build(query=ScheduleQuery("NOPE", "LOMBARD"))["routes"] == []
    assert build(query=ScheduleQuery("UP-W", "NOPE"))["routes"] == []
