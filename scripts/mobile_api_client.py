"""Small Free Mobile mobile API client used by the local smoke test.

This is deliberately independent from Home Assistant.  It mirrors the
requests made by the iOS application and keeps only the minimum state needed
to avoid an unnecessary SMS challenge on later runs.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

BASE_URL = "https://mobile.free.fr"
API_URL = f"{BASE_URL}/account/api"
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

# Values observed in the iOS application's requests.  The service label is
# endpoint-specific and is set by _headers().
APP_USER_AGENT = "Free/1855 CFNetwork/3896.100.1.2.1 Darwin/27.0.0"
APP_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-GB,en;q=0.9",
    "User-Agent": APP_USER_AGENT,
}


class FreeMobileApiError(Exception):
    """Base error returned by the mobile API client."""


class FreeMobileApiAuthError(FreeMobileApiError):
    """The current credentials or saved tokens are no longer accepted."""


class FreeMobileApiMfaRequired(FreeMobileApiError):
    """Free Mobile requires a one-time SMS code before issuing final tokens."""

    def __init__(self, access_token: str, otp_id: int) -> None:
        super().__init__("Free Mobile requires a temporary security code.")
        self.access_token = access_token
        self.otp_id = otp_id


@dataclass
class TokenState:
    """Persisted authentication state.  This file must remain private."""

    access_token: str | None = None
    refresh_token: str | None = None
    trusted_uuid: str | None = None
    account_login: str | None = None
    saved_at: str | None = None

    @classmethod
    def load(cls, path: Path) -> "TokenState":
        if not path.exists():
            return cls()
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError, json.JSONDecodeError):
            return cls()
        return cls(
            access_token=raw.get("access_token"),
            refresh_token=raw.get("refresh_token"),
            trusted_uuid=raw.get("trusted_uuid"),
            account_login=raw.get("account_login"),
            saved_at=raw.get("saved_at"),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.__dict__, indent=2) + "\n")
        path.chmod(0o600)

    def update_tokens(self, payload: dict[str, Any], account_login: str) -> None:
        self.access_token = _string_or_none(payload.get("accessToken")) or self.access_token
        self.refresh_token = _string_or_none(payload.get("refreshToken")) or self.refresh_token
        self.trusted_uuid = _string_or_none(payload.get("trustUuid")) or self.trusted_uuid
        self.account_login = account_login
        self.saved_at = datetime.now(timezone.utc).isoformat()


class FreeMobileMobileApiClient:
    """Minimal client for the endpoints used by the Free iOS app."""

    def __init__(self, session: aiohttp.ClientSession, state: TokenState) -> None:
        self.session = session
        self.state = state

    async def async_authenticate(self, username: str | None, password: str | None) -> str:
        """Return the active account login, using saved access state first."""
        account_login = username or self.state.account_login or _login_from_token(self.state.access_token)
        if self.state.access_token and account_login:
            try:
                await self.async_get_subscriber(account_login)
            except FreeMobileApiAuthError:
                # An expired token is normal.  Login below can still use the
                # trusted device UUID to avoid a second SMS challenge.
                self.state.access_token = None
            else:
                self.state.account_login = account_login
                return account_login

        if not username or not password:
            raise FreeMobileApiAuthError(
                "Set FREEMOBILE_USERNAME and FREEMOBILE_PASSWORD when no saved token is valid"
            )

        payload: dict[str, str] = {"username": username, "password": password}
        if self.state.trusted_uuid:
            payload["trustedUuid"] = self.state.trusted_uuid
        result = await self._async_post_json("/auth/login", payload, service_label="MobAuthLogin")

        access_token = _required_string(result, "accessToken")
        # Free currently includes type2FA in both challenge and trusted-device
        # responses.  otpId is the reliable signal that an SMS code is needed.
        otp_id = _integer_or_none(result.get("otpId"))
        if otp_id is not None:
            raise FreeMobileApiMfaRequired(access_token, otp_id)

        self.state.update_tokens(result, username)
        return username

    async def async_complete_mfa(self, account_login: str, challenge: FreeMobileApiMfaRequired, code: str) -> None:
        """Complete an OTP challenge and persist the trusted-device response."""
        result = await self._async_post_json(
            "/auth/otp",
            {"codeOtp": _otp_code(code), "idOtp": challenge.otp_id, "isTrusted": True},
            service_label="MobAuthOTP",
            access_token=challenge.access_token,
        )
        self.state.update_tokens(result, account_login)

    async def async_get_subscriber(self, account_login: str) -> dict[str, Any]:
        return await self._async_get_json(
            f"/subscriber/{account_login}", service_label="MobGetSubscriberInfo"
        )

    async def async_get_usage(self, line_login: str) -> dict[str, Any]:
        """Fetch structured consumption and allowance data for one family line."""
        consumption = await self._async_get_json(
            f"/subscriber/{line_login}/consumption", service_label="MobGetConsumption"
        )
        offer = await self._async_get_json(
            f"/subscriber/{line_login}/offer", service_label="MobGetOffer"
        )
        return _usage_summary(consumption, offer)

    def _headers(
        self,
        service_label: str,
        access_token: str | None = None,
        content_type: str | None = None,
    ) -> dict[str, str]:
        headers = {**APP_HEADERS, "ServiceLabel": service_label}
        if access_token or self.state.access_token:
            headers["Authorization"] = f"Bearer {access_token or self.state.access_token}"
        if service_label not in {"MobAuthLogin", "MobAuthOTP"}:
            headers["Service"] = "mobile"
            headers["X-Platform"] = "ios"
        if content_type:
            headers["Content-Type"] = content_type
        return headers

    async def _async_get_json(self, path: str, service_label: str) -> dict[str, Any]:
        async with self.session.get(
            f"{API_URL}{path}",
            headers=self._headers(service_label),
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            if response.status in {401, 403}:
                raise FreeMobileApiAuthError("Free Mobile rejected the saved access token")
            response.raise_for_status()
            return await _response_json(response)

    async def _async_post_json(
        self,
        path: str,
        payload: dict[str, Any],
        service_label: str,
        access_token: str | None = None,
    ) -> dict[str, Any]:
        async with self.session.post(
            f"{API_URL}{path}",
            json=payload,
            headers=self._headers(service_label, access_token, "application/json"),
            timeout=DEFAULT_TIMEOUT,
        ) as response:
            if response.status in {401, 403}:
                raise FreeMobileApiAuthError("Free Mobile rejected the supplied credentials or SMS code")
            response.raise_for_status()
            return await _response_json(response)


async def _response_json(response: aiohttp.ClientResponse) -> dict[str, Any]:
    try:
        payload = await response.json(content_type=None)
    except (aiohttp.ContentTypeError, json.JSONDecodeError) as err:
        raise FreeMobileApiError("Free Mobile returned an unexpected non-JSON response") from err
    if not isinstance(payload, dict):
        raise FreeMobileApiError("Free Mobile returned an unexpected JSON response")
    return payload


def _usage_summary(consumption: dict[str, Any], offer: dict[str, Any]) -> dict[str, Any]:
    national_limit = _fair_use_gb(offer.get("nationalData", {}).get("fairuse"))
    roaming_limit = _fair_use_gb(offer.get("roamingData", {}).get("fairuse"))
    national_used = _bytes_to_gb(consumption.get("national", {}).get("consumption", {}).get("data"))
    roaming_used = _bytes_to_gb(consumption.get("roaming", {}).get("consumption", {}).get("data"))

    return {
        "plan_type": offer.get("planType"),
        "national_data_used_gb": national_used,
        "national_data_limit_gb": national_limit,
        "national_data_remaining_gb": _remaining(national_limit, national_used),
        "roaming_data_used_gb": roaming_used,
        "roaming_data_limit_gb": roaming_limit,
        "roaming_data_remaining_gb": _remaining(roaming_limit, roaming_used),
        "period_start": consumption.get("startPeriod"),
        "period_end": consumption.get("endPeriod"),
        "period_reset": consumption.get("resetPeriod"),
    }


def _bytes_to_gb(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) / 1_000_000_000, 6)


def _fair_use_gb(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return _bytes_to_gb(value)


def _remaining(limit: float | None, used: float | None) -> float | None:
    if limit is None or used is None:
        return None
    return round(max(limit - used, 0), 6)


def _required_string(payload: dict[str, Any], name: str) -> str:
    value = _string_or_none(payload.get(name))
    if not value:
        raise FreeMobileApiError(f"Free Mobile response is missing {name}")
    return value


def _string_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def _integer_or_none(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _otp_code(value: str) -> int:
    if not value.isdigit():
        raise FreeMobileApiAuthError("The Free Mobile SMS code must contain digits only")
    return int(value)


def _login_from_token(token: str | None) -> str | None:
    """Read the account identifier from the unverified JWT payload locally."""
    if not token or token.count(".") < 2:
        return None
    try:
        encoded = token.split(".", 2)[1]
        decoded = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        payload = json.loads(decoded)
    except (ValueError, UnicodeDecodeError):
        return None
    return _string_or_none(payload.get("customerLogin"))
