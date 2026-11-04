"""Tests for the schedule logic, ported from the original .NET service's tests."""

from __future__ import annotations

from datetime import datetime

import pytest

from custom_components.metra_train_tracker.const import CENTRAL
from custom_components.metra_train_tracker.gtfs import (
    ScheduleParseError,
    parse_gtfs_time,
    parse_schedule,
)
from custom_components.metra_train_tracker.schedule import (
    RealtimeData,
    ScheduleQuery,
    build_schedule,
    delay_minutes,
    extract_train,
    index_realtime,
    trains_at_station,
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


# Favorite trains.


def test_favorites_are_marked() -> None:
    """Only the chosen trains are flagged."""
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, favorite_trains=("42",))
    payload = build(query=query)
    flags = {trip["train"]: trip["favorite"] for trip in payload["routes"][0]["trips"]}
    assert flags == {"38": False, "42": True, "99": False}
    assert payload["filters"]["favoriteTrains"] == ["42"]
    assert payload["nextFavorite"]["train"] == "42"


def test_next_favorite_found_beyond_the_display_limit() -> None:
    """Notifications can't depend on how many trains the card shows."""
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, next_trips=1, favorite_trains=("42",))
    payload = build(query=query)
    assert trains(payload) == ["38"]
    assert payload["nextFavorite"]["train"] == "42"


def test_next_favorite_includes_realtime_delay() -> None:
    """The favorite's delay comes from the realtime feed."""
    feed = trip_updates_feed(("UP-W_UW42_V1_A", "LOMBARD", posix(10, 11), None))
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, favorite_trains=("42",))
    assert build(query=query, data=realtime(feed))["nextFavorite"]["stops"][0]["delayedBy"] == 7


def test_next_favorite_moves_on_once_passed() -> None:
    """After a favorite has called, the next one takes over, then nothing."""
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, favorite_trains=("38", "42"))
    assert build(query=query)["nextFavorite"]["train"] == "38"
    at_930 = datetime(2026, 11, 4, 9, 30, tzinfo=CENTRAL)
    assert build(query=query, now=at_930)["nextFavorite"]["train"] == "42"
    at_1100 = datetime(2026, 11, 4, 11, 0, tzinfo=CENTRAL)
    assert build(query=query, now=at_1100)["nextFavorite"] is None


def test_no_favorites_by_default() -> None:
    """Stations without favorites have none flagged."""
    payload = build()
    assert payload["nextFavorite"] is None
    assert payload["filters"]["favoriteTrains"] is None
    assert not any(trip["favorite"] for trip in payload["routes"][0]["trips"])


def test_favorites_survive_new_trip_id_format() -> None:
    """Metra's announced trip IDs still resolve to the same train number."""
    archive = build_archive(
        trips="""route_id,service_id,trip_id,trip_headsign,block_id,shape_id,direction_id
UP-W,8_2009,UP-W_42_8_2009_5682524,Chicago OTC,5682524,1685230199,1
""",
        stop_times="""trip_id,arrival_time,departure_time,stop_id,stop_sequence
UP-W_42_8_2009_5682524,10:04:00,10:04:00,LOMBARD,2
""",
        calendar="""service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
8_2009,1,1,1,1,1,0,0,20200101,20301231
""",
    )
    query = ScheduleQuery("UP-W", "LOMBARD", "inbound", 1, favorite_trains=("42",))
    assert build(query=query, archive=archive)["nextFavorite"]["train"] == "42"


def test_trains_at_station_lists_current_trains_by_time() -> None:
    """The picker offers each train once, in time order, with a readable label."""
    schedule = parse_schedule(build_archive(), STOPS)
    trains_found = trains_at_station(schedule, INBOUND, NOW.date())
    assert trains_found == {
        "99": "99 – Lombard 1:30 AM, leaves Wheaton 12:50 AM, to Chicago OTC (Weekdays)",
        "38": "38 – Lombard 9:04 AM, leaves Wheaton 8:25 AM, to Chicago OTC (Weekdays)",
        "42": "42 – Lombard 10:04 AM, leaves Wheaton 9:30 AM, to Chicago OTC (Weekdays)",
        # The fixture's holiday swap adds weekend service on a Thursday, which isn't regular.
        "50": "50 – Lombard 12:00 PM to Chicago OTC (Sat, Sun)",
    }
    assert list(trains_found) == ["99", "38", "42", "50"]


