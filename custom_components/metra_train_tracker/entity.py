"""Base entity for the Metra Train Tracker integration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigSubentry
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import MetraCoordinator


class MetraEntity(CoordinatorEntity[MetraCoordinator]):
    """An entity belonging to a configured station."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: MetraCoordinator,
        subentry: ConfigSubentry,
        description: EntityDescription,
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        self._subentry_id = subentry.subentry_id
        self._attr_unique_id = f"{subentry.subentry_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, subentry.subentry_id)},
            name=subentry.title,
            manufacturer="Unofficial community project",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def available(self) -> bool:
        """Return whether this station has data."""
        return super().available and self._subentry_id in self.coordinator.data

    @property
    def payload(self) -> dict[str, Any]:
        """Return the station's schedule payload."""
        return self.coordinator.data[self._subentry_id]

    @property
    def route(self) -> dict[str, Any] | None:
        """Return the station's route, which is absent with no trains or alerts."""
        routes = self.payload["routes"]
        return routes[0] if routes else None

    @property
    def next_trip(self) -> dict[str, Any] | None:
        """Return the next train to call at the station."""
        route = self.route
        return route["trips"][0] if route and route["trips"] else None

    @property
    def next_favorite_stop(self) -> dict[str, Any] | None:
        """Return the next favorite train's call at the station."""
        trip = self.payload["nextFavorite"]
        return trip["stops"][0] if trip else None

    @property
    def next_stop(self) -> dict[str, Any] | None:
        """Return the next train's call at the station."""
        trip = self.next_trip
        return trip["stops"][0] if trip else None
