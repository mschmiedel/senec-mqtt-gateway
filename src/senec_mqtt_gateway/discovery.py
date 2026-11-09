"""Home Assistant MQTT discovery messages."""

from __future__ import annotations

import json

from .config import Config
from .schema import Metric, Raw, ReadPlan
from .senec import DeviceInfo


def node_id(topic_prefix: str) -> str:
    return "senec_mqtt_gateway_" + topic_prefix.replace("/", "_")


def _component(metric: Metric) -> str:
    return "binary_sensor" if metric.binary else "sensor"


def _object_id(metric: Metric) -> str:
    return metric.topic.replace("/", "_")


def discovery_topic(config: Config, metric: Metric) -> str:
    node = node_id(config.topic_prefix)
    return f"{config.ha_discovery_prefix}/{_component(metric)}/{node}/{_object_id(metric)}/config"


def discovery_messages(config: Config, info: DeviceInfo, raw: Raw, plan: ReadPlan) -> list[tuple[str, str]]:
    """Return ``(topic, payload)`` for every enabled value this device delivers in ``raw``."""
    node = node_id(config.topic_prefix)
    status_topic = f"{config.topic_prefix}/status"
    device = {
        "identifiers": [node],
        "name": "SENEC.Home",
        "manufacturer": "SENEC",
        **({"model": info.model} if info.model else {}),
        **({"sw_version": info.version} if info.version else {}),
    }

    messages = []
    for metric in plan.active:
        if metric.render(raw) is None:
            continue
        object_id = _object_id(metric)
        entity_id = metric.legacy_id or f"senec_{object_id}"
        payload = {
            "name": metric.name,
            "unique_id": f"{node}_{object_id}",
            "default_entity_id": f"{_component(metric)}.{entity_id}",
            "state_topic": f"{config.topic_prefix}/{metric.topic}",
            "availability_topic": status_topic,
            "device": device,
        }
        if metric.advanced:
            payload["enabled_by_default"] = False
        if metric.binary:
            payload.update(payload_on="1", payload_off="0")
        else:
            payload["suggested_display_precision"] = metric.precision
        for key, value in (
            ("unit_of_measurement", metric.unit),
            ("device_class", metric.device_class),
            ("state_class", metric.state_class),
        ):
            if value is not None:
                payload[key] = value
        messages.append((discovery_topic(config, metric), json.dumps(payload)))
    return messages
