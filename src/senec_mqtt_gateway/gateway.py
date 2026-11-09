"""Connects the SENEC client to the MQTT broker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import ssl
import time

import aiomqtt

from .config import Config
from .discovery import battery_mode_topic, discovery_messages, discovery_topic, node_id
from .schema import merge_sections, render_all
from .senec import BATTERY_MODES, SenecClient, SenecError

_LOGGER = logging.getLogger(__name__)

_ONLINE = "online"
_OFFLINE = "offline"
_SESSION_REFRESH_SECONDS = 3600.0
_RECONNECT_DELAY = 5.0


class Gateway:
    def __init__(self, config: Config, senec: SenecClient) -> None:
        self._config = config
        self._senec = senec
        self._plan = senec.plan
        self._status_topic = f"{config.topic_prefix}/status"
        self._command_topic = f"{config.topic_prefix}/command/battery_mode"
        self._available = False
        self._discovery_sent = False
        self._polls_until_details = 0

    def _client(self) -> aiomqtt.Client:
        config = self._config
        return aiomqtt.Client(
            hostname=config.mqtt_host,
            port=config.mqtt_port,
            username=config.mqtt_user,
            password=config.mqtt_password,
            identifier=node_id(config.topic_prefix),
            tls_context=ssl.create_default_context() if config.mqtt_tls else None,
            will=aiomqtt.Will(self._status_topic, payload=_OFFLINE, qos=1, retain=True),
        )

    async def run(self) -> None:
        """Run until cancelled, reconnecting to the broker when needed."""
        self._log_adjusted_detail_interval()
        while True:
            try:
                async with self._client() as client:
                    _LOGGER.info("Connected to MQTT broker")
                    await self._serve(client)
            except aiomqtt.MqttError as exc:
                _LOGGER.warning("MQTT connection lost: %s", exc)
                await asyncio.sleep(_RECONNECT_DELAY)

    def _log_adjusted_detail_interval(self) -> None:
        config = self._config
        effective = config.detail_every * config.poll_interval
        if config.detail_every and not math.isclose(effective, config.detail_interval, abs_tol=1e-6):
            _LOGGER.info(
                "DETAIL_INTERVAL %g is not a multiple of POLL_INTERVAL %g, using %g",
                config.detail_interval,
                config.poll_interval,
                effective,
            )

    async def _serve(self, client: aiomqtt.Client) -> None:
        self._available = False
        self._discovery_sent = False
        self._polls_until_details = 0
        await self._clear_disabled(client)
        await self._senec.start_session()

        tasks = [asyncio.create_task(self._poll_loop(client))]
        if self._config.commands_enabled:
            await client.subscribe(self._command_topic, qos=1)
            tasks.append(asyncio.create_task(self._command_loop(client)))
            _LOGGER.info("Battery commands enabled on %s", self._command_topic)

        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            # A clean disconnect does not trigger the last will. A failure here
            # must not replace the cancellation, or run() would keep going.
            with contextlib.suppress(aiomqtt.MqttError, TimeoutError):
                await asyncio.wait_for(self._publish_status(client, _OFFLINE), timeout=2)
            raise
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _clear_disabled(self, client: aiomqtt.Client) -> None:
        """Remove retained messages, or disabled values would stay visible forever."""
        for metric in self._plan.off:
            await client.publish(f"{self._config.topic_prefix}/{metric.topic}", payload=None, qos=1, retain=True)
            if self._config.ha_discovery_enabled:
                await client.publish(discovery_topic(self._config, metric), payload=None, qos=1, retain=True)
        if self._config.ha_discovery_enabled and not self._config.commands_enabled:
            await client.publish(battery_mode_topic(self._config), payload=None, qos=1, retain=True)

    async def _publish_status(self, client: aiomqtt.Client, status: str) -> None:
        await client.publish(self._status_topic, payload=status, qos=1, retain=True)
        self._available = status == _ONLINE

    async def _poll_loop(self, client: aiomqtt.Client) -> None:
        loop = asyncio.get_running_loop()
        interval = self._config.poll_interval
        next_poll = loop.time()
        last_session = time.monotonic()

        while True:
            if time.monotonic() - last_session >= _SESSION_REFRESH_SECONDS:
                await self._senec.start_session()
                last_session = time.monotonic()

            await self._poll_once(client)

            # Always publish, even unchanged values: evcc treats silence as a timeout.
            next_poll = max(next_poll + interval, loop.time())
            await asyncio.sleep(next_poll - loop.time())

    async def _poll_once(self, client: aiomqtt.Client) -> None:
        try:
            raw = await self._senec.read_values()
        except SenecError as exc:
            if self._available:
                _LOGGER.warning("SENEC unavailable: %s", exc)
                await self._publish_status(client, _OFFLINE)
            else:
                _LOGGER.debug("SENEC still unavailable: %s", exc)
            await self._senec.start_session()
            return

        details, details_settled = await self._read_details_if_due()
        merged = merge_sections(raw, details)
        if self._config.ha_discovery_enabled and details_settled and not self._discovery_sent:
            await self._publish_discovery(client, merged)

        await self._publish_values(client, merged)
        if not self._available:
            _LOGGER.info("SENEC available")
            await self._publish_status(client, _ONLINE)

    async def _publish_values(self, client: aiomqtt.Client, raw: dict) -> None:
        for suffix, payload in render_all(raw, self._plan).items():
            await client.publish(f"{self._config.topic_prefix}/{suffix}", payload=payload, retain=True)

    async def _read_details_if_due(self) -> tuple[dict, bool]:
        """Return the details and whether discovery can rely on what has been read.

        A failure here leaves the SENEC online: the values evcc needs still arrive.
        """
        every = self._config.detail_every
        if every == 0:
            return {}, True
        self._polls_until_details -= 1
        awaiting_discovery = self._config.ha_discovery_enabled and not self._discovery_sent
        if not awaiting_discovery and self._polls_until_details > 0:
            return {}, True
        self._polls_until_details = every
        try:
            return await self._senec.read_details(), True
        except SenecError as exc:
            _LOGGER.warning("Reading details failed: %s", exc)
            return {}, False

    async def _publish_discovery(self, client: aiomqtt.Client, raw: dict) -> None:
        info = await self._senec.read_device_info()
        for topic, payload in discovery_messages(self._config, info, raw, self._plan):
            await client.publish(topic, payload=payload, qos=1, retain=True)
        self._discovery_sent = True

    async def _command_loop(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            if message.retain:
                _LOGGER.warning("Ignoring retained command on %s", message.topic)
                continue
            mode = bytes(message.payload).decode(errors="replace").strip().lower()
            if mode not in BATTERY_MODES:
                _LOGGER.warning("Ignoring unknown battery mode %r", mode)
                continue
            try:
                state = await self._senec.set_battery_mode(mode)
            except SenecError as exc:
                _LOGGER.error("Setting battery mode %s failed: %s", mode, exc)
                continue
            _LOGGER.info("Battery mode set to %s", mode)
            # Report the result now, or a selection in Home Assistant would jump
            # back until the next poll.
            await self._publish_values(client, state)
