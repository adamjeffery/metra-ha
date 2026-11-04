"""Parse the static Metra GTFS schedule archive."""

from __future__ import annotations

import csv
import io
import zipfile
from collections import defaultdict
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime


class ScheduleParseError(Exception):
    """The schedule archive is missing or unusable."""


@dataclass(frozen=True, slots=True)
class Route:
    """A Metra line."""

    route_id: str
    short_name: str
    long_name: str
    description: str


@dataclass(slots=True)
class Trip:
    """A scheduled train, with its first and last times across all stops."""

    trip_id: str
    route_id: str
    service_id: str
    headsign: str
    direction_id: int
    start_seconds: int | None = None
    end_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class StopTime:
    """A train's scheduled call at a station. Times are seconds past service midnight."""

    trip: Trip
    sequence: int
    arrival: int | None
    departure: int | None


@dataclass(frozen=True, slots=True)
class Calendar:
    """The regular weekly pattern for a service."""

    weekdays: tuple[bool, ...]  # Monday first, matching date.weekday()
    start_date: date
    end_date: date


@dataclass(slots=True)
class GtfsSchedule:
    """The parts of the schedule the integration needs.

    Stop times are only retained for the stations in use: the full feed has over
    100,000 rows, and a station needs a small fraction of them.
    """

    routes: dict[str, Route] = field(default_factory=dict)
    stop_names: dict[str, str] = field(default_factory=dict)
    stops_by_route: dict[str, set[str]] = field(default_factory=dict)
    stop_times_by_stop: dict[str, list[StopTime]] = field(default_factory=dict)
    calendars: dict[str, Calendar] = field(default_factory=dict)
    added_services: dict[date, set[str]] = field(default_factory=dict)
    removed_services: dict[date, set[str]] = field(default_factory=dict)

    def stations_for_route(self, route_id: str) -> dict[str, str]:
        """Return a route's stations as {stop_id: name}, sorted by name."""
        stops = self.stops_by_route.get(route_id, ())
        names = {stop_id: self.stop_names.get(stop_id, stop_id) for stop_id in stops}
        return dict(sorted(names.items(), key=lambda item: item[1].casefold()))

    def is_service_active(self, service_id: str, service_date: date) -> bool:
        """Return whether a service runs on a date."""
        key = service_id.casefold()
        if key in self.removed_services.get(service_date, ()):
            return False
        if key in self.added_services.get(service_date, ()):
            return True
        calendar = self.calendars.get(key)
        if calendar is None:
            return True
        if not calendar.start_date <= service_date <= calendar.end_date:
            return False
        return calendar.weekdays[service_date.weekday()]


def parse_gtfs_time(value: str | None) -> int | None:
    """Convert HH:MM:SS to seconds. Hours may exceed 23 for after-midnight trips."""
    parts = (value or "").strip().split(":")
    if len(parts) != 3:
        return None
    try:
        hours, minutes, seconds = (int(part) for part in parts)
    except ValueError:
        return None
    if hours < 0 or not 0 <= minutes <= 59 or not 0 <= seconds <= 59:
        return None
    return hours * 3600 + minutes * 60 + seconds


def _parse_int(value: str | None, default: int = 0) -> int:
    try:
        return int(value or "")
    except ValueError:
        return default


def _parse_date(value: str | None) -> date:
    try:
        return datetime.strptime(value or "", "%Y%m%d").date()
    except ValueError:
        return date.min


def _read_rows(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, str]]:
    """Yield a file's rows, trimmed: Metra pads fields with spaces after commas."""
    if name not in archive.namelist():
        return
    with archive.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8-sig", errors="replace", newline="")
        reader = csv.reader(text, skipinitialspace=True)
        header = [column.strip().casefold() for column in next(reader, [])]
        for row in reader:
            if any(cell.strip() for cell in row):
                yield {column: cell.strip() for column, cell in zip(header, row, strict=False)}