def test_trains_at_station_skips_expired_services() -> None:
    """Trains from a calendar that has ended aren't offered."""
    archive = build_archive(
        calendar="""service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
WK,1,1,1,1,1,0,0,20200101,20201231
WE,0,0,0,0,0,1,1,20200101,20301231
""",
        calendar_dates="service_id,date,exception_type\n",
    )
    schedule = parse_schedule(archive, STOPS)
    assert list(trains_at_station(schedule, INBOUND, NOW.date())) == ["50"]


def test_trains_at_station_keeps_service_added_on_a_later_date() -> None:
    """A service with only a future special date is still current."""
    schedule = parse_schedule(
        build_archive(
            calendar="""service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date
WK,1,1,1,1,1,0,0,20200101,20201231
""",
            calendar_dates="service_id,date,exception_type\nWK,20261225,1\n",
        ),
        STOPS,
    )
    assert "38" in trains_at_station(schedule, INBOUND, NOW.date())


def test_trains_at_station_respects_direction() -> None:
    """Only trains in the chosen direction are offered."""
    schedule = parse_schedule(build_archive(), STOPS)
    outbound = ScheduleQuery("UP-W", "LOMBARD", "outbound", 0)
    assert list(trains_at_station(schedule, outbound, NOW.date())) == ["39"]


CALENDAR_HEADER = (
    "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
)


def picker_label(calendar_rows: str, trips: str) -> str:
    """Return train 1's picker label for the given calendar rows and service IDs."""
    archive = build_archive(
        calendar=CALENDAR_HEADER + calendar_rows,
        calendar_dates="service_id,date,exception_type\n",
        trips="route_id,service_id,trip_id,trip_headsign,block_id,shape_id,direction_id\n" + trips,
        stop_times="trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        + "".join(
            f"{line.split(',')[2]},09:00:00,09:00:00,LOMBARD,1\n" for line in trips.splitlines()
        ),
    )
    schedule = parse_schedule(archive, STOPS)
    return trains_at_station(schedule, INBOUND, NOW.date())["1"]


@pytest.mark.parametrize(
    ("weekdays", "expected"),
    [
        ("1,1,1,1,1,0,0", "Weekdays"),
        ("0,0,0,0,0,1,1", "Sat, Sun"),
        ("0,0,0,0,0,1,0", "Sat"),
        ("1,1,1,1,1,1,1", "Daily"),
        ("1,0,1,0,0,0,0", "Mon, Wed"),
    ],
)
def test_picker_shows_days_a_train_runs(weekdays: str, expected: str) -> None:
    """Trains at the same time can run on different days, so the label has to say."""
    label = picker_label(
        f"S,{weekdays},20200101,20301231\n", "UP-W,S,UP-W_UW1_V1_A,Chicago OTC,,S,1\n"
    )
    assert label == f"1 – Lombard 9:00 AM to Chicago OTC ({expected})"


def test_picker_days_combine_across_services() -> None:
    """A number on both weekday and weekend services runs daily."""
    label = picker_label(
        "A,1,1,1,1,1,0,0,20200101,20301231\nB,0,0,0,0,0,1,1,20200101,20301231\n",
        "UP-W,A,UP-W_UW1_V1_A,Chicago OTC,,S,1\nUP-W,B,UP-W_UW1_V2_A,Chicago OTC,,S,1\n",
    )
    assert label.endswith("(Daily)")


def test_picker_days_ignore_one_day_specials_and_expired_services() -> None:
    """A one-off Sunday or an ended calendar doesn't change a regular train's days."""
    label = picker_label(
        "A,1,1,1,1,1,0,0,20200101,20301231\n"
        "SPECIAL,0,0,0,0,0,0,1,20261122,20261122\n"
        "OLD,0,0,0,0,0,1,0,20200101,20201231\n",
        "UP-W,A,UP-W_UW1_V1_A,Chicago OTC,,S,1\n"
        "UP-W,SPECIAL,UP-W_UW1_V2_A,Chicago OTC,,S,1\n"
        "UP-W,OLD,UP-W_UW1_V3_A,Chicago OTC,,S,1\n",
    )
    assert label.endswith("(Weekdays)")


