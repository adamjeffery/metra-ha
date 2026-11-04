"""Combine the static schedule with realtime feeds into a station's upcoming trains."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from math import ceil
from typing import Any

from .const import CENTRAL
from .gtfs import GtfsSchedule, Route, StopTime

# A realtime time further than this from the schedule belongs to another day's run
# of the same trip ID, such as tonight's 24:30 train seen while last night's runs.
MAX_REALTIME_OFFSET_SECONDS = 12 * 3600
ON_TIME_TOLERANCE_SECONDS = 60
DIRECTION_NAMES = {0: "outbound", 1: "inbound"}
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


@dataclass(frozen=True, slots=True)
class ScheduleQuery:
    """What a configured station wants to see."""

    route_id: str
    stop_id: str
    direction: str | None = None  # "inbound", "outbound" or None for both
    direction_id: int | None = None
    next_trips: int = 3
    ignore_terms: tuple[str, ...] = ()
    favorite_trains: tuple[str, ...] = ()


@dataclass(slots=True)
class RealtimeData:
    """Realtime feeds indexed by case-insensitive trip ID or train label."""

    trip_updates: dict[str, Any] = field(default_factory=dict)
    positions_by_trip: dict[str, Any] = field(default_factory=dict)
    positions_by_train: dict[str, Any] = field(default_factory=dict)
    alert_entities: list[Any] = field(default_factory=list)


def index_realtime(trip_updates: Any, positions: Any, alerts: Any) -> RealtimeData:
    """Index GTFS-realtime FeedMessages for lookup."""
    data = RealtimeData()
    for entity in trip_updates.entity:
        if entity.HasField("trip_update") and (trip_id := entity.trip_update.trip.trip_id):
            data.trip_updates.setdefault(trip_id.casefold(), entity.trip_update)
    for entity in positions.entity:
        if not entity.HasField("vehicle"):
            continue
        if trip_id := entity.vehicle.trip.trip_id:
            data.positions_by_trip.setdefault(trip_id.casefold(), entity.vehicle)
        if label := entity.vehicle.vehicle.label:
            data.positions_by_train.setdefault(label.casefold(), entity.vehicle)
    data.alert_entities = [entity for entity in alerts.entity if entity.HasField("alert")]
    return data


def extract_train(trip_id: str) -> str | None:
    """Get the train number from a trip ID: UP-W_UW38_V1_AA is train 38."""
    parts = trip_id.split("_")
    if len(parts) < 2:
        return None
    for index, char in enumerate(parts[1]):
        if char.isascii() and char.isdigit():
            return parts[1][index:]
    return None


def trains_at_station(schedule: GtfsSchedule, query: ScheduleQuery, today: date) -> dict[str, str]:
    """Return the current trains calling at a station as {train: label}, by time of day.

    Trains are identified by number because trip IDs change between schedule versions.
    """
    details: dict[str, tuple[int, str, str]] = {}
    regular_days: dict[str, set[int]] = {}
    one_off_days: dict[str, set[int]] = {}
    for stop_time in schedule.stop_times_by_stop.get(query.stop_id.casefold(), ()):
        trip = stop_time.trip
        train = extract_train(trip.trip_id)
        seconds = stop_time.arrival if stop_time.arrival is not None else stop_time.departure
        if (
            train is None
            or seconds is None
            or trip.route_id.casefold() != query.route_id.casefold()
            or (query.direction_id is not None and trip.direction_id != query.direction_id)
            or not schedule.is_service_current(trip.service_id, today)
        ):
            continue
        # Sorted and labelled by clock time, so a 25:30 train comes first as 1:30 AM.
        clock = seconds % 86400
        hours, minutes = clock // 3600, clock % 3600 // 60
        time_text = f"{hours % 12 or 12}:{minutes:02d} {'AM' if hours < 12 else 'PM'}"
        if train not in details or clock < details[train][0]:
            details[train] = (clock, time_text, trip.headsign)

        calendar = schedule.calendars.get(trip.service_id.casefold())
        if calendar is not None and calendar.end_date >= today:
            days = regular_days if calendar.end_date > calendar.start_date else one_off_days
            days.setdefault(train, set()).update(
                index for index, runs in enumerate(calendar.weekdays) if runs
            )

    labels = {}
    for train, (_, time_text, headsign) in sorted(details.items(), key=lambda item: item[1][0]):
        # A one-day special says little about when a regular train runs.
        days_text = _format_days(regular_days.get(train) or one_off_days.get(train) or set())
        suffix = f" ({days_text})" if days_text else ""
        labels[train] = f"{train} – {time_text} to {headsign}{suffix}"
    return labels


def _format_days(days: set[int]) -> str:
    """Describe days of the week, where Monday is 0."""
    if days == set(range(7)):
        return "Daily"
    if days == set(range(5)):
        return "Weekdays"
    return ", ".join(DAY_NAMES[day] for day in sorted(days))


def delay_minutes(
    scheduled_arrival: datetime | None,
    scheduled_departure: datetime | None,
    realtime_arrival: datetime | None,
    realtime_departure: datetime | None,
) -> int:
    """Return whole minutes late (negative if early), or 0 within a minute of schedule."""
    if scheduled_arrival is not None and realtime_arrival is not None:
        seconds = realtime_arrival.timestamp() - scheduled_arrival.timestamp()
    elif scheduled_departure is not None and realtime_departure is not None:
        seconds = realtime_departure.timestamp() - scheduled_departure.timestamp()
    else:
        return 0
    if abs(seconds) <= ON_TIME_TOLERANCE_SECONDS:
        return 0
    minutes = ceil(abs(seconds) / 60)
    return -minutes if seconds < 0 else minutes


def build_schedule(
    schedule: GtfsSchedule, realtime: RealtimeData, query: ScheduleQuery, now: datetime
) -> dict[str, Any]:
    """Build a station's payload. Keys match the JSON of the original Metra API."""
    now_central = now.astimezone(CENTRAL)
    today = now_central.date()
    route = schedule.routes.get(query.route_id)
    route_alerts, trip_alerts = _build_alert_maps(realtime, query.ignore_terms)

    calls: list[tuple[float, str, dict[str, Any]]] = []
    stop_times = schedule.stop_times_by_stop.get(query.stop_id.casefold(), ())
    if route is not None:
        # After midnight, yesterday's service can still be running (25:30:00 and so on).
        for service_date in (today - timedelta(days=1), today):
            for stop_time in stop_times:
                call = _build_call(
                    schedule, realtime, query, stop_time, service_date, now_central, trip_alerts
                )
                if call is not None:
                    calls.append(call)

    calls.sort(key=lambda call: call[:2])
    trips = [call[2] for call in calls[: max(query.next_trips, 0)]]
    # Looked up before the limit is applied, as a favorite can be further away than that.
    next_favorite = next((call[2] for call in calls if call[2]["favorite"]), None)

    payload: dict[str, Any] = {
        "generatedAt": now_central.isoformat(),
        "filters": {
            "route": query.route_id,
            "stop": query.stop_id,
            "direction": query.direction,
            "next": query.next_trips,
            "ignoreTerms": list(query.ignore_terms) or None,
            "favoriteTrains": list(query.favorite_trains) or None,
        },
        "nextFavorite": next_favorite,
        "routes": [],
    }
    alerts = route_alerts.get(query.route_id.casefold(), [])
    if route is not None and (trips or alerts):
        payload["routes"].append(_route_payload(route, alerts, trips))
    return payload


