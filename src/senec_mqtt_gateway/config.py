"""Configuration from environment variables."""

from __future__ import annotations

import math
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from .schema import DEFAULT_PLAN, ReadPlan, build_plan


class ConfigError(Exception):
    """A required setting is missing or invalid."""


def load_plan(path: str | None) -> ReadPlan:
    """Return the recommended read plan, adjusted by the optional metrics file."""
    if path is None:
        return DEFAULT_PLAN
    try:
        with open(path, "rb") as handle:
            data = tomllib.load(handle)
    except OSError as exc:
        raise ConfigError(f"METRICS_CONFIG cannot be read: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"METRICS_CONFIG is not valid TOML: {exc}") from exc

    if set(data) != {"rates"} or not isinstance(data["rates"], dict):
        raise ConfigError("METRICS_CONFIG must contain exactly one [rates] table")
    try:
        return build_plan(data["rates"])
    except ValueError as exc:
        raise ConfigError(f"METRICS_CONFIG: {exc}") from exc


def _flag(value: str) -> bool:
    return value.strip().lower() in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Config:
    senec_url: str
    poll_interval: float = 10.0
    detail_interval: float = 60.0
    metrics_config: str | None = None
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_tls: bool = False
    mqtt_user: str | None = None
    mqtt_password: str | None = None
    topic_prefix: str = "senec"
    ha_discovery_enabled: bool = True
    ha_discovery_prefix: str = "homeassistant"
    commands_enabled: bool = False
    log_level: str = "INFO"

    @property
    def detail_every(self) -> int:
        """Read the details on every n-th poll, rounding up; 0 means never."""
        if self.detail_interval == 0:
            return 0
        # Rounding first keeps floating point noise from adding a whole poll.
        return max(1, math.ceil(round(self.detail_interval / self.poll_interval, 6)))

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Config:
        host = env.get("SENEC_HOST", "").strip()
        if not host:
            raise ConfigError("SENEC_HOST is required")
        scheme = env.get("SENEC_SCHEME", "https").strip()

        uri = urlsplit(env.get("MQTT_URI", "mqtt://localhost:1883"))
        if uri.scheme not in ("mqtt", "mqtts") or not uri.hostname:
            raise ConfigError("MQTT_URI must look like mqtt://host:1883 or mqtts://host:8883")

        try:
            poll_interval = float(env.get("POLL_INTERVAL", "10"))
        except ValueError as exc:
            raise ConfigError("POLL_INTERVAL must be a number of seconds") from exc
        if poll_interval < 2:
            raise ConfigError("POLL_INTERVAL must be at least 2 seconds")

        try:
            detail_interval = float(env.get("DETAIL_INTERVAL", "60"))
        except ValueError as exc:
            raise ConfigError("DETAIL_INTERVAL must be a number of seconds") from exc
        if detail_interval < 0:
            raise ConfigError("DETAIL_INTERVAL must not be negative")

        tls = uri.scheme == "mqtts"
        return cls(
            senec_url=f"{scheme}://{host}",
            poll_interval=poll_interval,
            detail_interval=detail_interval,
            metrics_config=env.get("METRICS_CONFIG") or None,
            mqtt_host=uri.hostname,
            mqtt_port=uri.port or (8883 if tls else 1883),
            mqtt_tls=tls,
            mqtt_user=env.get("MQTT_USER") or None,
            mqtt_password=env.get("MQTT_PASSWORD") or None,
            topic_prefix=env.get("MQTT_TOPIC", "senec").strip("/"),
            ha_discovery_enabled=_flag(env.get("HA_DISCOVERY_ENABLED", "true")),
            ha_discovery_prefix=env.get("HA_DISCOVERY_PREFIX", "homeassistant").strip("/"),
            commands_enabled=_flag(env.get("COMMANDS_ENABLED", "false")),
            log_level=env.get("LOG_LEVEL", "INFO").upper(),
        )
