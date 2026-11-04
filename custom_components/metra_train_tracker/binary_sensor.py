"""Service alert sensor for a configured Metra station."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import SUBENTRY_STATION
from .coordinator import MetraConfigEntry
from .entity import MetraEntity

ALERT_SENSOR = BinarySensorEntityDescription(
    key="service_alert",
    translation_key="service_alert",
    device_class=BinarySensorDeviceClass.PROBLEM,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MetraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the alert sensor for each station."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type == SUBENTRY_STATION:
            async_add_entities(
                [MetraAlertSensor(entry.runtime_data, subentry, ALERT_SENSOR)],
                config_subentry_id=subentry.subentry_id,
            )


class MetraAlertSensor(MetraEntity, BinarySensorEntity):
    """On when the station's line has a service alert."""

    _unrecorded_attributes = frozenset({"alerts"})

    @property
    def _alerts(self) -> list[dict[str, Any]]:
        route = self.route
        return route["alerts"] if route else []

    @property
    def is_on(self) -> bool:
        """Return whether there are alerts."""
        return bool(self._alerts)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the alert text."""
        return {"alerts": self._alerts}
