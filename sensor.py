"""Summary sensors for Portainer Update Manager."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PortainerUpdateManagerConfigEntry
from .entity import PortainerUpdateManagerEntity
from .models import CoordinatorData


@dataclass(frozen=True, kw_only=True)
class ManagerSensorDescription(SensorEntityDescription):
    """Describe a manager summary sensor."""

    value_fn: Callable[[CoordinatorData], int]


SENSORS: tuple[ManagerSensorDescription, ...] = (
    ManagerSensorDescription(
        key="container_count",
        translation_key="container_count",
        icon="mdi:docker",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: len(data.containers),
    ),
    ManagerSensorDescription(
        key="pending_updates",
        translation_key="pending_updates",
        icon="mdi:package-up",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: sum(
            item.update_available for item in data.updates.values()
        ),
    ),
    ManagerSensorDescription(
        key="automatic_updates_enabled",
        translation_key="automatic_updates_enabled",
        icon="mdi:autorenew",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: sum(
            item.automatic_updates for item in data.updates.values()
        ),
    ),
    ManagerSensorDescription(
        key="eligible_updates",
        translation_key="eligible_updates",
        icon="mdi:progress-download",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda data: sum(item.eligible for item in data.updates.values()),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PortainerUpdateManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up summary sensors."""
    async_add_entities(
        PortainerUpdateManagerSensor(entry.runtime_data, description)
        for description in SENSORS
    )


class PortainerUpdateManagerSensor(PortainerUpdateManagerEntity, SensorEntity):
    """Represent a summary count."""

    entity_description: ManagerSensorDescription

    def __init__(self, coordinator, description: ManagerSensorDescription) -> None:
        """Initialize the sensor."""
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{description.key}"
        )

    @property
    @override
    def native_value(self) -> int:
        """Return the current count."""
        return self.entity_description.value_fn(self.coordinator.data)
