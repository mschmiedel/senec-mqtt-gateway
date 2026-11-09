"""Generates docs/FIELDS.md from the metric list, so the description cannot drift.

After changing a value, run: uv run python -m senec_mqtt_gateway.field_docs > docs/FIELDS.md
"""

from __future__ import annotations

import re

from .schema import METRICS, Metric

_HEADER = """\
# Fields

Every value the gateway publishes, where it comes from on the SENEC, and anything the gateway does differently from the device.

This file is generated from `src/senec_mqtt_gateway/schema.py` and `field_docs.py`. Do not edit it by hand, run:

```bash
uv run python -m senec_mqtt_gateway.field_docs > docs/FIELDS.md
```

## Conventions

- Topics are shown without the prefix (`senec` by default). The payload is just the value, and all values are retained.
- `senec/status` is `online` or `offline`. It says whether the gateway is running and can read the SENEC.
- `poll` values are read every `POLL_INTERVAL` and republished even when unchanged, because evcc treats silence as a timeout. `detail` values are read every `DETAIL_INTERVAL`. Both can be changed, see the README.
- Placeholders: `<m>` is a battery module (`a` to `d`), `<n>` a cell number, `<s>` a PV string (`1` to `3`), `<p>` a phase (`l1` to `l3`).
- `[ ]` after a source marks one element of an array on the device.

## Where the gateway differs from the device

| What | Device | Gateway | Why |
|---|---|---|---|
| Battery power and current | Positive while charging | Positive while discharging | Matches evcc, which also treats grid import as positive |
| Phase power factor | Not reported | Calculated by the gateway as P / (U · I) | The device reports phase currents as positive RMS values, so they show no direction. See the note in the table |
| Cell voltages | Millivolts | Volts | Consistent with the other voltages |
| Some temperatures and SOH | Tenths of a degree or percent | Degrees and percent | Consistent with the other values |
| Module discharge current limit | Negative | Positive | Consistent with the system-wide limit, which the device reports as positive |
| Rounding | Full float precision | Rounded per value | Avoids meaningless digits |
| Negative zero | Reported as `-0.0` | `0.0` | |
| Hardware not installed | Zeros in arrays | Value is not published | Avoids empty modules, strings and meters |

## Values

| Topic | Unit | Rate | Source | Meaning and handling |
|---|---|---|---|---|
"""

_FOOTER = """
## Not published

- Identity data: serial numbers, device ids and other text values.
- Configuration and commissioning: `GRIDCONFIG`, `BMS_PARA`, wizard and self-test fields, and the charge test fields in `ENERGY`.
- Readable, but not published yet: `WALLBOX`, `AMPACE`, `SOCKETS`, `PWR_UNIT`, `STECA`. See [possible extensions](CONCEPT.md#possible-extensions).
- `STATISTIC` is empty on current firmware.
"""

_RMS_CURRENT = "Always positive, as reported by the device: an RMS value has no direction. The direction is in the power of the same phase."
_POWER_FACTOR = (
    "Not reported by the device, calculated by the gateway so that direction and load type are visible without "
    "combining three topics. Negative while exporting. Includes harmonics, so it is not exactly cos φ. "
    "Limited to ±1 and not published while voltage or current is zero. Unreliable at very small currents."
)

