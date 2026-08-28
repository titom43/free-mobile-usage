"""Coordinator for Free Mobile Usage."""

from __future__ import annotations

from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_SCAN_INTERVAL_HOURS,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL_HOURS,
    DOMAIN,
)
from .mobile_api import FreeMobileApiError, FreeMobileApiMfaRequired, FreeMobileMobileApiClient, MobileApiAuthState
from .models import FreeMobileUsageData


class FreeMobileUsageCoordinator(DataUpdateCoordinator[dict[str, FreeMobileUsageData]]):
    """Fetch Free Mobile usage data on a schedule."""

    config_entry: ConfigEntry

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        scan_interval_hours = entry.options.get(
            CONF_SCAN_INTERVAL_HOURS, DEFAULT_SCAN_INTERVAL_HOURS
        )
        super().__init__(
            hass,
            logger=__import__("logging").getLogger(__name__),
            name=DOMAIN,
            update_interval=timedelta(hours=scan_interval_hours),
            config_entry=entry,
        )
        self.auth_state = MobileApiAuthState.from_entry_data(dict(entry.data))
        self.entry = entry
        self.client = FreeMobileMobileApiClient(async_get_clientsession(hass), self.auth_state)

    async def _async_update_data(self) -> dict[str, FreeMobileUsageData]:
        """Fetch every line attached to the configured primary account."""
        try:
            await self.client.async_authenticate(self.entry.data[CONF_USERNAME], self.entry.data[CONF_PASSWORD])
            data = await self.client.async_get_family_usage(self.entry.data[CONF_USERNAME])
        except FreeMobileApiMfaRequired as err:
            raise UpdateFailed("Free Mobile requires a new SMS verification; reconfigure the integration") from err
        except FreeMobileApiError as err:
            raise UpdateFailed(str(err)) from err

        updated_entry_data = {**self.entry.data, **self.auth_state.as_entry_data()}
        if updated_entry_data != self.entry.data:
            self.hass.config_entries.async_update_entry(self.entry, data=updated_entry_data)
        return data
