import json
from pathlib import Path

import pytest

from senec_mqtt_gateway.config import ConfigError, load_plan
from senec_mqtt_gateway.schema import DEFAULT_PLAN, METRICS, build_plan, render_all

EXAMPLE = Path(__file__).parent.parent / "metrics.example.toml"


def topics(metrics):
    return {metric.topic for metric in metrics}


def requested(forms):
    return {(section, field) for form in forms for section, fields in form.items() for field in fields}


def test_recommended_split_follows_the_metric_list():
    assert topics(DEFAULT_PLAN.poll) == {metric.topic for metric in METRICS if metric.fast}
    assert topics(DEFAULT_PLAN.detail) == {metric.topic for metric in METRICS if not metric.fast}
    assert DEFAULT_PLAN.off == ()
    assert len(DEFAULT_PLAN.poll_forms) == 1
    assert DEFAULT_PLAN.warnings() == []


def test_value_can_move_to_poll():
    plan = build_plan({"battery/temperature": "poll"})
    assert "battery/temperature" in topics(plan.poll)
    assert "battery/temperature" not in topics(plan.detail)
    assert ("TEMPMEASURE", "BATTERY_TEMP") in requested(plan.poll_forms)
    assert ("TEMPMEASURE", "BATTERY_TEMP") not in requested(plan.detail_forms)


def test_condition_field_is_read_with_a_moved_value():
    plan = build_plan({"battery/module/a/soc": "poll"})
    assert ("BMS", "MODULES_CONFIGURED") in requested(plan.poll_forms)


@pytest.mark.parametrize(
    "rates",
    [
        {"battery/module/*/cell/*": "off", "battery/module/a/cell/1/voltage": "detail"},
        {"battery/module/a/cell/1/voltage": "detail", "battery/module/*/cell/*": "off"},
    ],
)
def test_written_out_topic_beats_wildcard_in_any_order(rates):
    plan = build_plan(rates)
    assert "battery/module/a/cell/1/voltage" in topics(plan.detail)
    assert "battery/module/a/cell/2/voltage" in topics(plan.off)


def test_narrower_wildcard_beats_broader_one():
    plan = build_plan({"battery/*": "off", "battery/module/a/*": "detail", "battery/soc": "poll"})
    assert "battery/module/b/soc" in topics(plan.off)
    assert "battery/module/a/soc" in topics(plan.detail)
    assert "battery/soc" in topics(plan.poll)


def test_later_rule_wins_a_tie():
    plan = build_plan({"pv/*/1/power": "off", "pv/string/*r": "poll"})
    assert len("pv//1/power") == len("pv/string/r")
    assert "pv/string/1/power" in topics(plan.poll)


def test_disabled_value_is_not_rendered_even_if_its_field_is_read():
    plan = build_plan({"grid/voltage/l1": "off"})
    rendered = render_all({"PM1OBJ1": {"U_AC": [230.0, 231.0, 232.0]}}, plan)
    assert ("PM1OBJ1", "U_AC") in requested(plan.poll_forms)
    assert "grid/voltage/l1" not in rendered
    assert rendered["grid/voltage/l2"] == "231.00"


def test_disabled_field_is_not_requested():
    plan = build_plan({"meter2/*": "off"})
    assert not any(section == "PM1OBJ2" for section, _ in requested(plan.detail_forms))


def test_large_poll_set_is_split_below_the_limit_and_reported():
    plan = build_plan({"battery/*": "poll"})
    assert len(plan.poll_forms) > 1
    for form in plan.poll_forms:
        assert len(json.dumps(form, separators=(",", ":"))) < 600
    assert any("requests per poll" in warning for warning in plan.warnings())


@pytest.mark.parametrize("topic", ["grid/power", "battery/soc"])
@pytest.mark.parametrize("rate", ["detail", "off"])
def test_moving_evcc_value_away_from_poll_warns(topic, rate):
    warnings = build_plan({topic: rate}).warnings()
    assert any("evcc" in warning and topic in warning for warning in warnings)


@pytest.mark.parametrize(
    "rates",
    [
        {"no/such/value": "poll"},
        {"battery/tempreature": "off"},
        {"grid/power": "sometimes"},
        {"*": "off"},
        {"*": "detail"},
    ],
)
def test_invalid_rules_are_rejected(rates):
    with pytest.raises(ValueError):
        build_plan(rates)


def test_no_file_means_recommended_split():
    assert load_plan(None) is DEFAULT_PLAN


def test_file_is_applied(tmp_path):
    path = tmp_path / "metrics.toml"
    path.write_text('[rates]\n"meter2/*" = "off"\n')
    assert "meter2/frequency" in topics(load_plan(str(path)).off)


@pytest.mark.parametrize(
    "content",
    ["not toml =", '"grid/power" = "poll"\n', '[rates]\n"no/such" = "poll"\n', '[rates]\n[other]\n'],
)
def test_bad_file_is_a_config_error(tmp_path, content):
    path = tmp_path / "metrics.toml"
    path.write_text(content)
    with pytest.raises(ConfigError):
        load_plan(str(path))


def test_missing_file_is_a_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_plan(str(tmp_path / "absent.toml"))


def test_example_file_changes_nothing_as_shipped():
    assert load_plan(str(EXAMPLE)) == DEFAULT_PLAN


def test_example_rules_are_valid_when_uncommented(tmp_path):
    lines = [line.removeprefix("# ") if line.startswith('# "') else line for line in EXAMPLE.read_text().splitlines()]
    path = tmp_path / "metrics.toml"
    path.write_text("\n".join(lines))
    plan = load_plan(str(path))
    assert "battery/temperature" in topics(plan.poll)
    assert "battery/module/a/cell/1/voltage" in topics(plan.detail)
    assert "battery/module/a/cell/2/voltage" in topics(plan.off)
