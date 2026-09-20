"""aiohttp client for the Docker Event Bridge."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
import json
from typing import Any

import aiohttp

from .models import BridgeSnapshot


class BridgeError(RuntimeError):
    """Base bridge error."""


class BridgeAuthenticationError(BridgeError):
    """Bridge authentication failed."""


class BridgeConnectionError(BridgeError):
    """Bridge is unreachable."""


class BridgeProtocolError(BridgeError):
    """Bridge returned an invalid response."""


class DockerEventBridgeClient:
    """Asynchronous client for snapshot and WebSocket endpoints."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        base_url: str,
        token: str,
    ) -> None:
        """Initialize the client."""
        self._session = session
        self._base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}

    async def async_get_snapshot(self) -> BridgeSnapshot:
        """Fetch and validate the current bridge snapshot."""
        url = f"{self._base_url}/api/v1/snapshot"
        try:
            async with asyncio.timeout(15):
                async with self._session.get(url, headers=self._headers) as response:
                    if response.status in (401, 403):
                        raise BridgeAuthenticationError("Invalid bridge token")
                    if response.status >= 400:
                        body = await response.text()
                        raise BridgeConnectionError(
                            f"Bridge returned HTTP {response.status}: {body[:200]}"
                        )
                    try:
                        payload = await response.json()
                    except (aiohttp.ContentTypeError, json.JSONDecodeError) as err:
                        raise BridgeProtocolError(
                            "Bridge returned invalid JSON"
                        ) from err
        except asyncio.CancelledError:
            raise
        except BridgeError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise BridgeConnectionError(str(err)) from err
        if not isinstance(payload, dict) or payload.get("type") != "snapshot":
            raise BridgeProtocolError("Bridge did not return a snapshot payload")
        try:
            return BridgeSnapshot.from_dict(payload)
        except (TypeError, ValueError) as err:
            raise BridgeProtocolError(str(err)) from err

    async def async_messages(self) -> AsyncIterator[dict[str, Any]]:
        """Yield validated JSON messages from the bridge WebSocket."""
        url = f"{self._base_url}/api/v1/events"
        try:
            async with self._session.ws_connect(
                url,
                headers=self._headers,
                heartbeat=30,
                receive_timeout=None,
                autoping=True,
            ) as websocket:
                async for message in websocket:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        try:
                            payload = json.loads(message.data)
                        except json.JSONDecodeError as err:
                            raise BridgeProtocolError(
                                "Bridge WebSocket returned invalid JSON"
                            ) from err
                        if isinstance(payload, dict):
                            yield payload
                    elif message.type is aiohttp.WSMsgType.ERROR:
                        raise BridgeConnectionError(str(websocket.exception()))
                    elif message.type in (
                        aiohttp.WSMsgType.CLOSE,
                        aiohttp.WSMsgType.CLOSED,
                    ):
                        raise BridgeConnectionError("Bridge WebSocket closed")
                raise BridgeConnectionError("Bridge WebSocket closed")
        except asyncio.CancelledError:
            raise
        except BridgeError:
            raise
        except aiohttp.WSServerHandshakeError as err:
            if err.status in (401, 403):
                raise BridgeAuthenticationError("Invalid bridge token") from err
            raise BridgeConnectionError(str(err)) from err
        except (aiohttp.ClientError, TimeoutError, OSError) as err:
            raise BridgeConnectionError(str(err)) from err
