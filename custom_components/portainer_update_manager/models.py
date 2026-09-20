"""Data models for Portainer Update Manager."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass(slots=True, frozen=True)
class BridgeContainer:
    """Container state supplied by the event bridge."""

    id: str
    name: str
    image: str
    image_id: str | None
    state: str
    status: str
    created: int | None
    health: str | None
    labels: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BridgeContainer:
        """Create a container from a bridge payload."""
        labels = value.get("labels") if isinstance(value.get("labels"), dict) else {}
        return cls(
            id=str(value.get("id") or ""),
            name=str(value.get("name") or ""),
            image=str(value.get("image") or ""),
            image_id=str(value["image_id"]) if value.get("image_id") else None,
            state=str(value.get("state") or "unknown"),
            status=str(value.get("status") or "unknown"),
            created=int(value["created"]) if value.get("created") is not None else None,
            health=str(value["health"]) if value.get("health") else None,
            labels={str(key): str(item) for key, item in labels.items()},
        )


@dataclass(slots=True, frozen=True)
class BridgeEvent:
    """A normalized Docker event supplied by the bridge."""

    action: str
    raw_action: str
    container_id: str
    container_name: str
    image: str | None
    timestamp: datetime
    time_nano: int | None
    attributes: dict[str, str] = field(default_factory=dict)
    container: BridgeContainer | None = None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BridgeEvent:
        """Create an event from a bridge payload."""
        attributes = (
            value.get("attributes")
            if isinstance(value.get("attributes"), dict)
            else {}
        )
        container_data = value.get("container")
        timestamp = _parse_datetime(str(value.get("timestamp") or ""))
        return cls(
            action=str(value.get("action") or "unknown"),
            raw_action=str(value.get("raw_action") or value.get("action") or "unknown"),
            container_id=str(value.get("container_id") or ""),
            container_name=str(value.get("container_name") or ""),
            image=str(value["image"]) if value.get("image") else None,
            timestamp=timestamp or datetime.now(UTC),
            time_nano=int(value["time_nano"]) if value.get("time_nano") else None,
            attributes={str(key): str(item) for key, item in attributes.items()},
            container=(
                BridgeContainer.from_dict(container_data)
                if isinstance(container_data, dict)
                else None
            ),
        )

    def event_data(self) -> dict[str, Any]:
        """Return event attributes suitable for Home Assistant."""
        data: dict[str, Any] = {
            "container_id": self.container_id,
            "container_name": self.container_name,
            "image": self.image,
            "raw_action": self.raw_action,
            "timestamp": self.timestamp.isoformat(),
            **self.attributes,
        }
        if self.container is not None:
            data.update(
                {
                    "state": self.container.state,
                    "status": self.container.status,
                    "health": self.container.health,
                    "image_id": self.container.image_id,
                }
            )
        return data


@dataclass(slots=True, frozen=True)
class BridgeSnapshot:
    """Snapshot response from the event bridge."""

    version: str | None
    docker_connected: bool
    last_event_at: datetime | None
    last_docker_error: str | None
    containers: dict[str, BridgeContainer]

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> BridgeSnapshot:
        """Create a snapshot from an API response."""
        raw_containers = value.get("containers")
        if not isinstance(raw_containers, list):
            raise ValueError("Bridge snapshot containers must be a list")
        containers: dict[str, BridgeContainer] = {}
        for raw_container in raw_containers:
            if not isinstance(raw_container, dict):
                continue
            container = BridgeContainer.from_dict(raw_container)
            if container.name:
                containers[container.name] = container
        return cls(
            version=str(value["version"]) if value.get("version") else None,
            docker_connected=bool(value.get("docker_connected", False)),
            last_event_at=_parse_datetime(
                str(value.get("last_event_at") or "")
            ),
            last_docker_error=(
                str(value["last_docker_error"])
                if value.get("last_docker_error")
                else None
            ),
            containers=containers,
        )


@dataclass(slots=True, frozen=True)
class ManagedContainerUpdate:
    """State for one native Portainer update entity."""

    key: str
    entity_id: str
    name: str
    device_id: str | None
    available: bool
    installed_version: str | None
    latest_version: str | None
    update_available: bool
    in_progress: bool
    automatic_updates: bool
    first_seen: datetime | None
    eligible: bool
    policy_reason: str
    last_attempt: datetime | None
    last_result: str | None


@dataclass(slots=True, frozen=True)
class CoordinatorData:
    """Combined bridge and native Portainer state."""

    updates: dict[str, ManagedContainerUpdate]
    containers: dict[str, BridgeContainer]
    bridge_configured: bool
    bridge_connected: bool
    bridge_version: str | None = None
    bridge_error: str | None = None
    last_event: BridgeEvent | None = None


def _parse_datetime(value: str) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed
