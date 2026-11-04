"""Constants for the Metra integration."""

from datetime import timedelta
from zoneinfo import ZoneInfo

DOMAIN = "metra"

CENTRAL = ZoneInfo("America/Chicago")

SCHEDULE_URL = "https://schedules.metrarail.com/gtfs/schedule.zip"
REALTIME_BASE_URL = "https://gtfspublic.metrarr.com/gtfs/public"

UPDATE_INTERVAL = timedelta(seconds=60)
SCHEDULE_REFRESH_HOUR = 5  # Central time
SCHEDULE_RETRY_INTERVAL = timedelta(hours=1)

SUBENTRY_STATION = "station"

CONF_LINE = "line"
CONF_STATION = "station"
CONF_DIRECTION = "direction"
CONF_NEXT = "next"
CONF_IGNORE_TERMS = "ignore_terms"

DIRECTION_BOTH = "both"
DIRECTION_INBOUND = "inbound"
DIRECTION_OUTBOUND = "outbound"
DIRECTION_IDS = {DIRECTION_OUTBOUND: 0, DIRECTION_INBOUND: 1}

DEFAULT_NEXT = 3
MAX_NEXT = 10
