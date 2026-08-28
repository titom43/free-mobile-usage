"""Sensor platform for Free Mobile Usage."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import FreeMobileUsageCoordinator
from .models import FreeMobileUsageData


@dataclass(frozen=True, kw_only=True)
class FreeMobileSensorDescription(SensorEntityDescription):
    """Description of a Free Mobile sensor."""

    value_fn: Callable[[FreeMobileUsageData], str | float | date | datetime | None]


SENSORS: tuple[FreeMobileSensorDescription, ...] = (
    FreeMobileSensorDescription(
        key="data_used",
        translation_key="data_used",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.data_used_gb,
    ),
    FreeMobileSensorDescription(
        key="data_remaining",
        translation_key="data_remaining",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.data_remaining_gb,
    ),
    FreeMobileSensorDescription(
        key="data_used_percent",
        translation_key="data_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.data_used_percent,
    ),
    FreeMobileSensorDescription(
        key="out_of_plan",
        translation_key="out_of_plan",
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda data: data.out_of_plan_eur,
    ),
    FreeMobileSensorDescription(
        key="national_data_used",
        translation_key="national_data_used",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.national_data_used_gb,
    ),
    FreeMobileSensorDescription(
        key="national_data_limit",
        translation_key="national_data_limit",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.national_data_limit_gb,
    ),
    FreeMobileSensorDescription(
        key="roaming_data_used",
        translation_key="roaming_data_used",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.roaming_data_used_gb,
    ),
    FreeMobileSensorDescription(
        key="roaming_data_limit",
        translation_key="roaming_data_limit",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.roaming_data_limit_gb,
    ),
    FreeMobileSensorDescription(
        key="roaming_data_remaining",
        translation_key="roaming_data_remaining",
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        device_class=SensorDeviceClass.DATA_SIZE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.roaming_data_remaining_gb,
    ),
    FreeMobileSensorDescription(
        key="roaming_data_used_percent",
        translation_key="roaming_data_used_percent",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda data: data.roaming_data_used_percent,
    ),
    FreeMobileSensorDescription(
        key="last_update",
        translation_key="last_update",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda data: data.fetched_at,
    ),
    FreeMobileSensorDescription(
        key="next_reset_date",
        translation_key="next_reset_date",
        device_class=SensorDeviceClass.DATE,
        value_fn=lambda data: data.next_reset_date,
    ),
)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    """Set up sensors."""
    coordinator: FreeMobileUsageCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        FreeMobileUsageSensor(coordinator, entry, line_id, description)
        for line_id in coordinator.data
        for description in SENSORS
    )


class FreeMobileUsageSensor(CoordinatorEntity[FreeMobileUsageCoordinator], SensorEntity):
    """Free Mobile usage sensor."""

    entity_description: FreeMobileSensorDescription
    _attr_has_entity_name = True

    def __init__(
        self, coordinator: FreeMobileUsageCoordinator, entry: ConfigEntry, line_id: str, description: FreeMobileSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self._line_id = line_id
        self.entity_description = description
        self._attr_unique_id = f"{entry.entry_id}_{line_id}_{description.key}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, line_id)},
            "name": self._device_name,
            "manufacturer": "Free Mobile",
            "model": self._model_name,
        }

    @property
    def _line_data(self) -> FreeMobileUsageData | None:
        if self.coordinator.data is None:
            return None
        return self.coordinator.data.get(self._line_id)

    @property
    def _device_name(self) -> str:
        data = self._line_data
        return f"Free Mobile {data.account_name}" if data and data.account_name else "Free Mobile"

    @property
    def _model_name(self) -> str:
        data = self._line_data
        return data.plan_type.replace("_", " ") if data and data.plan_type else "Mobile plan"

    @property
    def native_value(self):
        """Return native value."""
        data = self._line_data
        if data is None:
            return None
        return self.entity_description.value_fn(data)

    @property
    def extra_state_attributes(self) -> dict[str, str | float | None]:
        """Return useful usage attributes."""
        data = self._line_data
        if data is None:
            return {}
        return {
            "account_name": data.account_name,
            "phone_number": data.phone_number,
            "plan_type": data.plan_type,
            "data_limit_gb": data.data_limit_gb,
            "national_data_used_gb": data.national_data_used_gb,
            "national_data_limit_gb": data.national_data_limit_gb,
            "roaming_data_used_gb": data.roaming_data_used_gb,
            "roaming_data_limit_gb": data.roaming_data_limit_gb,
            "roaming_data_remaining_gb": data.roaming_data_remaining_gb,
            "roaming_data_used_percent": data.roaming_data_used_percent,
            "voice_used": data.voice_used,
            "sms_used": data.sms_used,
            "mms_used": data.mms_used,
            "next_reset_date": data.next_reset_date.isoformat() if data.next_reset_date else None,
        }
