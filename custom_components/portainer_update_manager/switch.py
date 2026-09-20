"""Automatic-update switches for Portainer Update Manager."""

from __future__ import annotations

from typing import Any, override

from homeassistant.components.switch import DOMAIN as SWITCH_DOMAIN, SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PortainerUpdateManagerConfigEntry
from .const import (
    ATTR_ELIGIBLE,
    ATTR_FIRST_SEEN,
    ATTR_INSTALLED_VERSION,
    ATTR_LAST_ATTEMPT,
    ATTR_LAST_RESULT,
    ATTR_LATEST_VERSION,
    ATTR_POLICY_REASON,
    ATTR_SOURCE_ENTITY,
    ATTR_UPDATE_AVAILABLE,
    DOMAIN,
)
from .entity import PortainerUpdateManagerEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PortainerUpdateManagerConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up automatic-update switches and remove stale ones."""
    coordinator = entry.runtime_data
    known: set[str] = set()
    registry = er.async_get(hass)

    # Remove registry entries left by containers which were already stale before
    # this platform loaded. Current entities are then added normally below.
    current_keys = set(coordinator.data.updates)
    prefix = f"{entry.entry_id}_"
    suffix = "_automatic_updates"
    for registry_entry in list(
        er.async_entries_for_config_entry(registry, entry.entry_id)
    ):
        if (
            registry_entry.domain != SWITCH_DOMAIN
            or registry_entry.platform != DOMAIN
            or not registry_entry.unique_id.startswith(prefix)
            or not registry_entry.unique_id.endswith(suffix)
        ):
            continue
        key = registry_entry.unique_id[len(prefix) : -len(suffix)]
        if key not in current_keys:
            registry.async_remove(registry_entry.entity_id)

    @callback
    def _async_sync_entities() -> None:
        current_keys = set(coordinator.data.updates)

        # Dynamic entity platforms do not automatically remove entities when the
        # coordinator stops returning them. Remove the registry entry explicitly so
        # permanently deleted containers do not remain as unavailable tombstones.
        removed_keys = known - current_keys
        for key in removed_keys:
            unique_id = f"{entry.entry_id}_{key}_automatic_updates"
            entity_id = registry.async_get_entity_id(
                SWITCH_DOMAIN, DOMAIN, unique_id
            )
            if entity_id is not None:
                registry.async_remove(entity_id)
        known.difference_update(removed_keys)

        new_keys = current_keys - known
        if new_keys:
            async_add_entities(
                AutomaticContainerUpdateSwitch(coordinator, key)
                for key in new_keys
            )
            known.update(new_keys)

    _async_sync_entities()
    entry.async_on_unload(coordinator.async_add_listener(_async_sync_entities))


class AutomaticContainerUpdateSwitch(PortainerUpdateManagerEntity, SwitchEntity):
    """Control automatic installation for a native Portainer update entity."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:autorenew"

    def __init__(self, coordinator, key: str) -> None:
        """Initialize the switch."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_{key}_automatic_updates"
        )

    @property
    @override
    def name(self) -> str:
        """Return the entity name."""
        item = self.coordinator.data.updates.get(self._key)
        return f"{item.name if item else self._key} automatic updates"

    @property
    @override
    def available(self) -> bool:
        """Return whether the source Portainer update entity exists."""
        item = self.coordinator.data.updates.get(self._key)
        # This is a policy control rather than a mirror of the source entity.
        # Keep it operable while Portainer is briefly unavailable/recreating a
        # container; permanently removed containers are purged after the grace
        # period by the coordinator.
        return super().available and item is not None

    @property
    @override
    def is_on(self) -> bool:
        """Return whether automatic updates are enabled."""
        return self.coordinator.automatic_updates_enabled(self._key)

    @property
    @override
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return current update and policy details."""
        item = self.coordinator.data.updates.get(self._key)
        if item is None:
            return {}
        return {
            ATTR_SOURCE_ENTITY: item.entity_id,
            ATTR_INSTALLED_VERSION: item.installed_version,
            ATTR_LATEST_VERSION: item.latest_version,
            ATTR_UPDATE_AVAILABLE: item.update_available,
            ATTR_ELIGIBLE: item.eligible,
            ATTR_POLICY_REASON: item.policy_reason,
            ATTR_FIRST_SEEN: item.first_seen.isoformat() if item.first_seen else None,
            ATTR_LAST_ATTEMPT: (
                item.last_attempt.isoformat() if item.last_attempt else None
            ),
            ATTR_LAST_RESULT: item.last_result,
        }

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable automatic updates."""
        await self.coordinator.async_set_automatic_updates(self._key, True)

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable automatic updates."""
        await self.coordinator.async_set_automatic_updates(self._key, False)