_NOTES = {
    "grid/power": "Positive when importing, negative when exporting.",
    "grid/power/l<p>": "Same sign as `grid/power`. The three phases add up to it.",
    "grid/current/l<p>": _RMS_CURRENT,
    "meter2/current/l<p>": _RMS_CURRENT,
    "grid/power_factor/l<p>": _POWER_FACTOR,
    "meter2/power_factor/l<p>": _POWER_FACTOR,
    "pv/power": "The sign while generating has not been confirmed on a real device yet.",
    "battery/power": "Positive when discharging, negative when charging.",
    "battery/current": "Positive when discharging, negative when charging.",
    "battery/safe_charge": "1 while the device is force-charging the battery, which is the mode `charge`.",
    "system/state": "Numeric state code of the device, not translated.",
    "pv/power_ratio": "Power limit of the inverter, 100 while unlimited.",
    "battery/modules": "Number of configured modules. Decides which module values are published.",
    "battery/bms_status": "Raw device code.",
    "battery/bms_error": "Raw device code.",
    "battery/dc/voltage": "Measured separately from `battery/voltage`, so the two differ slightly.",
    "battery/dc/current": "Measured separately from `battery/current`, so the two differ slightly.",
    "bat1/temperature/1": "What this sensor measures is not documented.",
    "bat1/temperature/2": "What this sensor measures is not documented.",
    "battery/module/<m>/status": "Raw device code.",
    "battery/module/<m>/balancing": "Raw device code.",
    "battery/module/<m>/charged_counter": "Raw counter. Its unit is not documented, so none is claimed.",
    "battery/module/<m>/discharged_counter": "Raw counter. Its unit is not documented, so none is claimed.",
    "battery/module/<m>/cell/<n>/temperature": "Up to 6 sensors per module, as far as the device reports them.",
    "battery/module/<m>/cell/<n>/voltage": "Up to 16 cells per module, as far as the device reports them.",
}


def _normalize_topic(topic: str) -> str:
    topic = re.sub(r"/module/[a-d]/", "/module/<m>/", topic)
    topic = re.sub(r"/cell/\d+/", "/cell/<n>/", topic)
    topic = re.sub(r"/string/\d/", "/string/<s>/", topic)
    return re.sub(r"/l[123]$", "/l<p>", topic)


def _normalize_name(name: str) -> str:
    name = re.sub(r"module [A-D]", "module M", name)
    name = re.sub(r"cell \d+", "cell N", name)
    name = re.sub(r"string \d", "string S", name)
    return re.sub(r" L[123]$", " per phase", name)


def _source(metric: Metric) -> str:
    field = re.sub(r"_MODULE_[A-D]$", "_MODULE_<M>", metric.field)
    field = re.sub(r"_L[123]$", "_L<p>", field)
    suffix = "[ ]" if metric.index is not None else ""
    source = f"`{metric.section}.{field}{suffix}`"
    if metric.apparent_power_of:
        first, second = metric.apparent_power_of
        return f"Calculated from {source}, `{first}{suffix}`, `{second}{suffix}`"
    return source


def _handling(group: list[Metric]) -> list[str]:
    metric = group[0]
    parts = []
    if metric.scale == -1.0:
        parts.append("Sign inverted from the device value.")
    elif metric.scale != 1.0:
        parts.append(f"Device value × {metric.scale:g}.")
    if metric.requires:
        section, field, minimum = metric.requires
        if len({member.requires for member in group}) > 1:
            parts.append(f"Only for items present according to `{section}.{field}`.")
        else:
            parts.append(f"Only if `{section}.{field}` is at least {minimum}.")
    if metric.advanced:
        parts.append("Disabled by default in Home Assistant.")
    return parts


def render() -> str:
    """Return the complete document."""
    groups: dict[str, list[Metric]] = {}
    for metric in METRICS:
        groups.setdefault(_normalize_topic(metric.topic), []).append(metric)

    unknown = set(_NOTES) - set(groups)
    if unknown:
        raise ValueError(f"Notes for unknown topics: {', '.join(sorted(unknown))}")

    rows = []
    for topic, group in groups.items():
        metric = group[0]
        unit = "1/0" if metric.binary else metric.unit or "–"
        rate = "poll" if metric.fast else "detail"
        meaning = [f"{_normalize_name(metric.name)}."]
        if topic in _NOTES:
            meaning.append(_NOTES[topic])
        meaning.extend(_handling(group))
        rows.append(f"| `{topic}` | {unit} | {rate} | {_source(metric)} | {' '.join(meaning)} |")
    return _HEADER + "\n".join(rows) + "\n" + _FOOTER


if __name__ == "__main__":
    print(render(), end="")
