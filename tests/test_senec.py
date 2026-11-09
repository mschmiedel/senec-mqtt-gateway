import json

import aiohttp
import pytest

from senec_mqtt_gateway.schema import build_plan
from senec_mqtt_gateway.senec import SenecClient, SenecError, create_session


@pytest.fixture
async def client(fake_senec):
    async with create_session(timeout=2) as session:
        yield SenecClient(session, fake_senec.url, retry_delay=0, verify_delay=0, form_delay=0)


async def test_read_values(client):
    values = await client.read_values()
    assert values["ENERGY"]["GUI_INVERTER_POWER"] == 3000.0
    assert len(values["PM1OBJ1"]["U_AC"]) == 3
    assert abs(values["PM1OBJ1"]["FREQ"] - 50.012) < 1e-3


async def test_one_bundled_request(client, fake_senec):
    await client.read_values()
    assert len(fake_senec.requests) == 1
    assert set(fake_senec.requests[0]) == {"ENERGY", "PM1OBJ1"}


async def test_request_stays_below_web_ui_chunk_size(client, fake_senec):
    await client.read_values()
    assert len(json.dumps(fake_senec.requests[0], separators=(",", ":"))) < 600


async def test_details_are_merged_across_requests(client, fake_senec):
    details = await client.read_details()
    assert len(fake_senec.requests) > 1
    assert details["BMS"]["MODULES_CONFIGURED"] == 2
    assert details["BMS"]["CURRENT"][1] == 22.5
    assert details["TEMPMEASURE"]["CASE_TEMP"] == 28.5


async def test_large_poll_set_is_read_in_several_requests(fake_senec):
    async with create_session(timeout=2) as session:
        client = SenecClient(session, fake_senec.url, retry_delay=0, form_delay=0, plan=build_plan({"battery/*": "poll"}))
        values = await client.read_values()
    assert len(fake_senec.requests) > 1
    assert values["ENERGY"]["GUI_INVERTER_POWER"] == 3000.0
    assert values["BMS"]["MODULES_CONFIGURED"] == 2


async def test_all_details_disabled_sends_no_request(fake_senec):
    async with create_session(timeout=2) as session:
        plan = build_plan({key: "off" for key in ("grid/limited", "grid/zero_export", "meter2/*", "pv/power_ratio*", "pv/string/*", "battery/temperature*", "battery/soh", "battery/modules", "battery/charge_limit", "battery/discharge_limit", "battery/spare_capacity", "battery/charging", "battery/boosting", "battery/storage_mode", "battery/derating", "battery/bms_*", "battery/dc/*", "bat1/*", "battery/module/*", "system/temperature/*", "system/hours_of_operation")})
        assert plan.detail == ()
        assert await SenecClient(session, fake_senec.url, plan=plan).read_details() == {}
    assert fake_senec.requests == []


async def test_details_do_not_read_identity_sections(client, fake_senec):
    await client.read_details()
    assert not any("FACTORY" in form or "WIZARD" in form for form in fake_senec.requests)


async def test_session_cookie_is_sent_afterwards(client, fake_senec):
    await client.start_session()
    await client.read_values()
    assert "session=abc" in fake_senec.headers[-1].get("Cookie", "")


async def test_start_session_tolerates_errors(client, fake_senec):
    fake_senec.fail_with = 500
    await client.start_session()


async def test_read_failure_raises_after_retry(client, fake_senec):
    fake_senec.fail_with = 500
    with pytest.raises(SenecError):
        await client.read_values()


async def test_incomplete_response_raises(fake_senec):
    class Incomplete(SenecClient):
        async def _post(self, form, extra_headers=None):
            return {"ENERGY": {}}

    async with aiohttp.ClientSession() as session:
        with pytest.raises(SenecError):
            await Incomplete(session, fake_senec.url, retry_delay=0).read_values()


async def test_unreachable_host_raises():
    async with create_session(timeout=1) as session:
        client = SenecClient(session, "http://127.0.0.1:1", retry_delay=0)
        with pytest.raises(SenecError):
            await client.read_values()


async def test_device_info_does_not_expose_serial(client):
    info = await client.read_device_info()
    assert info.model == "SYS_TYPE 17"
    assert info.version == "NPU 2411 / App 5"
    assert "SECRET" not in repr(info)


async def test_charge_command(client, fake_senec):
    await client.set_battery_mode("charge")
    assert fake_senec.writes() == [{"SAFE_CHARGE_FORCE": "u8_01"}]
    assert fake_senec.values["ENERGY"]["SAFE_CHARGE_RUNNING"] == "u8_01"


async def test_normal_command(client, fake_senec):
    fake_senec.values["ENERGY"]["SAFE_CHARGE_RUNNING"] = "u8_01"
    await client.set_battery_mode("normal")
    assert fake_senec.writes() == [{"SAFE_CHARGE_PROHIBIT": "u8_01"}]
    assert fake_senec.values["ENERGY"]["SAFE_CHARGE_RUNNING"] == "u8_00"


async def test_write_sends_web_ui_headers(client, fake_senec):
    await client.set_battery_mode("charge")
    headers = next(h for form, h in zip(fake_senec.requests, fake_senec.headers) if "SAFE_CHARGE_FORCE" in form["ENERGY"])
    assert headers["X-Requested-With"] == "XMLHttpRequest"
    assert headers["Origin"] == fake_senec.url
