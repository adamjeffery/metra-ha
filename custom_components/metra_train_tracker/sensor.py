"""Sensors for a configured Metra station."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CONF_FAVORITE_TRAINS, DOMAIN, SUBENTRY_STATION
from .coordinator import MetraConfigEntry
from .entity import MetraEntity


@dataclass(frozen=True, kw_only=True)
class MetraSensorDescription(SensorEntityDescription):
    """Describes a Metra sensor."""

    value_fn: Callable[[MetraEntity], Any]


def _expected_arrival(stop: dict[str, Any] | None) -> datetime | None:
    if stop is None or stop["expectedArrival"] is None:
        return None
    return datetime.fromisoformat(stop["expectedArrival"])


def _next_arrival(entity: MetraEntity) -> datetime | None:
    return _expected_arrival(entity.next_stop)


SENSORS = (
    MetraSensorDescription(
        key="next_arrival",
        translation_key="next_arrival",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=_next_arrival,
    ),
    MetraSensorDescription(
        key="next_train",
        translation_key="next_train",
        icon="mdi:train",
        value_fn=lambda entity: entity.next_trip["train"] if entity.next_trip else None,
    ),
    MetraSensorDescription(
        key="next_delay",
        translation_key="next_delay",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:clock-alert-outline",
        value_fn=lambda entity: entity.next_stop["delayedBy"] if entity.next_stop else None,
    ),
)


FAVORITE_SENSORS = (
    MetraSensorDescription(
        key="next_favorite_arrival",
        translation_key="next_favorite_arrival",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:star-outline",
        value_fn=lambda entity: _expected_arrival(entity.next_favorite_stop),
    ),
    MetraSensorDescription(
        key="next_favorite_train",
        translation_key="next_favorite_train",
        icon="mdi:star-outline",
        value_fn=lambda entity: (
            entity.payload["nextFavorite"]["train"] if entity.payload["nextFavorite"] else None
        ),
    ),
    MetraSensorDescription(
        key="next_favorite_delay",
        translation_key="next_favorite_delay",
        device_class=SensorDeviceClass.DURATION,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        icon="mdi:star-outline",
        value_fn=lambda entity: (
            entity.next_favorite_stop["delayedBy"] if entity.next_favorite_stop else None
        ),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: MetraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up sensors for each station."""
    for subentry in entry.subentries.values():
        if subentry.subentry_type != SUBENTRY_STATION:
            continue
        entities: list[MetraSensor] = [
            MetraScheduleSensor(entry.runtime_data, subentry, SENSORS[0]),
            *(MetraSensor(entry.runtime_data, subentry, item) for item in SENSORS[1:]),
        ]
        if subentry.data.get(CONF_FAVORITE_TRAINS):
            entities += [
                MetraSensor(entry.runtime_data, subentry, item) for item in FAVORITE_SENSORS
            ]
        else:
            # Favorites may have just been removed from this station.
            registry = er.async_get(hass)
            for item in FAVORITE_SENSORS:
                unique_id = f"{subentry.subentry_id}_{item.key}"
                if entity_id := registry.async_get_entity_id("sensor", DOMAIN, unique_id):
                    registry.async_remove(entity_id)
        async_add_entities(entities, config_subentry_id=subentry.subentry_id)


class MetraSensor(MetraEntity, SensorEntity):
    """A value about a station's next train."""

    entity_description: MetraSensorDescription

    @property
    def native_value(self) -> Any:
        """Return the sensor value."""
        return self.entity_description.value_fn(self)


class MetraScheduleSensor(MetraSensor):
    """The next arrival, also holding the full schedule that the card displays."""

    # Changes every minute and isn't useful as history.
    _unrecorded_attributes = frozenset({"generatedAt", "filters", "nextFavorite", "routes"})

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the schedule for the card."""
        return {
            key: self.payload[key] for key in ("generatedAt", "filters", "nextFavorite", "routes")
        }