def _route_payload(
    route: Route, alerts: list[dict[str, Any]], trips: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "routeId": route.route_id,
        "routeShortName": route.short_name,
        "routeLongName": route.long_name,
        "routeDesc": route.description,
        "alerts": alerts,
        "trips": trips,
    }


def _build_call(
    schedule: GtfsSchedule,
    realtime: RealtimeData,
    query: ScheduleQuery,
    stop_time: StopTime,
    service_date: date,
    now: datetime,
    trip_alerts: dict[str, list[dict[str, Any]]],
) -> tuple[float, str, dict[str, Any]] | None:
    """Return (sort time, trip ID, trip payload) if this call is still to come."""
    trip = stop_time.trip
    if (
        trip.route_id.casefold() != query.route_id.casefold()
        or (query.direction_id is not None and trip.direction_id != query.direction_id)
        or not schedule.is_service_active(trip.service_id, service_date)
    ):
        return None

    scheduled_arrival = _to_datetime(service_date, stop_time.arrival)
    scheduled_departure = _to_datetime(service_date, stop_time.departure)
    trip_key = trip.trip_id.casefold()
    trip_update = realtime.trip_updates.get(trip_key)
    realtime_arrival, realtime_departure = _realtime_times(
        trip_update, query.stop_id, scheduled_arrival, scheduled_departure
    )

    # The stop decides, not the trip's scheduled end: a late train on its final
    # stops is still on its way after its scheduled end has passed.
    now_timestamp = now.timestamp()
    if not (
        (scheduled_arrival is not None and scheduled_arrival.timestamp() >= now_timestamp)
        or (realtime_arrival is not None and realtime_arrival.timestamp() >= now_timestamp)
    ):
        return None

    scheduled = scheduled_arrival or scheduled_departure
    start = _to_datetime(service_date, trip.start_seconds)
    end = _to_datetime(service_date, trip.end_seconds)
    expected = realtime_arrival or realtime_departure or scheduled
    train = extract_train(trip.trip_id)
    payload = {
        "tripId": trip.trip_id,
        "train": train,
        "favorite": train in query.favorite_trains,
        "serviceId": trip.service_id,
        "tripHeadsign": trip.headsign,
        "directionId": trip.direction_id,
        "direction": DIRECTION_NAMES.get(trip.direction_id),
        "scheduledStartTime": start.isoformat() if start else None,
        "scheduledEndTime": end.isoformat() if end else None,
        "position": _position_payload(realtime, trip_key, trip_update),
        "alerts": trip_alerts.get(trip_key, []),
        "stops": [
            {
                "stopId": query.stop_id,
                "stopName": schedule.stop_names.get(query.stop_id),
                "stopSequence": stop_time.sequence,
                "arrivalTime": _clock(scheduled_arrival),
                "departureTime": _clock(scheduled_departure),
                "realtimeArrivalTime": _clock(realtime_arrival),
                "realtimeDepartureTime": _clock(realtime_departure),
                "delayedBy": delay_minutes(
                    scheduled_arrival, scheduled_departure, realtime_arrival, realtime_departure
                ),
                "expectedArrival": expected.isoformat() if expected else None,
            }
        ],
    }
    return (scheduled.timestamp() if scheduled else float("inf"), trip_key, payload)


