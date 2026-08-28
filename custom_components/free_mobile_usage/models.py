"""Data models for Free Mobile Usage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


@dataclass(frozen=True)
class FreeMobileUsageData:
    """Usage data parsed from the Free Mobile subscriber area."""

    line_id: str = ""
    account_name: str | None = None
    phone_number: str | None = None
    plan_type: str | None = None
    data_used_gb: float | None = None
    data_limit_gb: float | None = None
    national_data_used_gb: float | None = None
    national_data_limit_gb: float | None = None
    roaming_data_used_gb: float | None = None
    roaming_data_limit_gb: float | None = None
    out_of_plan_eur: float | None = None
    national_voice_seconds: float | None = None
    national_international_voice_seconds: float | None = None
    roaming_outgoing_voice_seconds: float | None = None
    roaming_incoming_voice_seconds: float | None = None
    national_sms: int | None = None
    national_mms: int | None = None
    roaming_sms: int | None = None
    roaming_mms: int | None = None
    # Kept for the legacy subscriber-area parser, which only exposes text summaries.
    voice_used: str | None = None
    sms_used: str | None = None
    mms_used: str | None = None
    next_reset_date: date | None = None
    raw_summary: str | None = None
    fetched_at: datetime | None = None

    @classmethod
    def from_mobile_api(
        cls,
        *,
        line_id: str,
        account_name: str | None,
        phone_number: str | None,
        plan_type: str | None,
        consumption: dict[str, Any],
        offer: dict[str, Any],
    ) -> "FreeMobileUsageData":
        """Normalize the API byte counters and fair-use limits into GB."""
        national_used = _bytes_to_gb(_nested_value(consumption, "national", "consumption", "data"))
        roaming_used = _bytes_to_gb(_nested_value(consumption, "roaming", "consumption", "data"))
        national_limit = _fair_use_to_gb(_nested_value(offer, "nationalData", "fairuse"))
        roaming_limit = _fair_use_to_gb(_nested_value(offer, "roamingData", "fairuse"))
        reset = _to_date(consumption.get("resetPeriod"))

        billing_values = [
            value
            for scope in ("national", "roaming")
            for value in (_nested_value(consumption, scope, "billing", "data"),)
            if isinstance(value, (int, float))
        ]
        # The API does not document monetary units for non-zero billing fields.
        # A zero is unambiguous; a non-zero value remains unavailable.
        out_of_plan = 0.0 if billing_values and all(value == 0 for value in billing_values) else None
        national_voice = _nested_value(consumption, "national", "consumption", "voice")
        roaming_voice = _nested_value(consumption, "roaming", "consumption", "voice")

        return cls(
            line_id=line_id,
            account_name=account_name,
            phone_number=phone_number,
            plan_type=plan_type,
            data_used_gb=national_used,
            data_limit_gb=national_limit,
            national_data_used_gb=national_used,
            national_data_limit_gb=national_limit,
            roaming_data_used_gb=roaming_used,
            roaming_data_limit_gb=roaming_limit,
            out_of_plan_eur=out_of_plan,
            national_voice_seconds=_number_or_none(_nested_value(national_voice, "nationalVoiceTime")),
            national_international_voice_seconds=_number_or_none(
                _nested_value(national_voice, "internationalVoiceTime")
            ),
            roaming_outgoing_voice_seconds=_number_or_none(
                _nested_value(roaming_voice, "roamingOutgoingVoiceTime")
            ),
            roaming_incoming_voice_seconds=_number_or_none(
                _nested_value(roaming_voice, "roamingIncomingVoiceTime")
            ),
            national_sms=_integer_or_none(_nested_value(consumption, "national", "consumption", "sms")),
            national_mms=_integer_or_none(_nested_value(consumption, "national", "consumption", "mms")),
            roaming_sms=_integer_or_none(_nested_value(consumption, "roaming", "consumption", "sms")),
            roaming_mms=_integer_or_none(_nested_value(consumption, "roaming", "consumption", "mms")),
            next_reset_date=reset,
            fetched_at=datetime.now(timezone.utc),
        )

    @property
    def data_remaining_gb(self) -> float | None:
        """Return remaining data allowance."""
        if self.data_used_gb is None or self.data_limit_gb is None:
            return None
        return max(self.data_limit_gb - self.data_used_gb, 0.0)

    @property
    def data_used_percent(self) -> float | None:
        """Return used data percent."""
        if self.data_used_gb is None or not self.data_limit_gb:
            return None
        return round((self.data_used_gb / self.data_limit_gb) * 100, 1)

    @property
    def roaming_data_remaining_gb(self) -> float | None:
        """Return remaining roaming data allowance."""
        if self.roaming_data_used_gb is None or self.roaming_data_limit_gb is None:
            return None
        return max(self.roaming_data_limit_gb - self.roaming_data_used_gb, 0.0)

    @property
    def roaming_data_used_percent(self) -> float | None:
        """Return used roaming data percent."""
        if self.roaming_data_used_gb is None or not self.roaming_data_limit_gb:
            return None
        return round((self.roaming_data_used_gb / self.roaming_data_limit_gb) * 100, 1)


def _nested_value(payload: dict[str, Any], *keys: str) -> Any:
    value: Any = payload
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _number_or_none(value: Any) -> float | None:
    """Return a numeric API counter when present."""
    return float(value) if isinstance(value, (int, float)) else None


def _integer_or_none(value: Any) -> int | None:
    """Return an integer API counter when present."""
    return value if isinstance(value, int) else None


def _bytes_to_gb(value: Any) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    return round(float(value) / 1_000_000_000, 6)


def _fair_use_to_gb(value: Any) -> float | None:
    if not isinstance(value, (int, float)) or value < 0:
        return None
    return _bytes_to_gb(value)


def _to_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None
