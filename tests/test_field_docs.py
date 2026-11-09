from pathlib import Path

from senec_mqtt_gateway.field_docs import render
from senec_mqtt_gateway.schema import METRICS, build_plan, render_all

FIELDS = Path(__file__).parent.parent / "docs" / "FIELDS.md"


def test_generated_file_is_up_to_date():
    assert FIELDS.read_text() == render(), "run: uv run python -m senec_mqtt_gateway.field_docs > docs/FIELDS.md"


def test_every_metric_source_is_documented():
    document = render()
    for metric in METRICS:
        field = metric.field.rsplit("_MODULE_", 1)[0].rsplit("_L", 1)[0]
        assert f"`{metric.section}.{field}" in document, metric.topic


def test_calculated_values_are_marked_as_calculated():
    document = render()
    row = next(line for line in document.splitlines() if line.startswith("| `grid/power_factor/l<p>`"))
    assert "Calculated from `PM1OBJ1.P_AC[ ]`" in row
    assert "calculated by the gateway" in row


def test_system_state_topic_keeps_the_number():
    assert render_all({"ENERGY": {"STAT_STATE": 8}})["system/state"] == "8"
    assert render_all({"ENERGY": {"STAT_STATE": 250}})["system/state"] == "250"


def test_every_system_state_is_documented():
    assert "| 8 | MAN. SAFETY CHARGE | MAN. SICHERHEITSLADUNG |" in render()


def _phases(**overrides):
    return {
        "PM1OBJ1": {
            "U_AC": [228.7, 226.7, 228.8],
            "I_AC": [6.94, 6.87, 0.64],
            "P_AC": [-1529.83, 1476.66, 34.76],
            **overrides,
        }
    }


def test_current_stays_as_reported_by_the_device():
    rendered = render_all(_phases())
    assert rendered["grid/current/l1"] == "6.94"


def test_power_factor_shows_direction():
    rendered = render_all(_phases())
    assert rendered["grid/power_factor/l1"] == "-0.96"
    assert rendered["grid/power_factor/l2"] == "0.95"
    assert rendered["grid/power_factor/l3"] == "0.24"


def test_power_factor_is_limited_to_one():
    rendered = render_all(_phases(P_AC=[9999.0, -9999.0, 0.0]))
    assert rendered["grid/power_factor/l1"] == "1.00"
    assert rendered["grid/power_factor/l2"] == "-1.00"


def test_power_factor_needs_current_and_voltage():
    rendered = render_all(_phases(I_AC=[0.0, 6.87]))
    assert "grid/power_factor/l1" not in rendered
    assert "grid/power_factor/l3" not in rendered
    assert "grid/power_factor/l2" in rendered


def test_power_factor_inputs_are_read_when_they_are_disabled():
    plan = build_plan({"grid/power/*": "off", "grid/voltage/*": "off", "grid/current/*": "off"})
    assert {"P_AC", "U_AC", "I_AC"} <= set(plan.poll_forms[0]["PM1OBJ1"])
    rendered = render_all(_phases(), plan)
    assert rendered["grid/power_factor/l1"] == "-0.96"
    assert "grid/power/l1" not in rendered
    assert "grid/current/l1" not in rendered
