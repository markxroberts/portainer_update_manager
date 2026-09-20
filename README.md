# Portainer Update Manager 0.5.3

A standalone Home Assistant custom integration that connects to the separate [Docker Event Bridge](https://github.com/markxroberts/docker-event-bridge). Portainer remains responsible for container monitoring, control, update detection, and pull/recreate operations. This project adds immediate Docker lifecycle events and configurable automatic-update policy.

```text
Docker Engine
    └── Docker Event Bridge 0.4.x
            ├── filtered Docker lifecycle/health subscription
            ├── authenticated snapshot API
            └── authenticated WebSocket event stream
                        │
                        ▼
Home Assistant Portainer Update Manager
    ├── instant Home Assistant bus events
    ├── debounced native Portainer refresh
    ├── persistent automatic-update switches
    ├── stale policy/entity cleanup
    └── maintenance-window/update-delay policy
                        │
                        ▼
Native Home Assistant Portainer integration
    ├── monitoring and container controls
    ├── registry digest checks
    └── pull and recreate
```

There is no MQTT, Docker2MQTT, DIUN, Watchtower, REST command, or Docker control endpoint in the bridge.


## Changes in 0.5.3

- Fix automatic-update policy polling being starved by the bridge's 30-second status keepalive. Unchanged bridge status/hello messages no longer reset the DataUpdateCoordinator poll interval.
- Run automatic installations as config-entry background tasks so an eligible update at Home Assistant startup cannot hold up bootstrap.
- Add Logbook entries when an automatic update starts, succeeds, or fails.
- Explicitly declare the `logbook` dependency in `manifest.json`.
- Docker Event Bridge 0.4.x remains compatible and does not need to be upgraded.

## Changes in 0.5.2

- Removed the per-container Home Assistant `EventEntity` entities completely. They are unnecessary now that the reliable `portainer_update_manager_docker_event` bus event provides instant event triggers without Logbook noise.
- Migration removes old disabled lifecycle event entities from the entity registry.
- Added cleanup for automatic-update switches belonging to permanently removed containers instead of leaving them unavailable indefinitely.
- A 10-minute stale-container grace period protects normal Portainer pull/recreate operations, which briefly generate Docker destroy/create events. If the container returns during the grace period, its automatic-update policy is retained.
- If a container remains absent after the grace period, its Portainer Update Manager automatic-update entity and stored policy/result state are removed.
- The bridge is only considered authoritative for stale cleanup when the selected native Portainer config entry resolves to a single endpoint. Multi-endpoint Portainer installations are therefore not destructively cleaned from a single bridge.
- Replaced the invalid `mdi:update-auto` icon with the supported `mdi:autorenew` icon for the automatic-update summary sensor and per-container automatic-update switches.
- Docker Event Bridge 0.4.x remains compatible and does not need to be upgraded for v0.5.2.

## Requirements

- Home Assistant 2026.8.0 or newer.
- The native Home Assistant Portainer integration configured and working.
- Docker Compose on the Docker host for the supplied bridge deployment.
- Network access from Home Assistant to the bridge.

## Installation

Copy `custom_components/portainer_update_manager` from this repository into your Home Assistant `/config/custom_components/` directory, restart Home Assistant, then add **Portainer Update Manager** under **Settings → Devices & services → Add integration**. Select the native Portainer entry and configure the bridge URL and raw bearer token. Configure the maintenance window and enable automatic updates for the containers you choose.

For bridge installation and upgrades, use the separate [Docker Event Bridge repository](https://github.com/markxroberts/docker-event-bridge).

## Upgrade the Home Assistant component

Replace:

```text
/config/custom_components/portainer_update_manager
```

with the directory from the v0.5.3 component package, then restart Home Assistant.

Existing bridge configuration and active automatic-update selections are retained. Old per-container event entities are removed during config-entry migration.

No Docker Event Bridge change is required if bridge v0.4.0 is already working correctly.

## Container removal behaviour

The bridge reports Docker `destroy` immediately. Portainer recreation also uses destroy/create, so deleting entities immediately on every destroy would lose policy during ordinary updates.

Portainer Update Manager therefore behaves as follows:

1. A missing container is made ineligible for automatic updates immediately.
2. Its automatic-update switch remains operable during temporary Portainer/container unavailability.
3. If the container returns within 10 minutes, the same switch and automatic-update setting continue normally.
4. If it remains absent for 10 minutes, the custom integration removes its automatic-update switch from the entity registry and forgets its stored policy/result state.

The native Portainer integration owns its own container devices and entities. Those may still appear unavailable after a permanent removal; this companion integration intentionally does not delete registry entries owned by another integration.

## Instant Docker events

Per-container event entities are no longer created. Use the Home Assistant event bus instead:

```text
portainer_update_manager_docker_event
```

Example:

```yaml
triggers:
  - trigger: event
    event_type: portainer_update_manager_docker_event
    event_data:
      action: oom

actions:
  - action: notify.mobile_app_mark
    data:
      title: Docker container problem
      message: >-
        {{ trigger.event.data.container_name }} generated an OOM event.
```

Supported actions are:

```text
create
destroy
die
health_healthy
health_starting
health_unhealthy
oom
pause
start
stop
unpause
update
```

## Docker Event Bridge

Bridge 0.4.x remains current for this release. Its default subscription is:

```env
DOCKER_EVENTS=create,destroy,die,health_status,oom,pause,start,stop,unpause,update
STATUS_INTERVAL_SECONDS=30
```

Opening the base URL shows the live, non-sensitive status page:

```text
http://DOCKER_HOST:9177/
```

The snapshot and WebSocket API routes remain bearer-protected.

## Security

The bridge exposes read-only application endpoints, but any process able to access the Docker socket is inherently highly privileged. The supplied container runs non-root, drops all capabilities, uses a read-only root filesystem, and should remain on a trusted network. Use HTTPS or a VPN across untrusted networks.

## Development and validation

```bash
python -m pip install -r requirements-dev.txt
python -m compileall custom_components
pytest -q
```

The integration uses modern config entries and `config_flow.py`, `DataUpdateCoordinator`, async/await, and `aiohttp`. `manifest.json` explicitly includes `version`, `dependencies`, and `requirements`.


## v0.5.2 bug fixes

- Removes all legacy per-container EventEntity registry entries owned by the integration, including disabled/renamed remnants from v0.2-v0.4.
- Keeps the Docker event stream connected binary sensor available whenever a bridge is configured; disconnection is represented by `off`, not `unavailable`.
- Discovers automatic-update controls from the native Portainer update entity registry even when an entity has no current state yet.
- Matches containers using Portainer's stable update-entity unique ID rather than friendly names.
- Keeps automatic-update switches operable through temporary Portainer/container unavailability and removes them only after confirmed stale-container cleanup.
- Removes obsolete in-memory per-container EventEntity dispatch queues; real-time `portainer_update_manager_docker_event` bus events are unchanged.
