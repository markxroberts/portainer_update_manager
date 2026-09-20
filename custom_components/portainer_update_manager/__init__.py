"""The Portainer Update Manager integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store

from .api import DockerEventBridgeClient
from .const import (
    ALL_PLATFORMS,
    CONF_BRIDGE_TOKEN,
    CONF_BRIDGE_URL,
    CONF_PORTAINER_ENTRY_ID,
    CONF_VERIFY_SSL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    LEGACY_CONF_EVENT_ENTITIES,
    STORAGE_KEY,
    STORAGE_VERSION,
)
from .coordinator import PortainerUpdateCoordinator


type PortainerUpdateManagerConfigEntry = ConfigEntry[PortainerUpdateCoordinator]


def _remove_legacy_event_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove all legacy v0.2-v0.4 per-container event entities."""
    registry = er.async_get(hass)
    # The integration no longer exposes EventEntity at all. Scan the registry by
    # platform rather than only this config-entry ID so disabled/orphaned entries
    # from older versions are also removed if their config-entry association was
    # lost during an earlier migration.
    for registry_entry in list(registry.entities.values()):
        if registry_entry.domain == "event" and registry_entry.platform == DOMAIN:
            registry.async_remove(registry_entry.entity_id)


async def async_setup_entry(
    hass: HomeAssistant, entry: PortainerUpdateManagerConfigEntry
) -> bool:
    """Set up Portainer Update Manager from a config entry."""
    portainer_entry_id = entry.data[CONF_PORTAINER_ENTRY_ID]
    portainer_entry = hass.config_entries.async_get_entry(portainer_entry_id)
    if portainer_entry is None or portainer_entry.state is not ConfigEntryState.LOADED:
        raise ConfigEntryNotReady("The selected Portainer config entry is not loaded")

    # Defence in depth for upgrades where an old disabled EventEntity remains in
    # the registry. v0.5 no longer exposes per-container event entities at all.
    _remove_legacy_event_entities(hass, entry)

    bridge_client: DockerEventBridgeClient | None = None
    if bridge_url := entry.data.get(CONF_BRIDGE_URL):
        session = async_get_clientsession(
            hass,
            verify_ssl=bool(entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)),
        )
        bridge_client = DockerEventBridgeClient(
            session,
            str(bridge_url),
            str(entry.data.get(CONF_BRIDGE_TOKEN, "")),
        )

    coordinator = PortainerUpdateCoordinator(
        hass, entry, portainer_entry, bridge_client
    )
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, ALL_PLATFORMS)
    if bridge_client is not None:
        entry.async_create_background_task(
            hass,
            coordinator.async_listen_bridge(),
            f"portainer_update_manager_bridge_{entry.entry_id}",
        )
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: PortainerUpdateManagerConfigEntry
) -> bool:
    """Unload a Portainer Update Manager config entry."""
    return await hass.config_entries.async_unload_platforms(entry, ALL_PLATFORMS)


async def async_remove_entry(
    hass: HomeAssistant, entry: PortainerUpdateManagerConfigEntry
) -> None:
    """Remove persisted automatic-update policy state."""
    store: Store[dict[str, object]] = Store(
        hass,
        STORAGE_VERSION,
        STORAGE_KEY.format(entry_id=entry.entry_id),
    )
    await store.async_remove()


async def async_migrate_entry(
    hass: HomeAssistant, entry: PortainerUpdateManagerConfigEntry
) -> bool:
    """Migrate earlier entries while retaining active policy state."""
    if entry.version < 5:
        options = dict(entry.options)
        options.pop(LEGACY_CONF_EVENT_ENTITIES, None)
        _remove_legacy_event_entities(hass, entry)
        hass.config_entries.async_update_entry(entry, version=5, options=options)
    return True
