"""Binary sensors for Portainer Update Manager."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PortainerUpdateManagerConfigEntry
from .entity import PortainerUpdateManagerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PortainerUpdateManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up bridge connectivity."""
    async_add_entities([BridgeConnectedBinarySensor(entry.runtime_data)])


class BridgeConnectedBinarySensor(PortainerUpdateManagerEntity, BinarySensorEntity):
    """Represent the live event bridge connection."""

    _attr_translation_key = "bridge_connected"
    _attr_icon = "mdi:lan-connect"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_bridge_connected"

    @property
    @override
    def is_on(self) -> bool:
        return self.coordinator.data.bridge_connected

    @property
    @override
    def available(self) -> bool:
        # Connectivity is the *state* of this entity. Once a bridge client is
        # configured the entity itself should remain available even while the
        # bridge or Docker stream is disconnected.
        return self.coordinator.bridge_client is not None

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        last_event = self.coordinator.data.last_event
        return {
            "bridge_version": self.coordinator.data.bridge_version,
            "last_error": self.coordinator.data.bridge_error,
            "last_event": last_event.action if last_event else None,
            "last_event_at": (
                last_event.timestamp.isoformat() if last_event else None
            ),
        }
