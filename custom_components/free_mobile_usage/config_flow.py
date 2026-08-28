"""Config flow for Free Mobile Usage."""

from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CONF_USERNAME, DOMAIN
from .mobile_api import FreeMobileApiAuthError, FreeMobileApiError, FreeMobileApiMfaRequired, FreeMobileMobileApiClient, MobileApiAuthState


class FreeMobileUsageConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Free Mobile Usage."""

    VERSION = 2

    _pending_client: FreeMobileMobileApiClient | None = None
    _pending_challenge: FreeMobileApiMfaRequired | None = None
    _pending_data: dict[str, str] | None = None

    async def async_step_user(self, user_input: dict | None = None):
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            username = user_input[CONF_USERNAME]
            await self.async_set_unique_id(username)
            self._abort_if_unique_id_configured()

            client = FreeMobileMobileApiClient(async_get_clientsession(self.hass), MobileApiAuthState())

            try:
                await client.async_authenticate(username, user_input[CONF_PASSWORD])
                # Do not create an entry until the returned access token has
                # completed an actual family-line query.
                await client.async_get_family_usage(username)
            except FreeMobileApiMfaRequired as challenge:
                self._pending_client = client
                self._pending_challenge = challenge
                self._pending_data = dict(user_input)
                return await self.async_step_mfa()
            except FreeMobileApiAuthError:
                errors["base"] = "invalid_auth"
            except FreeMobileApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001 - config flow must not crash on portal changes
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(title="Free Mobile", data={**user_input, **client.auth_state.as_entry_data()})

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): str,
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_mfa(self, user_input: dict | None = None):
        """Finish the optional Free Mobile SMS verification."""
        if not self._pending_client or not self._pending_challenge or not self._pending_data:
            return self.async_abort(reason="mfa_session_expired")

        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                await self._pending_client.async_complete_mfa(self._pending_challenge, user_input["code"])
                await self._pending_client.async_get_family_usage(self._pending_data[CONF_USERNAME])
            except FreeMobileApiAuthError:
                errors["base"] = "invalid_mfa"
            except FreeMobileApiError:
                errors["base"] = "cannot_connect"
            else:
                entry_data = {**self._pending_data, **self._pending_client.auth_state.as_entry_data()}
                return self.async_create_entry(title="Free Mobile", data=entry_data)

        return self.async_show_form(
            step_id="mfa",
            data_schema=vol.Schema({vol.Required("code"): str}),
            errors=errors,
        )
