"""The values read from the SENEC and the MQTT topics they are published on.

Signs follow evcc: grid import and battery discharge are positive.

Metrics marked ``fast`` are read every poll, because evcc controls charging with
them. All others are read less often, to keep the load on the device low.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import Any

Raw = Mapping[str, Mapping[str, Any]]

RATES = ("poll", "detail", "off")

# The web UI of the SENEC splits its requests at this size.
_MAX_FORM_LENGTH = 600

# The values the evcc configuration in the README reads with a timeout.
EVCC_TOPICS = frozenset(
    {
        "grid/power",
        "grid/power/l1",
        "grid/power/l2",
        "grid/power/l3",
        "grid/current/l1",
        "grid/current/l2",
        "grid/current/l3",
        "pv/power",
        "battery/power",
        "battery/soc",
    }
)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


@dataclass(frozen=True)
class Metric:
    """One published value.

    ``legacy_id`` reuses the entity id of the ha-senec-v3 integration where the
    meaning is unchanged, so that recorded history can continue. Battery power
    and current do not have one, because their sign differs from that integration.

    ``requires`` is ``(section, field, minimum)``. The value is only published if
    that field reaches the minimum, which hides modules, PV strings and meters
    the device reports as zeros but does not have.

    ``advanced`` values are published, but disabled by default in Home Assistant.

    ``apparent_power_of`` names the two fields of the same section whose product
    is the apparent power. The value is then calculated by the gateway as
    power factor: ``field`` divided by that product. Descriptions are generated
    from this list, see field_docs.py.
    """

    topic: str
    name: str
    section: str
    field: str
    precision: int
    unit: str | None = None
    device_class: str | None = None
    state_class: str | None = "measurement"
    index: int | None = None
    scale: float = 1.0
    legacy_id: str | None = None
    binary: bool = False
    fast: bool = False
    requires: tuple[str, str, int] | None = None
    advanced: bool = False
    apparent_power_of: tuple[str, str] | None = None

    def _applies(self, raw: Raw) -> bool:
        if self.requires is None:
            return True
        section, field, minimum = self.requires
        value = _number(raw.get(section, {}).get(field))
        return value is not None and value >= minimum

    def _read(self, raw: Raw, field: str) -> float | None:
        value = raw.get(self.section, {}).get(field)
        if self.index is None:
            return _number(value)
        if not isinstance(value, list) or self.index >= len(value):
            return None
        return _number(value[self.index])

    def _value(self, raw: Raw) -> float | None:
        value = self._read(raw, self.field)
        if value is None:
            return None
        if self.apparent_power_of is not None:
            return self._power_factor(raw, value)
        return value * self.scale

    def _power_factor(self, raw: Raw, power: float) -> float | None:
        first, second = (self._read(raw, field) for field in self.apparent_power_of)
        if not first or not second:
            return None
        # Rounded device values can push the ratio slightly past 1.
        return max(-1.0, min(1.0, power / (first * second)))

    def render(self, raw: Raw) -> str | None:
        if not self._applies(raw):
            return None
        value = self._value(raw)
        if value is None:
            return None
        if self.binary:
            return "1" if value else "0"
        # Adding 0.0 turns a rounded -0.0 into 0.0.
        return f"{round(value, self.precision) + 0.0:.{self.precision}f}"


_PHASES = ("l1", "l2", "l3")
_MODULE_LETTERS = "abcd"
_MILLI = 0.001
_DECI = 0.1


def _phase_metrics(
    topic: str, name: str, section: str, field: str, unit: str, device_class: str, **kwargs: Any
) -> list[Metric]:
    legacy = kwargs.pop("legacy", None)
    return [
        Metric(
            topic=f"{topic}/{phase}",
            name=f"{name} {phase.upper()}",
            section=section,
            field=field,
            index=index,
            precision=2,
            unit=unit,
            device_class=device_class,
            legacy_id=f"senec_enfluri_net_{legacy}_p{index + 1}" if legacy else None,
            **kwargs,
        )
        for index, phase in enumerate(_PHASES)
    ]


def _power_factor_metrics(topic: str, name: str, section: str, **kwargs: Any) -> list[Metric]:
    return [
        Metric(
            topic=f"{topic}/{phase}",
            name=f"{name} {phase.upper()}",
            section=section,
            field="P_AC",
            index=index,
            precision=2,
            device_class="power_factor",
            apparent_power_of=("U_AC", "I_AC"),
            **kwargs,
        )
        for index, phase in enumerate(_PHASES)
    ]


def _meter2_metrics() -> list[Metric]:
    requires = ("PM1OBJ2", "ENABLED", 1)
    common = {"section": "PM1OBJ2", "requires": requires}
    return [
        Metric("meter2/frequency", "Meter 2 frequency", field="FREQ", precision=3, unit="Hz", device_class="frequency", **common),
        Metric("meter2/power", "Meter 2 power", field="P_TOTAL", precision=1, unit="W", device_class="power", **common),
        *_phase_metrics("meter2/power", "Meter 2 power", "PM1OBJ2", "P_AC", "W", "power", requires=requires),
        *_phase_metrics("meter2/current", "Meter 2 current", "PM1OBJ2", "I_AC", "A", "current", requires=requires),
        *_phase_metrics("meter2/voltage", "Meter 2 voltage", "PM1OBJ2", "U_AC", "V", "voltage", requires=requires),
        *_power_factor_metrics("meter2/power_factor", "Meter 2 power factor", "PM1OBJ2", requires=requires),
    ]


def _pv_string_metrics() -> list[Metric]:
    metrics = []
    for index in range(3):
        number = index + 1
        for field, part, label, precision, unit, device_class, legacy in (
            ("MPP_VOL", "voltage", "voltage", 2, "V", "voltage", "potential"),
            ("MPP_CUR", "current", "current", 2, "A", "current", "current"),
            ("MPP_POWER", "power", "power", 1, "W", "power", "power"),
        ):
            metrics.append(
                Metric(
                    topic=f"pv/string/{number}/{part}",
                    name=f"PV string {number} {label}",
                    section="PV1",
                    field=field,
                    index=index,
                    precision=precision,
                    unit=unit,
                    device_class=device_class,
                    legacy_id=f"senec_solar_mpp{number}_{legacy}",
                    requires=("PV1", "MPP_AVAIL", number),
                )
            )
    return metrics


def _module_metrics() -> list[Metric]:
    metrics = []
    for index, letter in enumerate(_MODULE_LETTERS):
        requires = ("BMS", "MODULES_CONFIGURED", index + 1)
        label = f"Battery module {letter.upper()}"

        def add(part: str, name: str, field: str, precision: int, unit: str | None = None,
                device_class: str | None = None, **kwargs: Any) -> None:
            metrics.append(
                Metric(
                    topic=f"battery/module/{letter}/{part}",
                    name=f"{label} {name}",
                    section="BMS",
                    field=field,
                    index=index,
                    precision=precision,
                    unit=unit,
                    device_class=device_class,
                    requires=requires,
                    **kwargs,
                )
            )

        add("soc", "state of charge", "SOC", 0, "%", "battery")
        add("soh", "state of health", "SOH", 0, "%")
        add("cycles", "cycles", "CYCLES", 0, state_class="total_increasing")
        add("voltage", "voltage", "VOLTAGE", 2, "V", "voltage")
        add("current", "current", "CURRENT", 2, "A", "current", scale=-1.0)
        add("temperature/min", "minimum temperature", "TEMP_MIN", 0, "°C", "temperature")
        add("temperature/max", "maximum temperature", "TEMP_MAX", 0, "°C", "temperature")
        add("cell_voltage/min", "minimum cell voltage", "MIN_CELL_VOLTAGE", 3, "V", "voltage", scale=_MILLI)
        add("cell_voltage/max", "maximum cell voltage", "MAX_CELL_VOLTAGE", 3, "V", "voltage", scale=_MILLI)
        add("charge_limit", "charge current limit", "CHARGE_CURRENT_LIMIT", 1, "A", "current", advanced=True)
        add("discharge_limit", "discharge current limit", "DISCHARGE_CURRENT_LIMIT", 1, "A", "current", scale=-1.0, advanced=True)
        add("status", "status", "STATUS", 0, state_class=None, advanced=True)
        add("balancing", "cell balancing status", "CELL_BALANCE_STATUS", 0, state_class=None, advanced=True)
        # The unit of these counters is not documented, so none is claimed.
        add("charged_counter", "charged energy counter", "CHARGED_ENERGY", 0, state_class="total_increasing", advanced=True)
        add("discharged_counter", "discharged energy counter", "DISCHARGED_ENERGY", 0, state_class="total_increasing", advanced=True)

        for cell in range(6):
            metrics.append(
                Metric(
                    topic=f"battery/module/{letter}/cell/{cell + 1}/temperature",
                    name=f"{label} cell {cell + 1} temperature",
                    section="BMS",
                    field=f"CELL_TEMPERATURES_MODULE_{letter.upper()}",
                    index=cell,
                    precision=0,
                    unit="°C",
                    device_class="temperature",
                    legacy_id=f"senec_bms_cell_temp_{letter}{cell + 1}",
                    requires=requires,
                    advanced=True,
                )
            )
        for cell in range(16):
            metrics.append(
                Metric(
                    topic=f"battery/module/{letter}/cell/{cell + 1}/voltage",
                    name=f"{label} cell {cell + 1} voltage",
                    section="BMS",
                    field=f"CELL_VOLTAGES_MODULE_{letter.upper()}",
                    index=cell,
                    scale=_MILLI,
                    precision=3,
                    unit="V",
                    device_class="voltage",
                    requires=requires,
                    advanced=True,
                )
            )
    return metrics


METRICS: tuple[Metric, ...] = (
    # Read every poll
    Metric("grid/power", "Grid power", "ENERGY", "GUI_GRID_POW", 1, "W", "power", legacy_id="senec_grid_state_power", fast=True),
    *_phase_metrics("grid/power", "Grid power", "PM1OBJ1", "P_AC", "W", "power", legacy="power", fast=True),
    *_phase_metrics("grid/current", "Grid current", "PM1OBJ1", "I_AC", "A", "current", legacy="current", fast=True),
    *_phase_metrics("grid/voltage", "Grid voltage", "PM1OBJ1", "U_AC", "V", "voltage", legacy="potential", fast=True),
    *_power_factor_metrics("grid/power_factor", "Grid power factor", "PM1OBJ1", fast=True),
    Metric("grid/frequency", "Grid frequency", "PM1OBJ1", "FREQ", 3, "Hz", "frequency", legacy_id="senec_enfluri_net_freq", fast=True),
    Metric("pv/power", "PV power", "ENERGY", "GUI_INVERTER_POWER", 1, "W", "power", legacy_id="senec_solar_generated_power", fast=True),
    Metric("house/power", "House power", "ENERGY", "GUI_HOUSE_POW", 1, "W", "power", legacy_id="senec_house_power", fast=True),
    Metric("battery/power", "Battery power", "ENERGY", "GUI_BAT_DATA_POWER", 1, "W", "power", scale=-1.0, fast=True),
    Metric("battery/voltage", "Battery voltage", "ENERGY", "GUI_BAT_DATA_VOLTAGE", 2, "V", "voltage", legacy_id="senec_battery_state_voltage", fast=True),
    Metric("battery/current", "Battery current", "ENERGY", "GUI_BAT_DATA_CURRENT", 2, "A", "current", scale=-1.0, fast=True),
    Metric("battery/soc", "Battery state of charge", "ENERGY", "GUI_BAT_DATA_FUEL_CHARGE", 1, "%", "battery", legacy_id="senec_battery_charge_percent", fast=True),
    Metric("battery/safe_charge", "Battery forced charging", "ENERGY", "SAFE_CHARGE_RUNNING", 0, state_class=None, binary=True, fast=True),
    Metric("system/state", "System state", "ENERGY", "STAT_STATE", 0, state_class=None, fast=True),
    # Read every DETAIL_INTERVAL
    Metric("grid/limited", "Grid feed-in limited", "ENERGY", "STAT_LIMITED_NET_SKEW", 0, state_class=None, binary=True),
    Metric("grid/zero_export", "Zero export", "ENERGY", "ZERO_EXPORT", 0, state_class=None, binary=True, advanced=True),
    *_meter2_metrics(),
    Metric("pv/power_ratio", "PV power limit", "PV1", "POWER_RATIO", 1, "%"),
    *(
        Metric(f"pv/power_ratio/{phase}", f"PV power limit {phase.upper()}", "PV1", f"POWER_RATIO_{phase.upper()}", 1, "%", advanced=True)
        for phase in _PHASES
    ),
    *_pv_string_metrics(),
    Metric("battery/temperature", "Battery temperature", "TEMPMEASURE", "BATTERY_TEMP", 1, "°C", "temperature", legacy_id="senec_battery_temp"),
    Metric("battery/temperature/min", "Battery minimum temperature", "BMS", "MIN_TEMP", 1, "°C", "temperature", scale=_DECI),
    Metric("battery/temperature/max", "Battery maximum temperature", "BMS", "MAX_TEMP", 1, "°C", "temperature", scale=_DECI),
    Metric("battery/soh", "Battery state of health", "BMS", "SYSTEM_SOH", 1, "%", scale=_DECI),
    Metric("battery/modules", "Battery modules", "BMS", "MODULES_CONFIGURED", 0, state_class=None, advanced=True),
    Metric("battery/charge_limit", "Battery charge current limit", "BMS", "SYSTEM_CHARGE_CURRENT_LIMIT", 1, "A", "current", advanced=True),
    Metric("battery/discharge_limit", "Battery discharge current limit", "BMS", "SYSTEM_DISCHARGE_CURRENT_LIMIT", 1, "A", "current", advanced=True),
    Metric("battery/spare_capacity", "Battery spare capacity", "BAT1", "SPARE_CAPACITY", 0, "%"),
    Metric("battery/charging", "Battery charging", "ENERGY", "GUI_CHARGING_INFO", 0, state_class=None, binary=True),
    Metric("battery/boosting", "Battery boosting", "ENERGY", "GUI_BOOSTING_INFO", 0, state_class=None, binary=True, advanced=True),
    Metric("battery/storage_mode", "Battery storage mode", "ENERGY", "LI_STORAGE_MODE_RUNNING", 0, state_class=None, binary=True, advanced=True),
    Metric("battery/derating", "Battery derating", "BMS", "DERATING", 0, state_class=None, advanced=True),
    Metric("battery/bms_status", "BMS status", "BMS", "BMS_STATUS", 0, state_class=None, advanced=True),
    Metric("battery/bms_error", "BMS error", "BMS", "ERROR", 0, state_class=None, advanced=True),
    Metric("battery/dc/voltage", "Battery DC voltage", "BAT1OBJ1", "U_DC", 2, "V", "voltage", advanced=True),
    Metric("battery/dc/current", "Battery DC current", "BAT1OBJ1", "I_DC", 2, "A", "current", scale=-1.0, advanced=True),
    # What these two sensors measure is not documented.
    Metric("bat1/temperature/1", "BAT1 temperature 1", "BAT1OBJ1", "TEMP1", 0, "°C", "temperature", advanced=True),
    Metric("bat1/temperature/2", "BAT1 temperature 2", "BAT1OBJ1", "TEMP2", 0, "°C", "temperature", advanced=True),
    *_module_metrics(),
    Metric("system/temperature/case", "Case temperature", "TEMPMEASURE", "CASE_TEMP", 1, "°C", "temperature", legacy_id="senec_case_temp"),
    Metric("system/temperature/mcu", "MCU temperature", "TEMPMEASURE", "MCU_TEMP", 1, "°C", "temperature", legacy_id="senec_mcu_temp"),
    Metric("system/hours_of_operation", "Hours of operation", "ENERGY", "STAT_HOURS_OF_OPERATION", 0, "h", "duration", state_class="total_increasing"),
)


def _build_forms(metrics: Iterable[Metric]) -> list[dict[str, dict[str, str]]]:
    """Group the fields into request forms below the web UI's size limit."""
    fields: dict[tuple[str, str], None] = {}
    for metric in metrics:
        fields[(metric.section, metric.field)] = None
        if metric.requires is not None:
            fields[metric.requires[:2]] = None
        for field in metric.apparent_power_of or ():
            fields[(metric.section, field)] = None

    forms: list[dict[str, dict[str, str]]] = [{}]
    for section, field in fields:
        candidate = {name: dict(values) for name, values in forms[-1].items()}
        candidate.setdefault(section, {})[field] = ""
        if forms[-1] and len(json.dumps(candidate, separators=(",", ":"))) >= _MAX_FORM_LENGTH:
            forms.append({section: {field: ""}})
        else:
            forms[-1] = candidate
    return [form for form in forms if form]


