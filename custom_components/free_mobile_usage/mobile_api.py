"""Structured client for the Free Mobile endpoints used by the iOS app."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import aiohttp

from .models import FreeMobileUsageData

BASE_URL = "https://mobile.free.fr/account/api"
TIMEOUT = aiohttp.ClientTimeout(total=30)

# These headers and per-endpoint ServiceLabel values are observed from the Free
# iOS app.  No device identifier or user-specific value is impersonated.
APP_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "User-Agent": "Free/1855 CFNetwork/3896.100.1.2.1 Darwin/27.0.0",
}


class FreeMobileApiError(Exception):
    """Base error returned by the Free Mobile API."""


class FreeMobileApiAuthError(FreeMobileApiError):
    """Credentials or a saved access token are no longer accepted."""


class FreeMobileApiMfaRequired(FreeMobileApiError):
    """A temporary SMS code is needed to complete login."""

    def __init__(self, access_token: str, otp_id: int) -> None:
        super().__init__("Free Mobile requires a temporary security code")
        self.access_token = access_token
        self.otp_id = otp_id


@dataclass
class MobileApiAuthState:
    """Authentication material persisted in the Home Assistant config entry."""

    access_token: str | None = None
    refresh_token: str | None = None
    trusted_uuid: str | None = None

    @classmethod
    def from_entry_data(cls, data: dict[str, Any]) -> "MobileApiAuthState":
        return cls(
            access_token=_as_str(data.get("access_token")),
            refresh_token=_as_str(data.get("refresh_token")),
            trusted_uuid=_as_str(data.get("trusted_uuid")),
        )

    def as_entry_data(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "access_token": self.access_token,
                "refresh_token": self.refresh_token,
                "trusted_uuid": self.trusted_uuid,
            }.items()
            if value
        }

    def update(self, payload: dict[str, Any]) -> None:
        self.access_token = _as_str(payload.get("accessToken")) or self.access_token
        self.refresh_token = _as_str(payload.get("refreshToken")) or self.refresh_token
        self.trusted_uuid = _as_str(payload.get("trustUuid")) or self.trusted_uuid


class FreeMobileMobileApiClient:
    """Fetch all family-line usage data through the structured mobile API."""

    def __init__(self, session: aiohttp.ClientSession, auth_state: MobileApiAuthState) -> None:
        self._session = session
        self.auth_state = auth_state

    async def async_authenticate(self, username: str, password: str) -> None:
        """Use a valid token first, then log in with the trusted device UUID."""
        if self.auth_state.access_token:
            try:
                await self.async_get_subscriber(username)
            except FreeMobileApiAuthError:
                self.auth_state.access_token = None
            else:
                return

        payload: dict[str, str] = {"username": username, "password": password}
        if self.auth_state.trusted_uuid:
            payload["trustedUuid"] = self.auth_state.trusted_uuid
        response = await self._async_post_json("/auth/login", payload, "MobAuthLogin")

        access_token = _required_str(response, "accessToken")
        # type2FA exists on trusted-device responses too. An otpId is the only
        # reliable indication that the user must supply an SMS code.
        otp_id = _otp_id(response)
        if _requires_mfa(response):
            if otp_id is None:
                raise FreeMobileApiError("Free Mobile requested SMS verification without a usable challenge identifier")
            raise FreeMobileApiMfaRequired(access_token, otp_id)
        self.auth_state.update(response)

    async def async_complete_mfa(self, challenge: FreeMobileApiMfaRequired, code: str) -> None:
        """Finish the temporary SMS challenge and mark this HA instance trusted."""
        response = await self._async_post_json(
            "/auth/otp",
            {"codeOtp": _otp_code(code), "idOtp": challenge.otp_id, "isTrusted": True},
            "MobAuthOTP",
            access_token=challenge.access_token,
        )
        self.auth_state.update(response)

    async def async_get_family_usage(self, username: str) -> dict[str, FreeMobileUsageData]:
        """Return one normalized usage object per family line."""
        subscriber = await self.async_get_subscriber(username)
        lines = subscriber.get("lines")
        if not isinstance(lines, list):
            raise FreeMobileApiError("Free Mobile did not return the family line list")

        results = await asyncio.gather(*(self._async_get_line_usage(line) for line in lines if isinstance(line, dict)))
        return {usage.line_id: usage for usage in results if usage is not None}

    async def async_get_subscriber(self, username: str) -> dict[str, Any]:
        return await self._async_get_json(f"/subscriber/{username}", "MobGetSubscriberInfo")

    async def _async_get_line_usage(self, line: dict[str, Any]) -> FreeMobileUsageData | None:
        line_id = _as_str(line.get("login"))
        if not line_id:
            return None
        consumption, offer = await asyncio.gather(
            self._async_get_json(f"/subscriber/{line_id}/consumption", "MobGetConsumption"),
            self._async_get_json(f"/subscriber/{line_id}/offer", "MobGetOffer"),
        )
        identity = line.get("identity") if isinstance(line.get("identity"), dict) else {}
        return FreeMobileUsageData.from_mobile_api(
            line_id=line_id,
            account_name=_as_str(identity.get("firstName")),
            phone_number=_as_str(line.get("msisdn")),
            plan_type=_as_str(offer.get("planType")),
            consumption=consumption,
            offer=offer,
        )

    def _headers(
        self, service_label: str, access_token: str | None = None, content_type: str | None = None
    ) -> dict[str, str]:
        headers = {**APP_HEADERS, "ServiceLabel": service_label}
        token = access_token or self.auth_state.access_token
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if service_label not in {"MobAuthLogin", "MobAuthOTP"}:
            headers["Service"] = "mobile"
            headers["X-Platform"] = "ios"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _async_get_json(self, path: str, service_label: str) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{BASE_URL}{path}", headers=self._headers(service_label), timeout=TIMEOUT
            ) as response:
                if response.status in {401, 403}:
                    raise FreeMobileApiAuthError("Free Mobile rejected the saved access token")
                response.raise_for_status()
                return await _response_json(response)
        except aiohttp.ClientError as err:
            raise FreeMobileApiError("Unable to connect to Free Mobile") from err

    async def _async_post_json(
        self, path: str, payload: dict[str, Any], service_label: str, access_token: str | None = None
    ) -> dict[str, Any]:
        try:
            async with self._session.post(
                f"{BASE_URL}{path}",
                json=payload,
                headers=self._headers(service_label, access_token, "application/json"),
                timeout=TIMEOUT,
            ) as response:
                if response.status in {401, 403}:
                    raise FreeMobileApiAuthError("Free Mobile rejected the supplied credentials or SMS code")
                response.raise_for_status()
                return await _response_json(response)
        except aiohttp.ClientError as err:
            raise FreeMobileApiError("Unable to connect to Free Mobile") from err


async def _response_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        payload = await response.json(content_type=None)
    except (aiohttp.ContentTypeError, ValueError) as err:
        raise FreeMobileApiError("Free Mobile returned an unexpected non-JSON response") from err
    if not isinstance(payload, dict):
        raise FreeMobileApiError("Free Mobile returned an unexpected JSON response")
    return payload


def _as_str(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _required_str(payload: dict[str, Any], key: str) -> str:
    value = _as_str(payload.get(key))
    if value is None:
        raise FreeMobileApiError(f"Free Mobile response is missing {key}")
    return value


def _otp_id(payload: dict[str, Any]) -> int | None:
    """Accept the OTP field spellings observed across Free API versions."""
    for key in ("otpId", "idOtp", "otp_id", "id_otp", "challengeId"):
        value = payload.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _otp_code(value: str) -> int:
    """Validate the code while matching the numeric payload used by the app."""
    if not value.isdigit():
        raise FreeMobileApiAuthError("The Free Mobile SMS code must contain digits only")
    return int(value)


def _requires_mfa(payload: dict[str, Any]) -> bool:
    """Interpret Free's type2FA flag without treating 'false' as truthy."""
    value = payload.get("type2FA")
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "no", "disabled", "not_required"}
    return bool(value)
