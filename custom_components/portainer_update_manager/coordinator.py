"""Data update coordinator for Portainer Update Manager."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import logging
from typing import Any, override

from homeassistant.components import logbook
from homeassistant.components.update import (
    ATTR_IN_PROGRESS,
    ATTR_INSTALLED_VERSION,
    ATTR_LATEST_VERSION,
    ATTR_TITLE,
    DOMAIN as UPDATE_DOMAIN,
    SERVICE_INSTALL,
)
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import (
    ATTR_ENTITY_ID,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    BridgeAuthenticationError,
    BridgeConnectionError,
    BridgeProtocolError,
    DockerEventBridgeClient,
)
from .const import (
    CONF_PORTAINER_ENTRY_ID,
    CONF_SCAN_INTERVAL,
    CONF_UPDATE_DELAY_HOURS,
    CONF_WINDOW_END,
    CONF_WINDOW_START,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_UPDATE_DELAY_HOURS,
    DEFAULT_WINDOW_END,
    DEFAULT_WINDOW_START,
    DOCKER_EVENT_TYPES,
    DOMAIN,
    EVENT_DOCKER_EVENT,
    PORTAINER_DOMAIN,
    PORTAINER_REFRESH_ACTIONS,
    PORTAINER_REFRESH_DEBOUNCE,
    PORTAINER_REFRESH_SETTLE,
    REASON_DELAY,
    REASON_DISABLED,
    REASON_IN_PROGRESS,
    REASON_NO_UPDATE,
    REASON_OUTSIDE_WINDOW,
    REASON_READY,
    REASON_RETRY_COOLDOWN,
    REASON_SOURCE_UNAVAILABLE,
    RESULT_FAILED,
    RESULT_SUCCESS,
    RETRY_COOLDOWN,
    STORAGE_KEY,
    STORAGE_VERSION,
    STALE_CONTAINER_GRACE,
)
from .models import (
    BridgeContainer,
    BridgeEvent,
    BridgeSnapshot,
    CoordinatorData,
    ManagedContainerUpdate,
)
from .util import is_time_in_window, parse_time

_LOGGER = logging.getLogger(__name__)


class PortainerUpdateCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Coordinate Portainer update policy and Docker event push data."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        portainer_entry: ConfigEntry,
        bridge_client: DockerEventBridgeClient | None,
    ) -> None:
        """Initialize the coordinator."""
        scan_minutes = int(
            config_entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=scan_minutes),
            always_update=False,
        )
        self.config_entry = config_entry
        self.portainer_entry = portainer_entry
        self.bridge_client = bridge_client
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            STORAGE_KEY.format(entry_id=config_entry.entry_id),
        )
        self._automatic_updates: dict[str, bool] = {}
        self._seen: dict[str, dict[str, str]] = {}
        self._last_attempt: dict[str, dict[str, str]] = {}
        self._last_result: dict[str, str] = {}
        self._install_task: asyncio.Task[None] | None = None
        self._portainer_refresh_task: asyncio.Task[None] | None = None
        self._storage_dirty = False
        self._bridge_reauth_started = False
        self._bridge_stream_connected = False
        self._missing_sources: dict[str, datetime] = {}
        self._stale_check_tasks: dict[str, asyncio.Task[None]] = {}
        self._destroyed_containers: set[str] = set()

    @override
    async def _async_setup(self) -> None:
        """Load persistent policy state before the first refresh."""
        stored = await self._store.async_load() or {}
        automatic_updates = stored.get("automatic_updates", {})
        seen = stored.get("seen", {})
        last_attempt = stored.get("last_attempt", {})
        last_result = stored.get("last_result", {})

        self._automatic_updates = (
            {str(key): bool(value) for key, value in automatic_updates.items()}
            if isinstance(automatic_updates, dict)
            else {}
        )
        self._seen = (
            {
                str(key): dict(value)
                for key, value in seen.items()
                if isinstance(value, dict)
            }
            if isinstance(seen, dict)
            else {}
        )
        self._last_attempt = (
            {
                str(key): dict(value)
                for key, value in last_attempt.items()
                if isinstance(value, dict)
            }
            if isinstance(last_attempt, dict)
            else {}
        )
        self._last_result = (
            {str(key): str(value) for key, value in last_result.items()}
            if isinstance(last_result, dict)
            else {}
        )

    @override
    async def _async_update_data(self) -> CoordinatorData:
        """Poll bridge snapshot and read native Portainer update entities."""
        self._ensure_portainer_loaded()
        previous = self.data
        snapshot: BridgeSnapshot | None = None
        bridge_error: str | None = None
        if self.bridge_client is not None:
            try:
                snapshot = await self.bridge_client.async_get_snapshot()
            except BridgeAuthenticationError as err:
                raise ConfigEntryAuthFailed(
                    "Docker Event Bridge authentication failed"
                ) from err
            except (BridgeConnectionError, BridgeProtocolError) as err:
                bridge_error = str(err)
                _LOGGER.warning("Docker Event Bridge poll failed: %s", err)

        containers = (
            snapshot.containers
            if snapshot is not None
            else previous.containers
            if previous is not None
            else {}
        )
        if snapshot is not None:
            self._destroyed_containers.difference_update(snapshot.containers)
        data = await self._async_build_data(
            containers=containers,
            bridge_connected=(
                snapshot.docker_connected
                if snapshot is not None
                else bool(
                    self._bridge_stream_connected
                    and previous is not None
                    and previous.bridge_connected
                )
            ),
            bridge_version=(
                snapshot.version
                if snapshot
                else previous.bridge_version
                if previous
                else None
            ),
            bridge_error=(
                snapshot.last_docker_error if snapshot else bridge_error
            ),
            last_event=(previous.last_event if previous else None),
        )
        return data

    def _ensure_portainer_loaded(self) -> None:
        current_portainer_entry = self.hass.config_entries.async_get_entry(
            self.config_entry.data[CONF_PORTAINER_ENTRY_ID]
        )
        if (
            current_portainer_entry is None
            or current_portainer_entry.state is not ConfigEntryState.LOADED
        ):
            raise UpdateFailed("The selected Portainer config entry is not loaded")

    async def _async_build_data(
        self,
        *,
        containers: dict[str, BridgeContainer],
        bridge_connected: bool,
        bridge_version: str | None,
        bridge_error: str | None,
        last_event: BridgeEvent | None,
    ) -> CoordinatorData:
        """Build coordinated state from memory and Home Assistant states."""
        entity_registry = er.async_get(self.hass)
        device_registry = dr.async_get(self.hass)
        source_entries = [
            entry
            for entry in er.async_entries_for_config_entry(
                entity_registry, self.portainer_entry.entry_id
            )
            if entry.domain == UPDATE_DOMAIN and entry.platform == PORTAINER_DOMAIN
        ]

        bridge_authoritative = self._bridge_is_authoritative_for_sources(
            source_entries, device_registry
        )

        now_utc = dt_util.utcnow()
        now_local = dt_util.now()
        delay = timedelta(
            hours=int(
                self.config_entry.options.get(
                    CONF_UPDATE_DELAY_HOURS, DEFAULT_UPDATE_DELAY_HOURS
                )
            )
        )
        window_start = parse_time(
            self.config_entry.options.get(CONF_WINDOW_START, DEFAULT_WINDOW_START)
        )
        window_end = parse_time(
            self.config_entry.options.get(CONF_WINDOW_END, DEFAULT_WINDOW_END)
        )
        in_window = is_time_in_window(now_local.time(), window_start, window_end)

        updates: dict[str, ManagedContainerUpdate] = {}
        source_keys = {entry.unique_id for entry in source_entries}
        for registry_entry in source_entries:
            state = self.hass.states.get(registry_entry.entity_id)

            key = registry_entry.unique_id
            source_device = (
                device_registry.async_get(registry_entry.device_id)
                if registry_entry.device_id is not None
                else None
            )
            name = self._portainer_container_name(
                registry_entry, state, source_device
            )
            native_available = (
                state is not None
                and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            )

            source_missing = self._bridge_reports_container_missing(
                key=key,
                container_name=name,
                containers=containers,
                bridge_connected=bridge_connected,
                bridge_authoritative=bridge_authoritative,
                native_available=native_available,
                now_utc=now_utc,
            )
            if source_missing and self._source_missing_is_stale(key, now_utc):
                self._forget_policy_key(key)
                continue

            attributes = state.attributes if state is not None else {}
            installed_version = attributes.get(ATTR_INSTALLED_VERSION)
            latest_version = attributes.get(ATTR_LATEST_VERSION)
            available = native_available and not source_missing
            update_available = (
                state is not None and state.state == STATE_ON and not source_missing
            )
            in_progress = (
                bool(attributes.get(ATTR_IN_PROGRESS, False))
                and not source_missing
            )
            automatic_updates = self._automatic_updates.get(key, False)

            first_seen = self._update_first_seen(
                key,
                str(latest_version) if latest_version is not None else None,
                update_available,
                now_utc,
            )

            eligible, reason = self._evaluate_policy(
                automatic_updates=automatic_updates,
                available=available,
                update_available=update_available,
                in_progress=in_progress,
                first_seen=first_seen,
                latest_version=(
                    str(latest_version) if latest_version is not None else None
                ),
                last_attempt_record=self._last_attempt.get(key),
                now_utc=now_utc,
                delay=delay,
                in_window=in_window,
            )

            updates[key] = ManagedContainerUpdate(
                key=key,
                entity_id=registry_entry.entity_id,
                name=name,
                device_id=registry_entry.device_id,
                available=available,
                installed_version=(
                    str(installed_version) if installed_version is not None else None
                ),
                latest_version=(
                    str(latest_version) if latest_version is not None else None
                ),
                update_available=update_available,
                in_progress=in_progress,
                automatic_updates=automatic_updates,
                first_seen=first_seen,
                eligible=eligible,
                policy_reason=reason,
                last_attempt=self._parse_datetime(
                    (self._last_attempt.get(key) or {}).get("time")
                ),
                last_result=self._last_result.get(key),
            )

        # If the native Portainer entity itself has been deleted, its policy state
        # can be removed immediately. A temporary container recreation does not
        # remove that registry entry and is handled by the grace period above.
        for orphaned_key in set(self._automatic_updates) - source_keys:
            self._forget_policy_key(orphaned_key)
        for orphaned_key in set(self._missing_sources) - source_keys:
            self._clear_missing_source(orphaned_key)

        if self._storage_dirty:
            await self._async_save()

        data = CoordinatorData(
            updates=updates,
            containers=containers,
            bridge_configured=self.bridge_client is not None,
            bridge_connected=bridge_connected,
            bridge_version=bridge_version,
            bridge_error=bridge_error,
            last_event=last_event,
        )
        self._schedule_next_automatic_install(data.updates)
        return data

    def _portainer_container_name(
        self,
        registry_entry: er.RegistryEntry,
        state: Any,
        source_device: dr.DeviceEntry | None,
    ) -> str:
        """Return the container name represented by a Portainer update entity.

        Portainer's public entity unique ID is stable and is built from the
        Portainer config-entry ID, container name and ``container_image_update``.
        Prefer that over the state/friendly name so matching is unaffected by
        user renames, translations or a temporarily absent state object.
        """
        prefix = f"{self.portainer_entry.entry_id}_"
        suffix = "_container_image_update"
        unique_id = registry_entry.unique_id
        if unique_id.startswith(prefix) and unique_id.endswith(suffix):
            name = unique_id[len(prefix) : -len(suffix)]
            if name:
                return name

        if state is not None:
            title = state.attributes.get(ATTR_TITLE)
            if title:
                return str(title)
        if source_device is not None and source_device.name:
            return str(source_device.name)
        if state is not None and state.name:
            return str(state.name)
        return str(registry_entry.original_name or registry_entry.entity_id)

    @staticmethod
    def _bridge_is_authoritative_for_sources(
        source_entries: list[er.RegistryEntry],
        device_registry: dr.DeviceRegistry,
    ) -> bool:
        """Return whether one Portainer endpoint backs all managed sources.

        One bridge represents one Docker Engine. If a Portainer config entry manages
        several endpoints, absence from this bridge cannot safely prove that a
        container belonging to another endpoint was deleted.
        """
        endpoint_ids: set[str] = set()
        for source_entry in source_entries:
            if source_entry.device_id is None:
                continue
            device = device_registry.async_get(source_entry.device_id)
            seen: set[str] = set()
            while device is not None and device.id not in seen:
                seen.add(device.id)
                if device.via_device_id is None:
                    endpoint_ids.add(device.id)
                    break
                device = device_registry.async_get(device.via_device_id)
        return len(endpoint_ids) <= 1

    def _bridge_reports_container_missing(
        self,
        *,
        key: str,
        container_name: str,
        containers: dict[str, BridgeContainer],
        bridge_connected: bool,
        bridge_authoritative: bool,
        native_available: bool,
        now_utc: datetime,
    ) -> bool:
        """Track a Portainer source whose container is absent from the live bridge."""
        if (
            self.bridge_client is None
            or not bridge_connected
            or not bridge_authoritative
            or container_name in containers
        ):
            self._clear_missing_source(key)
            return False

        # A Docker destroy event is authoritative immediately. Otherwise require
        # Portainer to agree that its source entity is unavailable before beginning
        # stale cleanup; this avoids deleting sources from unrelated endpoints.
        if native_available and container_name not in self._destroyed_containers:
            self._clear_missing_source(key)
            return False

        if key not in self._missing_sources:
            self._missing_sources[key] = now_utc
            self._schedule_stale_check(key)
        return True

    def _source_missing_is_stale(self, key: str, now_utc: datetime) -> bool:
        """Return whether a missing container has exceeded the recreation grace."""
        missing_since = self._missing_sources.get(key)
        return (
            missing_since is not None
            and now_utc - missing_since >= STALE_CONTAINER_GRACE
        )

    def _schedule_stale_check(self, key: str) -> None:
        """Refresh after the grace period even when no further Docker event occurs."""
        task = self._stale_check_tasks.get(key)
        if task is not None and not task.done():
            return
        # This timer intentionally sleeps for the recreation grace period. It must
        # be a background task so Home Assistant does not wait for it during the
        # startup phase (or async_block_till_done). Config-entry background tasks
        # are also cancelled automatically when the entry unloads.
        self._stale_check_tasks[key] = self.config_entry.async_create_background_task(
            self.hass,
            self._async_stale_check(key),
            f"{DOMAIN}_stale_container_{key}",
        )

    async def _async_stale_check(self, key: str) -> None:
        """Request a coordinator refresh when a missing source becomes stale."""
        try:
            await asyncio.sleep(STALE_CONTAINER_GRACE.total_seconds())
            await self.async_request_refresh()
        finally:
            self._stale_check_tasks.pop(key, None)

    def _clear_missing_source(self, key: str) -> None:
        """Forget a transiently missing source and cancel its stale check."""
        self._missing_sources.pop(key, None)
        task = self._stale_check_tasks.pop(key, None)
        if (
            task is not None
            and not task.done()
            and task is not asyncio.current_task()
        ):
            task.cancel()

    def _forget_policy_key(self, key: str) -> None:
        """Remove persisted state for a permanently removed container."""
        changed = False
        for mapping in (
            self._automatic_updates,
            self._seen,
            self._last_attempt,
            self._last_result,
        ):
            if key in mapping:
                mapping.pop(key, None)
                changed = True
        if changed:
            self._storage_dirty = True

    async def async_listen_bridge(self) -> None:
        """Maintain the bridge WebSocket and apply push updates."""
        if self.bridge_client is None:
            return
        delay = 1
        while True:
            try:
                async for message in self.bridge_client.async_messages():
                    delay = 1
                    self._bridge_stream_connected = True
                    await self._async_handle_bridge_message(message)
            except asyncio.CancelledError:
                raise
            except BridgeAuthenticationError:
                self._bridge_stream_connected = False
                if not self._bridge_reauth_started:
                    self._bridge_reauth_started = True
                    self.config_entry.async_start_reauth(self.hass)
                await self._async_set_bridge_disconnected("Invalid bridge token")
                return
            except (BridgeConnectionError, BridgeProtocolError) as err:
                self._bridge_stream_connected = False
                _LOGGER.warning("Docker Event Bridge stream unavailable: %s", err)
                await self._async_set_bridge_disconnected(str(err))
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)

    async def _async_handle_bridge_message(self, message: dict[str, Any]) -> None:
        """Handle a hello, status, snapshot, or event message."""
        message_type = message.get("type")
        if message_type == "hello":
            if self.data is not None:
                connected = bool(message.get("docker_connected", False))
                version = (
                    str(message["version"]) if message.get("version") else None
                )
                # async_set_updated_data() resets a polling coordinator's refresh
                # interval. The bridge sends regular keepalives, so only publish a
                # hello update when it actually changes entity-visible state.
                if (
                    self.data.bridge_configured is not True
                    or self.data.bridge_connected != connected
                    or self.data.bridge_version != version
                    or self.data.bridge_error is not None
                ):
                    self.async_set_updated_data(
                        CoordinatorData(
                            updates=self.data.updates,
                            containers=self.data.containers,
                            bridge_configured=True,
                            bridge_connected=connected,
                            bridge_version=version,
                            bridge_error=None,
                            last_event=self.data.last_event,
                        )
                    )
            return
        if message_type == "status":
            await self._async_set_bridge_status(
                bool(message.get("docker_connected", False)),
                str(message["last_docker_error"])
                if message.get("last_docker_error")
                else None,
            )
            return
        if message_type == "snapshot":
            try:
                snapshot = BridgeSnapshot.from_dict(message)
            except (TypeError, ValueError) as err:
                raise BridgeProtocolError(str(err)) from err
            previous = self.data
            self._destroyed_containers.difference_update(snapshot.containers)
            data = await self._async_build_data(
                containers=snapshot.containers,
                bridge_connected=snapshot.docker_connected,
                bridge_version=snapshot.version,
                bridge_error=snapshot.last_docker_error,
                last_event=previous.last_event if previous else None,
            )
            self.async_set_updated_data(data)
            return
        if message_type != "event" or not isinstance(message.get("event"), dict):
            return

        event = BridgeEvent.from_dict(message["event"])
        if event.action not in DOCKER_EVENT_TYPES:
            _LOGGER.debug(
                "Ignoring unsupported Docker event %s (raw action: %s)",
                event.action,
                event.raw_action,
            )
            return
        previous = self.data
        containers = dict(previous.containers) if previous else {}
        old_name = event.attributes.get("oldName", "").lstrip("/")
        if old_name and old_name != event.container_name:
            containers.pop(old_name, None)
            self._destroyed_containers.discard(old_name)
        if event.action == "destroy":
            containers.pop(event.container_name, None)
            self._destroyed_containers.add(event.container_name)
        elif event.container is not None:
            containers[event.container.name] = event.container
            self._destroyed_containers.discard(event.container.name)

        data = await self._async_build_data(
            containers=containers,
            bridge_connected=True,
            bridge_version=previous.bridge_version if previous else None,
            bridge_error=None,
            last_event=event,
        )
        event_data = {"action": event.action, **event.event_data()}
        self.hass.bus.async_fire(EVENT_DOCKER_EVENT, event_data)
        self.async_set_updated_data(data)
        if event.action in PORTAINER_REFRESH_ACTIONS:
            self._schedule_portainer_refresh()

    async def _async_set_bridge_disconnected(self, error: str) -> None:
        await self._async_set_bridge_status(False, error)

    async def _async_set_bridge_status(
        self, connected: bool, error: str | None
    ) -> None:
        if self.data is None:
            return
        # The bridge sends a status keepalive every 30 seconds. Calling
        # async_set_updated_data() for an unchanged keepalive would reset the
        # DataUpdateCoordinator polling timer every 30 seconds and prevent the
        # configured policy scan from ever running.
        if (
            self.data.bridge_connected == connected
            and self.data.bridge_error == error
        ):
            return
        self.async_set_updated_data(
            CoordinatorData(
                updates=self.data.updates,
                containers=self.data.containers,
                bridge_configured=self.data.bridge_configured,
                bridge_connected=connected,
                bridge_version=self.data.bridge_version,
                bridge_error=error,
                last_event=self.data.last_event,
            )
        )

    def _schedule_portainer_refresh(self) -> None:
        """Debounce a public refresh of the native Portainer integration."""
        if (
            self._portainer_refresh_task is not None
            and not self._portainer_refresh_task.done()
        ):
            return
        self._portainer_refresh_task = self.config_entry.async_create_task(
            self.hass,
            self._async_refresh_portainer(),
            f"{DOMAIN}_refresh_portainer",
        )

    async def _async_refresh_portainer(self) -> None:
        """Refresh one Portainer entity, which refreshes its shared coordinator."""
        try:
            await asyncio.sleep(PORTAINER_REFRESH_DEBOUNCE)
            entity_registry = er.async_get(self.hass)
            representative = next(
                (
                    entry.entity_id
                    for entry in er.async_entries_for_config_entry(
                        entity_registry, self.portainer_entry.entry_id
                    )
                    if entry.disabled_by is None
                ),
                None,
            )
            if representative is None:
                return
            await self.hass.services.async_call(
                "homeassistant",
                "update_entity",
                {ATTR_ENTITY_ID: representative},
                blocking=True,
            )
            await asyncio.sleep(PORTAINER_REFRESH_SETTLE)
            await self.async_request_refresh()
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.warning("Unable to refresh native Portainer entities: %s", err)
        finally:
            self._portainer_refresh_task = None

    def _update_first_seen(
        self,
        key: str,
        latest_version: str | None,
        update_available: bool,
        now_utc: datetime,
    ) -> datetime | None:
        """Track when the currently offered image digest was first seen."""
        if not update_available or latest_version is None:
            if key in self._seen:
                self._seen.pop(key, None)
                self._storage_dirty = True
            return None

        record = self._seen.get(key)
        if record is None or record.get("version") != latest_version:
            record = {
                "version": latest_version,
                "first_seen": now_utc.isoformat(),
            }
            self._seen[key] = record
            self._storage_dirty = True

        return self._parse_datetime(record.get("first_seen"))

    @staticmethod
    def _evaluate_policy(
        *,
        automatic_updates: bool,
        available: bool,
        update_available: bool,
        in_progress: bool,
        first_seen: datetime | None,
        latest_version: str | None,
        last_attempt_record: dict[str, str] | None,
        now_utc: datetime,
        delay: timedelta,
        in_window: bool,
    ) -> tuple[bool, str]:
        """Evaluate automatic-update eligibility."""
        if not automatic_updates:
            return False, REASON_DISABLED
        if not available:
            return False, REASON_SOURCE_UNAVAILABLE
        if not update_available:
            return False, REASON_NO_UPDATE
        if in_progress:
            return False, REASON_IN_PROGRESS
        if first_seen is None or now_utc - first_seen < delay:
            return False, REASON_DELAY
        if (
            last_attempt_record is not None
            and latest_version is not None
            and last_attempt_record.get("version") == latest_version
            and (
                last_attempt := PortainerUpdateCoordinator._parse_datetime(
                    last_attempt_record.get("time")
                )
            )
            is not None
            and now_utc - last_attempt < RETRY_COOLDOWN
        ):
            return False, REASON_RETRY_COOLDOWN
        if not in_window:
            return False, REASON_OUTSIDE_WINDOW
        return True, REASON_READY

    def _schedule_next_automatic_install(
        self, updates: dict[str, ManagedContainerUpdate]
    ) -> None:
        """Schedule one eligible update; subsequent refreshes handle the rest."""
        if self._install_task is not None and not self._install_task.done():
            return

        candidate = next((item for item in updates.values() if item.eligible), None)
        if candidate is None:
            return

        _LOGGER.info(
            "Automatic update eligible for %s; scheduling installation",
            candidate.entity_id,
        )
        self._install_task = self.config_entry.async_create_background_task(
            self.hass,
            self._async_install(candidate),
            f"{DOMAIN}_install_{candidate.entity_id}",
        )

    async def _async_install(self, item: ManagedContainerUpdate) -> None:
        """Install an update through the native Portainer update entity."""
        attempt_time = dt_util.utcnow()
        self._last_attempt[item.key] = {
            "time": attempt_time.isoformat(),
            "version": item.latest_version or "unknown",
        }
        self._last_result[item.key] = "in_progress"
        await self._async_save()

        _LOGGER.info(
            "Starting automatic update for %s (%s)",
            item.entity_id,
            item.latest_version or "unknown version",
        )
        logbook.async_log_entry(
            self.hass,
            "Portainer Update Manager",
            f"Starting automatic update for {item.name}",
            domain=DOMAIN,
            entity_id=item.entity_id,
        )

        try:
            await self.hass.services.async_call(
                UPDATE_DOMAIN,
                SERVICE_INSTALL,
                {ATTR_ENTITY_ID: item.entity_id},
                blocking=True,
            )
        except (HomeAssistantError, TimeoutError) as err:
            _LOGGER.error("Automatic update failed for %s: %s", item.entity_id, err)
            self._last_result[item.key] = f"{RESULT_FAILED}: {err}"
            logbook.async_log_entry(
                self.hass,
                "Portainer Update Manager",
                f"Automatic update failed for {item.name}: {err}",
                domain=DOMAIN,
                entity_id=item.entity_id,
            )
        except Exception as err:  # noqa: BLE001
            _LOGGER.exception(
                "Unexpected automatic-update failure for %s", item.entity_id
            )
            self._last_result[item.key] = f"{RESULT_FAILED}: {err}"
            logbook.async_log_entry(
                self.hass,
                "Portainer Update Manager",
                f"Automatic update failed for {item.name}: {err}",
                domain=DOMAIN,
                entity_id=item.entity_id,
            )
        else:
            self._last_result[item.key] = RESULT_SUCCESS
            _LOGGER.info("Automatic update completed for %s", item.entity_id)
            logbook.async_log_entry(
                self.hass,
                "Portainer Update Manager",
                f"Automatic update completed for {item.name}",
                domain=DOMAIN,
                entity_id=item.entity_id,
            )
        finally:
            await self._async_save()
            self._install_task = None
            await self.async_request_refresh()

    async def async_set_automatic_updates(self, key: str, enabled: bool) -> None:
        """Enable or disable automatic updates for one container."""
        self._automatic_updates[key] = enabled
        await self._async_save()
        await self.async_request_refresh()

    def automatic_updates_enabled(self, key: str) -> bool:
        """Return stored automatic-update state."""
        return self._automatic_updates.get(key, False)

    async def _async_save(self) -> None:
        """Persist policy and result state."""
        await self._store.async_save(
            {
                "automatic_updates": self._automatic_updates,
                "seen": self._seen,
                "last_attempt": self._last_attempt,
                "last_result": self._last_result,
            }
        )
        self._storage_dirty = False

    @staticmethod
    def _parse_datetime(value: str | None) -> datetime | None:
        """Parse an ISO datetime from storage."""
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