def parse_schedule(archive_bytes: bytes, keep_stops: Collection[str]) -> GtfsSchedule:
    """Parse a GTFS archive, retaining stop times only for keep_stops."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(archive_bytes))
    except zipfile.BadZipFile as err:
        raise ScheduleParseError("Schedule archive is not a valid zip file") from err

    schedule = GtfsSchedule()
    keep = {stop_id.casefold() for stop_id in keep_stops}

    with archive:
        for row in _read_rows(archive, "routes.txt"):
            if route_id := row.get("route_id"):
                schedule.routes[route_id] = Route(
                    route_id,
                    row.get("route_short_name", ""),
                    row.get("route_long_name", ""),
                    row.get("route_desc", ""),
                )

        for row in _read_rows(archive, "stops.txt"):
            if stop_id := row.get("stop_id"):
                schedule.stop_names[stop_id] = row.get("stop_name", stop_id)

        trips: dict[str, Trip] = {}
        for row in _read_rows(archive, "trips.txt"):
            if trip_id := row.get("trip_id"):
                trips[trip_id.casefold()] = Trip(
                    trip_id,
                    row.get("route_id", ""),
                    row.get("service_id", ""),
                    row.get("trip_headsign", ""),
                    _parse_int(row.get("direction_id")),
                )

        _read_stop_times(archive, schedule, trips, keep)

        for row in _read_rows(archive, "calendar.txt"):
            if service_id := row.get("service_id"):
                schedule.calendars[service_id.casefold()] = Calendar(
                    tuple(
                        _parse_int(row.get(day)) == 1
                        for day in (
                            "monday",
                            "tuesday",
                            "wednesday",
                            "thursday",
                            "friday",
                            "saturday",
                            "sunday",
                        )
                    ),
                    _parse_date(row.get("start_date")),
                    _parse_date(row.get("end_date")),
                )

        for row in _read_rows(archive, "calendar_dates.txt"):
            if service_id := row.get("service_id"):
                exceptions = {
                    1: schedule.added_services,
                    2: schedule.removed_services,
                }.get(_parse_int(row.get("exception_type")))
                if exceptions is not None:
                    exceptions.setdefault(_parse_date(row.get("date")), set()).add(
                        service_id.casefold()
                    )

    if not schedule.routes or not trips:
        raise ScheduleParseError("Schedule archive has no routes or trips")
    return schedule


def _read_stop_times(
    archive: zipfile.ZipFile,
    schedule: GtfsSchedule,
    trips: dict[str, Trip],
    keep: set[str],
) -> None:
    """Index stop times and work out each trip's start and end times."""
    stops_by_route: defaultdict[str, set[str]] = defaultdict(set)
    stop_times: defaultdict[str, list[StopTime]] = defaultdict(list)
    first_sequence: dict[str, int] = {}
    last_sequence: dict[str, int] = {}

    for row in _read_rows(archive, "stop_times.txt"):
        trip = trips.get(row.get("trip_id", "").casefold())
        stop_id = row.get("stop_id")
        if trip is None or not stop_id:
            continue

        stops_by_route[trip.route_id].add(stop_id)
        sequence = _parse_int(row.get("stop_sequence"))
        arrival = parse_gtfs_time(row.get("arrival_time"))
        departure = parse_gtfs_time(row.get("departure_time"))

        start = arrival if arrival is not None else departure
        if start is not None and sequence < first_sequence.get(trip.trip_id, sequence + 1):
            first_sequence[trip.trip_id] = sequence
            trip.start_seconds = start

        end = departure if departure is not None else arrival
        if end is not None and sequence > last_sequence.get(trip.trip_id, sequence - 1):
            last_sequence[trip.trip_id] = sequence
            trip.end_seconds = end

        key = stop_id.casefold()
        if key in keep:
            stop_times[key].append(StopTime(trip, sequence, arrival, departure))

    schedule.stops_by_route = dict(stops_by_route)
    schedule.stop_times_by_stop = dict(stop_times)
