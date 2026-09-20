"""Constants for the Portainer Update Manager integration."""

from datetime import timedelta

from homeassistant.const import Platform

DOMAIN = "portainer_update_manager"
PORTAINER_DOMAIN = "portainer"

ALL_PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
]

CONF_PORTAINER_ENTRY_ID = "portainer_entry_id"
CONF_BRIDGE_URL = "bridge_url"
CONF_BRIDGE_TOKEN = "bridge_token"
CONF_VERIFY_SSL = "verify_ssl"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_UPDATE_DELAY_HOURS = "update_delay_hours"
CONF_WINDOW_START = "window_start"
CONF_WINDOW_END = "window_end"

# Removed in config-entry version 4. Retained only so migration can clean old options.
LEGACY_CONF_EVENT_ENTITIES = "event_entities"

DEFAULT_BRIDGE_URL = "http://localhost:9177"
DEFAULT_VERIFY_SSL = True
DEFAULT_SCAN_INTERVAL = 5
DEFAULT_UPDATE_DELAY_HOURS = 0
DEFAULT_WINDOW_START = "00:00:00"
DEFAULT_WINDOW_END = "00:00:00"
MIN_SCAN_INTERVAL = 1
MAX_SCAN_INTERVAL = 60
MAX_UPDATE_DELAY_HOURS = 720

STORAGE_VERSION = 2
STORAGE_KEY = f"{DOMAIN}.{{entry_id}}"

EVENT_DOCKER_EVENT = f"{DOMAIN}_docker_event"

ATTR_SOURCE_ENTITY = "source_update_entity"
ATTR_INSTALLED_VERSION = "installed_version"
ATTR_LATEST_VERSION = "latest_version"
ATTR_UPDATE_AVAILABLE = "update_available"
ATTR_ELIGIBLE = "eligible"
ATTR_POLICY_REASON = "policy_reason"
ATTR_FIRST_SEEN = "first_seen"
ATTR_LAST_ATTEMPT = "last_attempt"
ATTR_LAST_RESULT = "last_result"

REASON_DISABLED = "automatic_updates_disabled"
REASON_SOURCE_UNAVAILABLE = "source_entity_unavailable"
REASON_NO_UPDATE = "no_update_available"
REASON_IN_PROGRESS = "update_in_progress"
REASON_DELAY = "update_delay"
REASON_OUTSIDE_WINDOW = "outside_maintenance_window"
REASON_RETRY_COOLDOWN = "retry_cooldown"
REASON_READY = "ready"

RESULT_SUCCESS = "success"
RESULT_FAILED = "failed"

RETRY_COOLDOWN = timedelta(hours=6)
PORTAINER_REFRESH_DEBOUNCE = 2
PORTAINER_REFRESH_SETTLE = 1

# A container can briefly disappear while Portainer recreates it. Keep the policy
# entity through that transient gap, but purge it if the bridge continues to report
# the container absent for this long.
STALE_CONTAINER_GRACE = timedelta(minutes=10)

# Defence-in-depth allow-list. The bridge also filters at Docker's /events
# endpoint, but Home Assistant will never forward exec_* or unknown events even
# when connected to an older or misconfigured bridge.
DOCKER_EVENT_TYPES: list[str] = [
    "create",
    "destroy",
    "die",
    "health_healthy",
    "health_starting",
    "health_unhealthy",
    "oom",
    "pause",
    "start",
    "stop",
    "unpause",
    "update",
]

PORTAINER_REFRESH_ACTIONS = set(DOCKER_EVENT_TYPES)
