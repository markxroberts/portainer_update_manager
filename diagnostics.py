"""Diagnostics for Portainer Update Manager."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from . import PortainerUpdateManagerConfigEntry
from .const import CONF_BRIDGE_TOKEN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant,
    entry: PortainerUpdateManagerConfigEntry,
) -> dict[str, Any]:
    """Return redacted diagnostics for a config entry."""
    coordinator = entry.runtime_data
    data = dict(entry.data)
    if CONF_BRIDGE_TOKEN in data:
        data[CONF_BRIDGE_TOKEN] = "**REDACTED**"
    return {
        "config_entry": {
            "title": entry.title,
            "version": entry.version,
            "data": data,
            "options": dict(entry.options),
        },
        "portainer_entry": {
            "entry_id": coordinator.portainer_entry.entry_id,
            "title": coordinator.portainer_entry.title,
            "state": coordinator.portainer_entry.state.value,
        },
        "bridge": {
            "configured": coordinator.data.bridge_configured,
            "connected": coordinator.data.bridge_connected,
            "version": coordinator.data.bridge_version,
            "error": coordinator.data.bridge_error,
            "container_count": len(coordinator.data.containers),
            "last_event": (
                {
                    "action": coordinator.data.last_event.action,
                    **coordinator.data.last_event.event_data(),
                }
                if coordinator.data.last_event
                else None
            ),
        },
        "updates": {
            key: {
                "entity_id": item.entity_id,
                "name": item.name,
                "available": item.available,
                "installed_version": item.installed_version,
                "latest_version": item.latest_version,
                "update_available": item.update_available,
                "in_progress": item.in_progress,
                "automatic_updates": item.automatic_updates,
                "first_seen": item.first_seen.isoformat() if item.first_seen else None,
                "eligible": item.eligible,
                "policy_reason": item.policy_reason,
                "last_attempt": (
                    item.last_attempt.isoformat() if item.last_attempt else None
                ),
                "last_result": item.last_result,
            }
            for key, item in coordinator.data.updates.items()
        },
    }
