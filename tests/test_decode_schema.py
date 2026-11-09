import json

from senec_mqtt_gateway.decode import decode_tree, decode_value
from senec_mqtt_gateway.schema import DEFAULT_PLAN, METRICS, merge_sections, render_all

from conftest import fl

READ_FORM = DEFAULT_PLAN.poll_forms[0]
DETAIL_FORMS = DEFAULT_PLAN.detail_forms


def test_float():
    assert decode_value("fl_43708000") == 240.5


def test_unsigned():
    assert decode_value("u8_0E") == 14
    assert decode_value("u3_FFFFFFFF") == 4294967295


def test_signed_is_twos_complement():
    assert decode_value("i8_FF") == -1
    assert decode_value("i1_8000") == -32768
    assert decode_value("i3_00000010") == 16


def test_string_keeps_underscores():
    assert decode_value("st_a_b_c") == "a_b_c"


def test_unencoded_and_unknown_values_are_unchanged():
    assert decode_value("plain") == "plain"
    assert decode_value("VARIABLE_NOT_FOUND") == "VARIABLE_NOT_FOUND"
    assert decode_value("xy_12") == "xy_12"


def test_malformed_hex_is_unchanged():
    assert decode_value("fl_XYZ") == "fl_XYZ"
    assert decode_value("u8_ZZ") == "u8_ZZ"


def test_tree_does_not_mutate_input():
    raw = {"A": {"B": "u8_01", "C": ["u8_02", "u8_03"]}}
    assert decode_tree(raw) == {"A": {"B": 1, "C": [2, 3]}}
    assert raw["A"]["B"] == "u8_01"


def _raw():
    return {
        "ENERGY": {
            "GUI_GRID_POW": -1200.54,
            "GUI_BAT_DATA_POWER": 1000.0,
            "GUI_BAT_DATA_CURRENT": 19.0,
            "SAFE_CHARGE_RUNNING": 1,
        },
        "PM1OBJ1": {"FREQ": 50.0123, "U_AC": [230.123, 231.0, 232.0]},
    }


def test_signs_follow_evcc():
    rendered = render_all(_raw())
    assert rendered["grid/power"] == "-1200.5"
    assert rendered["battery/power"] == "-1000.0"
    assert rendered["battery/current"] == "-19.00"


def test_phases_and_precision():
    rendered = render_all(_raw())
    assert rendered["grid/voltage/l1"] == "230.12"
    assert rendered["grid/voltage/l3"] == "232.00"
    assert rendered["grid/frequency"] == "50.012"
    assert rendered["battery/safe_charge"] == "1"


def test_no_negative_zero():
    rendered = render_all(
        {"ENERGY": {"GUI_INVERTER_POWER": -0.0, "GUI_BAT_DATA_POWER": 0.0, "GUI_GRID_POW": -0.04}}
    )
    assert rendered["pv/power"] == "0.0"
    assert rendered["battery/power"] == "0.0"
    assert rendered["grid/power"] == "0.0"


def test_missing_values_are_skipped():
    rendered = render_all({"ENERGY": {"GUI_GRID_POW": "VARIABLE_NOT_FOUND"}, "PM1OBJ1": {"U_AC": [230.0]}})
    assert rendered == {"grid/voltage/l1": "230.00"}


def test_nan_is_skipped():
    assert "grid/power" not in render_all({"ENERGY": {"GUI_GRID_POW": float("nan")}})


def test_topics_are_unique():
    topics = [metric.topic for metric in METRICS]
    assert len(topics) == len(set(topics))


def _requested_fields(forms):
    return {(section, field) for form in forms for section, fields in form.items() for field in fields}


def test_every_metric_is_requested_at_its_rate():
    fast = _requested_fields([READ_FORM])
    detail = _requested_fields(DETAIL_FORMS)
    for metric in METRICS:
        assert (metric.section, metric.field) in (fast if metric.fast else detail), metric.topic
        if metric.requires:
            assert metric.requires[:2] in detail, metric.topic


def test_forms_stay_below_web_ui_chunk_size():
    for form in [READ_FORM, *DETAIL_FORMS]:
        assert len(json.dumps(form, separators=(",", ":"))) < 600


def test_read_forms_never_write():
    for form in [READ_FORM, *DETAIL_FORMS]:
        assert all(value == "" for fields in form.values() for value in fields.values())


def test_modules_above_configured_count_are_hidden():
    rendered = render_all(
        {"BMS": {"MODULES_CONFIGURED": 1, "SOC": [84, 0], "CELL_VOLTAGES_MODULE_B": [3390]}}
    )
    assert rendered["battery/module/a/soc"] == "84"
    assert "battery/module/b/soc" not in rendered
    assert "battery/module/b/cell/1/voltage" not in rendered


def test_module_values_without_known_count_are_hidden():
    assert "battery/module/a/soc" not in render_all({"BMS": {"SOC": [84]}})


def test_unused_pv_strings_are_hidden():
    rendered = render_all({"PV1": {"MPP_AVAIL": 2, "MPP_VOL": [250.0, 240.0, 0.0]}})
    assert rendered["pv/string/2/voltage"] == "240.00"
    assert "pv/string/3/voltage" not in rendered


def test_second_meter_only_when_enabled():
    raw = {"PM1OBJ2": {"ENABLED": 0, "U_AC": [0.0, 0.0, 0.0]}}
    assert "meter2/voltage/l1" not in render_all(raw)
    raw = {"PM1OBJ2": {"ENABLED": 1, "U_AC": [230.0, 0.0, 0.0]}}
    assert render_all(raw)["meter2/voltage/l1"] == "230.00"


def test_units_are_converted():
    rendered = render_all(
        {"BMS": {"MODULES_CONFIGURED": 1, "CELL_VOLTAGES_MODULE_A": [3390], "MAX_TEMP": 322, "CURRENT": [23.2]}}
    )
    assert rendered["battery/module/a/cell/1/voltage"] == "3.390"
    assert rendered["battery/temperature/max"] == "32.2"
    assert rendered["battery/module/a/current"] == "-23.20"


def test_text_values_are_never_published():
    rendered = render_all({"BMS": {"MODULES_CONFIGURED": 1, "SOC": ["SECRET"]}})
    assert "SECRET" not in rendered.values()


def test_merge_keeps_fields_of_overlapping_sections():
    merged = merge_sections({"ENERGY": {"A": 1}}, {"ENERGY": {"B": 2}, "BMS": {"C": 3}})
    assert merged == {"ENERGY": {"A": 1, "B": 2}, "BMS": {"C": 3}}


def test_helper_roundtrip():
    assert abs(decode_value(fl(230.5)) - 230.5) < 1e-6