def _to_datetime(service_date: date, seconds: int | None) -> datetime | None:
    if seconds is None:
        return None
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, secs = divmod(remainder, 60)
    return datetime.combine(
        service_date + timedelta(days=days), time(hours, minutes, secs), tzinfo=CENTRAL
    )


def _clock(value: datetime | None) -> str | None:
    return value.strftime("%H:%M:%S") if value else None


def _realtime_times(
    trip_update: Any,
    stop_id: str,
    scheduled_arrival: datetime | None,
    scheduled_departure: datetime | None,
) -> tuple[datetime | None, datetime | None]:
    if trip_update is None:
        return None, None
    key = stop_id.casefold()
    for update in trip_update.stop_time_update:
        if update.stop_id.casefold() == key:
            return (
                _realtime_time(update.arrival.time, scheduled_arrival),
                _realtime_time(update.departure.time, scheduled_departure),
            )
    return None, None


def _realtime_time(posix_seconds: int, scheduled: datetime | None) -> datetime | None:
    if posix_seconds <= 0:
        return None
    if (
        scheduled is not None
        and abs(posix_seconds - scheduled.timestamp()) > MAX_REALTIME_OFFSET_SECONDS
    ):
        return None
    return datetime.fromtimestamp(posix_seconds, CENTRAL)


def _position_payload(
    realtime: RealtimeData, trip_key: str, trip_update: Any
) -> dict[str, Any] | None:
    vehicle = realtime.positions_by_trip.get(trip_key)
    if vehicle is None and trip_update is not None and (label := trip_update.vehicle.label):
        vehicle = realtime.positions_by_train.get(label.casefold())
    if vehicle is None:
        return None
    has_position = vehicle.HasField("position")
    position = vehicle.position
    return {
        "vehicleLabel": vehicle.vehicle.label or None,
        # The feed uses 32-bit floats, so round away the conversion noise.
        "latitude": round(position.latitude, 6) if has_position else None,
        "longitude": round(position.longitude, 6) if has_position else None,
        "bearing": round(position.bearing, 1) if has_position else None,
        "speed": round(position.speed, 1) if has_position else None,
        "timestamp": (
            datetime.fromtimestamp(vehicle.timestamp, CENTRAL).isoformat()
            if vehicle.timestamp > 0
            else None
        ),
    }


def _build_alert_maps(
    realtime: RealtimeData, ignore_terms: tuple[str, ...]
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """Group alerts by route and trip, dropping ignored and duplicate alerts."""
    terms = [term.casefold() for term in ignore_terms if term.strip()]
    route_alerts: dict[str, list[dict[str, Any]]] = {}
    trip_alerts: dict[str, list[dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()

    for entity in realtime.alert_entities:
        alert = entity.alert
        header_texts = [item.text for item in alert.header_text.translation]
        description_texts = [item.text for item in alert.description_text.translation]
        searchable = [text.casefold() for text in (*header_texts, *description_texts)]
        if any(term in text for term in terms for text in searchable):
            continue

        payload = {
            "header": header_texts[0] if header_texts else None,
            "description": description_texts[0] if description_texts else None,
        }
        identity = (
            (payload["header"] or "").casefold(),
            (payload["description"] or "").casefold(),
        )
        for informed in alert.informed_entity:
            trip_id = informed.trip.trip_id.strip()
            if trip_id:
                target, key = trip_alerts, trip_id.casefold()
            elif informed.route_id.strip():
                target, key = route_alerts, informed.route_id.casefold()
            else:
                continue
            if (key, *identity) not in seen:
                seen.add((key, *identity))
                target.setdefault(key, []).append(payload)

    return route_alerts, trip_alerts
