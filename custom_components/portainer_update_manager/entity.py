"""Base entities for Portainer Update Manager."""

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import PortainerUpdateCoordinator


class PortainerUpdateManagerEntity(CoordinatorEntity[PortainerUpdateCoordinator]):
    """Base entity for the integration."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: PortainerUpdateCoordinator) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.config_entry.entry_id)},
            name=coordinator.config_entry.title,
            manufacturer="Home Assistant Community",
            model="Portainer update and Docker event manager",
            entry_type=DeviceEntryType.SERVICE,
            sw_version="0.5.3",
        )