def test_picker_days_use_one_day_special_when_nothing_else_exists() -> None:
    """A train that only runs on a special date still says which day."""
    label = picker_label(
        "SPECIAL,0,0,0,0,0,0,1,20261122,20261122\n",
        "UP-W,SPECIAL,UP-W_UW1_V1_A,Chicago OTC,,S,1\n",
    )
    assert label.endswith("(Sun)")


def test_picker_omits_days_when_unknown() -> None:
    """A service with no calendar row gets no day label rather than a wrong one."""
    label = picker_label(
        "A,1,1,1,1,1,0,0,20200101,20301231\n", "UP-W,NONE,UP-W_UW1_V1_A,Chicago OTC,,S,1\n"
    )
    assert label == "1 – Lombard 9:00 AM to Chicago OTC"


def test_picker_uses_first_stop_as_origin_regardless_of_file_order() -> None:
    """The origin comes from stop_sequence, and uses its departure time."""
    archive = build_archive(
        trips="route_id,service_id,trip_id,trip_headsign,block_id,shape_id,direction_id\n"
        "UP-W,WK,UP-W_UW45_V1_A,Elburn,,S,0\n",
        stop_times="trip_id,arrival_time,departure_time,stop_id,stop_sequence\n"
        "UP-W_UW45_V1_A,17:12:00,17:13:00,LOMBARD,5\n"
        "UP-W_UW45_V1_A,18:00:00,18:00:00,AURORA,9\n"
        "UP-W_UW45_V1_A,16:30:00,16:35:00,OTC,1\n",
    )
    schedule = parse_schedule(archive, STOPS)
    query = ScheduleQuery("UP-W", "LOMBARD", "outbound", 0)
    assert trains_at_station(schedule, query, NOW.date()) == {
        "45": "45 – Lombard 5:12 PM, leaves Chicago OTC 4:35 PM, to Elburn (Weekdays)"
    }


def test_picker_omits_origin_when_train_starts_at_the_station() -> None:
    """A train starting at the chosen station doesn't repeat itself."""
    schedule = parse_schedule(build_archive(), {"WHEATON"})
    query = ScheduleQuery("UP-W", "WHEATON", "inbound", 1)
    assert trains_at_station(schedule, query, NOW.date())["38"] == (
        "38 – Wheaton 8:25 AM to Chicago OTC (Weekdays)"
    )


def test_favorite_trips_listed_up_to_the_limit() -> None:
    """The card's favorites-only mode gets every favorite within the limit."""
    query = ScheduleQuery(
        "UP-W", "LOMBARD", "inbound", 1, next_trips=1, favorite_trains=("38", "42")
    )
    payload = build(query=query)
    assert [trip["train"] for trip in payload["favoriteTrips"]] == ["38"]
    assert payload["nextFavorite"]["train"] == "38"
    wider = ScheduleQuery(
        "UP-W", "LOMBARD", "inbound", 1, next_trips=5, favorite_trains=("38", "42")
    )
    assert [trip["train"] for trip in build(query=wider)["favoriteTrips"]] == ["38", "42"]


def test_payload_includes_origin_and_station_name() -> None:
    """The card can show where a train starts and name the station with no trains."""
    payload = build()
    trip = payload["routes"][0]["trips"][0]
    assert trip["originName"] == "Wheaton"
    assert trip["originDepartureTime"] == "08:25:00"
    assert payload["filters"]["stopName"] == "Lombard"


def test_payload_omits_origin_when_train_starts_at_the_station() -> None:
    """A train starting here has no separate origin to show."""
    trip = build(query=ScheduleQuery("UP-W", "LOMBARD", "outbound", 0))["routes"][0]["trips"][0]
    assert trip["train"] == "39"
    assert trip["originName"] is None
    assert trip["originDepartureTime"] is None


def test_payload_includes_line_colors() -> None:
    """The card can show train numbers in the line's color."""
    route = build()["routes"][0]
    assert route["routeColor"] == "FE8D81"
    assert route["routeTextColor"] == "000000"


def test_line_colors_are_none_when_the_feed_has_none() -> None:
    """A line with no colors gives None, not an empty string."""
    query = ScheduleQuery("BNSF", "AURORA", "inbound", 1)
    route = build(query=query, now=datetime(2026, 11, 4, 8, 0, tzinfo=CENTRAL))["routes"][0]
    assert route["routeColor"] is None
    assert route["routeTextColor"] is None