@dataclass(frozen=True)
class ReadPlan:
    """Which values are read at which rate, and the requests that fetches them."""

    poll: tuple[Metric, ...]
    detail: tuple[Metric, ...]
    off: tuple[Metric, ...]
    poll_forms: tuple[dict[str, dict[str, str]], ...]
    detail_forms: tuple[dict[str, dict[str, str]], ...]

    @property
    def active(self) -> tuple[Metric, ...]:
        return self.poll + self.detail

    def warnings(self) -> list[str]:
        """Describe choices that work, but that the user should know about."""
        messages = []
        polled = {metric.topic for metric in self.poll}
        moved = sorted(EVCC_TOPICS - polled)
        if moved:
            messages.append(f"Not read every poll, evcc may report timeouts: {', '.join(moved)}")
        if len(self.poll_forms) > 1:
            messages.append(f"Poll values need {len(self.poll_forms)} requests per poll, which adds load on the SENEC")
        return messages


def _literal_length(pattern: str) -> int:
    return len(pattern.replace("*", "").replace("?", ""))


def build_plan(rates: Mapping[str, str]) -> ReadPlan:
    """Apply ``{topic or wildcard: rate}`` on top of the recommended split.

    The rule with the most literal characters wins, so a topic written out in
    full beats any wildcard. On a tie, the later rule wins.
    """
    assigned = {metric.topic: "poll" if metric.fast else "detail" for metric in METRICS}
    for pattern, rate in sorted(rates.items(), key=lambda rule: _literal_length(rule[0])):
        if rate not in RATES:
            raise ValueError(f"{pattern}: rate must be one of {', '.join(RATES)}, not {rate!r}")
        matches = [topic for topic in assigned if fnmatchcase(topic, pattern)]
        if not matches:
            raise ValueError(f"{pattern}: does not match any value")
        assigned.update(dict.fromkeys(matches, rate))

    grouped = {rate: tuple(metric for metric in METRICS if assigned[metric.topic] == rate) for rate in RATES}
    if not grouped["poll"]:
        raise ValueError("at least one value must stay on poll, it decides whether the SENEC is online")
    return ReadPlan(
        poll=grouped["poll"],
        detail=grouped["detail"],
        off=grouped["off"],
        poll_forms=tuple(_build_forms(grouped["poll"])),
        detail_forms=tuple(_build_forms(grouped["detail"])),
    )


DEFAULT_PLAN = build_plan({})


def merge_sections(*parts: Raw) -> dict[str, dict[str, Any]]:
    """Combine responses whose sections may overlap."""
    merged: dict[str, dict[str, Any]] = {}
    for part in parts:
        for section, fields in part.items():
            if isinstance(fields, Mapping):
                merged.setdefault(section, {}).update(fields)
    return merged


def render_all(raw: Raw, plan: ReadPlan = DEFAULT_PLAN) -> dict[str, str]:
    """Return ``{topic suffix: payload}`` for every enabled value present in ``raw``."""
    rendered = {}
    for metric in plan.active:
        payload = metric.render(raw)
        if payload is not None:
            rendered[metric.topic] = payload
    return rendered
