"""Config flow for the Metra Train Tracker integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    SubentryFlowResult,
)
from homeassistant.const import CONF_API_KEY
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import MetraApi, MetraAuthError, MetraError
from .const import (
    CONF_DIRECTION,
    CONF_IGNORE_TERMS,
    CONF_LINE,
    CONF_NEXT,
    CONF_STATION,
    DEFAULT_NEXT,
    DIRECTION_BOTH,
    DIRECTION_INBOUND,
    DIRECTION_OUTBOUND,
    DOMAIN,
    MAX_NEXT,
    SUBENTRY_STATION,
)
from .gtfs import GtfsSchedule

API_KEY_SCHEMA = vol.Schema(
    {vol.Required(CONF_API_KEY): TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))}
)


class MetraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Collects the API key. Stations are added afterwards as subentries."""

    VERSION = 1

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        """Return the subentry types this integration supports."""
        return {SUBENTRY_STATION: StationSubentryFlow}

    async def _async_validate_key(self, api_key: str) -> dict[str, str]:
        try:
            await MetraApi(self.hass, api_key).async_validate_key()
        except MetraAuthError:
            return {"base": "invalid_auth"}
        except MetraError:
            return {"base": "cannot_connect"}
        return {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate_key(user_input[CONF_API_KEY])
            if not errors:
                return self.async_create_entry(title="Metra Train Tracker", data=user_input)
        return self.async_show_form(step_id="user", data_schema=API_KEY_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after Metra rejects the key."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for a replacement API key."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate_key(user_input[CONF_API_KEY])
            if not errors:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(), data_updates=user_input
                )
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=API_KEY_SCHEMA, errors=errors
        )


class StationSubentryFlow(ConfigSubentryFlow):
    """Adds or reconfigures a station: line first, then the station on that line."""

    def __init__(self) -> None:
        """Initialize the flow."""
        self._line: str | None = None

    @property
    def _is_reconfigure(self) -> bool:
        return self.source == "reconfigure"

    def _defaults(self) -> Mapping[str, Any]:
        return self._get_reconfigure_subentry().data if self._is_reconfigure else {}

    def _get_schedule(self) -> GtfsSchedule | None:
        entry = self._get_entry()
        return entry.runtime_data.schedule if entry.state is ConfigEntryState.LOADED else None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        """Choose a line."""
        schedule = self._get_schedule()
        if schedule is None:
            return self.async_abort(reason="not_loaded")
        if user_input is not None:
            self._line = user_input[CONF_LINE]
            return await self.async_step_station()

        options = [
            SelectOptionDict(value=route.route_id, label=f"{route.long_name} ({route.short_name})")
            for route in sorted(schedule.routes.values(), key=lambda item: item.long_name)
            if schedule.stops_by_route.get(route.route_id)
        ]
        schema = vol.Schema(
            {
                vol.Required(CONF_LINE, default=self._defaults().get(CONF_LINE, vol.UNDEFINED)): (
                    SelectSelector(
                        SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN)
                    )
                )
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema)

    async_step_reconfigure = async_step_user

    async def async_step_station(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        """Choose a station, direction and display options."""
        schedule = self._get_schedule()
        if schedule is None or self._line is None:
            return self.async_abort(reason="not_loaded")
        stations = schedule.stations_for_route(self._line)

        errors: dict[str, str] = {}
        if user_input is not None:
            data = {CONF_LINE: self._line, **user_input}
            data[CONF_NEXT] = int(data[CONF_NEXT])
            unique_id = "_".join((self._line, data[CONF_STATION], data[CONF_DIRECTION])).casefold()
            if self._is_duplicate(unique_id):
                errors["base"] = "already_configured"
            else:
                title = self._build_title(schedule, stations, data)
                if self._is_reconfigure:
                    return self.async_update_and_abort(
                        self._get_entry(),
                        self._get_reconfigure_subentry(),
                        title=title,
                        data=data,
                        unique_id=unique_id,
                    )
                return self.async_create_entry(title=title, data=data, unique_id=unique_id)

        defaults = user_input or self._defaults()
        station = defaults.get(CONF_STATION)
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_STATION, default=station if station in stations else vol.UNDEFINED
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[
                            SelectOptionDict(value=stop_id, label=name)
                            for stop_id, name in stations.items()
                        ],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_DIRECTION, default=defaults.get(CONF_DIRECTION, DIRECTION_BOTH)
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=[DIRECTION_BOTH, DIRECTION_INBOUND, DIRECTION_OUTBOUND],
                        mode=SelectSelectorMode.DROPDOWN,
                        translation_key=CONF_DIRECTION,
                    )
                ),
                vol.Required(CONF_NEXT, default=defaults.get(CONF_NEXT, DEFAULT_NEXT)): (
                    NumberSelector(
                        NumberSelectorConfig(
                            min=1, max=MAX_NEXT, step=1, mode=NumberSelectorMode.BOX
                        )
                    )
                ),
                vol.Optional(
                    CONF_IGNORE_TERMS, default=defaults.get(CONF_IGNORE_TERMS, [])
                ): TextSelector(TextSelectorConfig(multiple=True)),
            }
        )
        return self.async_show_form(step_id="station", data_schema=schema, errors=errors)

    def _is_duplicate(self, unique_id: str) -> bool:
        current = self._get_reconfigure_subentry() if self._is_reconfigure else None
        return any(
            subentry.unique_id == unique_id and subentry is not current
            for subentry in self._get_entry().subentries.values()
        )

    def _build_title(
        self, schedule: GtfsSchedule, stations: dict[str, str], data: dict[str, Any]
    ) -> str:
        line = schedule.routes[self._line].short_name or self._line
        title = f"{line} {stations.get(data[CONF_STATION], data[CONF_STATION])}"
        if data[CONF_DIRECTION] != DIRECTION_BOTH:
            title = f"{title} {data[CONF_DIRECTION].capitalize()}"
        return title
