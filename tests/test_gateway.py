"""End-to-end tests: simulated SENEC, real MQTT broker, real gateway."""

import asyncio
import contextlib
import dataclasses
import json
import logging

import aiomqtt
import pytest

from conftest import start_broker
from senec_mqtt_gateway.config import Config, ConfigError
from senec_mqtt_gateway.gateway import Gateway
from senec_mqtt_gateway.schema import DEFAULT_PLAN, build_plan
from senec_mqtt_gateway.senec import SenecClient, SenecError, create_session


def make_config(fake, port, **overrides):
    return Config(
        senec_url=fake.url,
        poll_interval=0.05,
        mqtt_host="127.0.0.1",
        mqtt_port=port,
        **overrides,
    )


@contextlib.asynccontextmanager
async def running_gateway(config, plan=DEFAULT_PLAN):
    async with create_session(timeout=1) as session:
        senec = SenecClient(session, config.senec_url, retry_delay=0, verify_delay=0, form_delay=0, plan=plan)
        gateway = Gateway(config, senec)
        task = asyncio.create_task(gateway.run())
        try:
            yield task
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task


@contextlib.asynccontextmanager
async def collector(port, topic="#"):
    seen = {}

    async with aiomqtt.Client("127.0.0.1", port, identifier="test-collector") as client:
        await client.subscribe(topic, qos=1)

        async def collect():
            async for message in client.messages:
                seen[message.topic.value] = bytes(message.payload).decode()

        task = asyncio.create_task(collect())
        try:
            yield seen, client
        finally:
            task.cancel()


async def wait_for(condition, timeout=3.0):
    async with asyncio.timeout(timeout):
        while not condition():
            await asyncio.sleep(0.01)


async def test_publishes_values_with_evcc_signs(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")

    assert seen["senec/grid/power"] == "-1200.5"
    assert seen["senec/battery/power"] == "-1000.0"
    assert seen["senec/grid/voltage/l3"] == "232.30"
    assert seen["senec/grid/current/l2"] == "2.50"
    assert seen["senec/grid/power_factor/l2"] == "-0.35"
    assert seen["senec/grid/frequency"] == "50.012"
    assert seen["senec/battery/soc"] == "64.2" or seen["senec/battery/soc"] == "64.3"


async def test_values_are_published_repeatedly(fake_senec, mqtt_port):
    messages = []
    async with aiomqtt.Client("127.0.0.1", mqtt_port, identifier="test-repeat") as client:
        await client.subscribe("senec/pv/power")

        async def collect():
            async for message in client.messages:
                messages.append(message)

        task = asyncio.create_task(collect())
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: len(messages) >= 3)
        task.cancel()


async def test_values_are_retained_for_late_subscribers(fake_senec, mqtt_port):
    config = make_config(fake_senec, mqtt_port)
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(config):
            await wait_for(lambda: seen.get("senec/status") == "online")
    async with collector(mqtt_port, "senec/pv/power") as (late, _):
        await wait_for(lambda: late.get("senec/pv/power") == "3000.0")


async def test_clean_shutdown_marks_offline(fake_senec, mqtt_port, monkeypatch):
    # amqtt acknowledges a message and queues it, then drops the queue if the
    # DISCONNECT is handled first. Keep the connection open until it is delivered.
    publish_status = Gateway._publish_status

    async def publish_and_linger(self, client, status):
        await publish_status(self, client, status)
        await asyncio.sleep(0.2)

    monkeypatch.setattr(Gateway, "_publish_status", publish_and_linger)
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")
        await wait_for(lambda: seen.get("senec/status") == "offline")


async def test_senec_outage_and_recovery(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")
            fake_senec.fail_with = 503
            await wait_for(lambda: seen.get("senec/status") == "offline")
            fake_senec.fail_with = None
            await wait_for(lambda: seen.get("senec/status") == "online")


async def test_discovery(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")

    node = "senec_mqtt_gateway_senec"
    voltage = json.loads(seen[f"homeassistant/sensor/{node}/grid_voltage_l1/config"])
    assert voltage["state_topic"] == "senec/grid/voltage/l1"
    assert voltage["unit_of_measurement"] == "V"
    assert voltage["device_class"] == "voltage"
    assert voltage["availability_topic"] == "senec/status"
    assert voltage["default_entity_id"] == "sensor.senec_enfluri_net_potential_p1"
    assert voltage["device"]["sw_version"] == "NPU 2411 / App 5"

    charge = json.loads(seen[f"homeassistant/binary_sensor/{node}/battery_safe_charge/config"])
    assert charge["payload_on"] == "1"
    battery = json.loads(seen[f"homeassistant/sensor/{node}/battery_power/config"])
    assert battery["default_entity_id"] == "sensor.senec_battery_power"
    assert not any("SECRET" in payload for payload in seen.values())


async def test_details_are_published(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")

    assert seen["senec/battery/temperature"] == "32.2"
    assert seen["senec/battery/module/b/soc"] == "83"
    assert seen["senec/pv/string/1/power"] == "1500.0"
    assert "senec/battery/module/c/soc" not in seen
    assert "senec/battery/module/c/cell/1/voltage" not in seen


@pytest.mark.parametrize("discovery", [True, False])
async def test_details_are_read_less_often_than_values(fake_senec, mqtt_port, discovery):
    async with collector(mqtt_port, "senec/pv/power") as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, ha_discovery_enabled=discovery)):
            await wait_for(lambda: "senec/pv/power" in seen)
            await asyncio.sleep(0.3)

    fast = [form for form in fake_senec.requests if "GUI_GRID_POW" in form.get("ENERGY", {})]
    detail = [form for form in fake_senec.requests if "BATTERY_TEMP" in form.get("TEMPMEASURE", {})]
    assert len(fast) >= 3
    assert len(detail) == 1


@pytest.mark.parametrize("discovery", [True, False])
async def test_details_run_on_every_nth_poll(fake_senec, mqtt_port, discovery):
    def detail_requests():
        return sum("BATTERY_TEMP" in form.get("TEMPMEASURE", {}) for form in fake_senec.requests)

    config = make_config(fake_senec, mqtt_port, detail_interval=0.15, ha_discovery_enabled=discovery)
    async with running_gateway(config):
        await wait_for(lambda: detail_requests() >= 3)

    kinds = "".join(
        "D" if "BATTERY_TEMP" in form.get("TEMPMEASURE", {}) else "F"
        for form in fake_senec.requests
        if "BATTERY_TEMP" in form.get("TEMPMEASURE", {}) or "GUI_GRID_POW" in form.get("ENERGY", {})
    )
    assert kinds.startswith("FDFFFDFFFD")


async def test_adjusted_detail_interval_is_logged(fake_senec, mqtt_port, caplog):
    config = make_config(fake_senec, mqtt_port, detail_interval=0.07)
    with caplog.at_level(logging.INFO, logger="senec_mqtt_gateway.gateway"):
        async with running_gateway(config):
            await wait_for(lambda: "is not a multiple of POLL_INTERVAL" in caplog.text)
    assert "using 0.1" in caplog.text


async def test_exact_multiple_is_not_logged_as_adjusted(fake_senec, mqtt_port, caplog):
    config = make_config(fake_senec, mqtt_port, detail_interval=0.15)
    with caplog.at_level(logging.INFO, logger="senec_mqtt_gateway.gateway"):
        async with running_gateway(config):
            await wait_for(lambda: "Connected to MQTT broker" in caplog.text)
    assert "is not a multiple" not in caplog.text


async def test_value_moved_to_poll_is_published_without_details(fake_senec, mqtt_port):
    plan = build_plan({"battery/temperature": "poll"})
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, detail_interval=0), plan):
            await wait_for(lambda: seen.get("senec/status") == "online")
    assert seen["senec/battery/temperature"] == "32.2"
    assert "senec/system/temperature/case" not in seen


async def test_disabled_values_disappear_from_the_broker(fake_senec, mqtt_port):
    node = "senec_mqtt_gateway_senec"
    config_topic = f"homeassistant/sensor/{node}/battery_temperature/config"
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")
    assert "senec/battery/temperature" in seen and config_topic in seen

    plan = build_plan({"battery/temperature": "off"})
    async with collector(mqtt_port, "senec/status") as (status, _):
        async with running_gateway(make_config(fake_senec, mqtt_port), plan):
            await wait_for(lambda: status.get("senec/status") == "online")

    async with collector(mqtt_port) as (late, _):
        await wait_for(lambda: "senec/pv/power" in late)
        await asyncio.sleep(0.1)
    assert "senec/battery/temperature" not in late
    assert config_topic not in late
    assert f"homeassistant/sensor/{node}/system_temperature_case/config" in late


async def test_clearing_leaves_discovery_alone_when_it_is_disabled(fake_senec, mqtt_port):
    node = "senec_mqtt_gateway_senec"
    config_topic = f"homeassistant/sensor/{node}/battery_temperature/config"
    async with aiomqtt.Client("127.0.0.1", mqtt_port, identifier="test-foreign") as client:
        await client.publish(config_topic, payload="{}", qos=1, retain=True)
        await asyncio.sleep(0.05)

    plan = build_plan({"battery/temperature": "off"})
    config = make_config(fake_senec, mqtt_port, ha_discovery_enabled=False)
    async with collector(mqtt_port, "senec/status") as (status, _):
        async with running_gateway(config, plan):
            await wait_for(lambda: status.get("senec/status") == "online")

    async with collector(mqtt_port, "homeassistant/#") as (late, _):
        await wait_for(lambda: config_topic in late)


async def test_detail_failure_keeps_status_online_and_values_flowing(fake_senec, mqtt_port, monkeypatch):
    async def fail(self):
        raise SenecError("boom")

    monkeypatch.setattr(SenecClient, "read_details", fail)
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, detail_interval=0.01)):
            await wait_for(lambda: seen.get("senec/status") == "online")
            await asyncio.sleep(0.1)
            assert seen["senec/status"] == "online"
    assert seen["senec/pv/power"] == "3000.0"


async def test_details_can_be_disabled(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, detail_interval=0)):
            await wait_for(lambda: seen.get("senec/status") == "online")
    assert "senec/battery/temperature" not in seen
    assert not any("TEMPMEASURE" in form for form in fake_senec.requests)
    assert any(topic.endswith("grid_voltage_l1/config") for topic in seen)


async def test_discovery_covers_only_what_the_device_has(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")

    node = "senec_mqtt_gateway_senec"
    assert f"homeassistant/sensor/{node}/battery_module_b_soc/config" in seen
    assert f"homeassistant/sensor/{node}/battery_module_c_soc/config" not in seen
    assert f"homeassistant/sensor/{node}/meter2_frequency/config" not in seen
    assert f"homeassistant/sensor/{node}/pv_string_3_power/config" not in seen

    cell = json.loads(seen[f"homeassistant/sensor/{node}/battery_module_a_cell_1_voltage/config"])
    assert cell["enabled_by_default"] is False
    temperature = json.loads(seen[f"homeassistant/sensor/{node}/battery_temperature/config"])
    assert temperature["default_entity_id"] == "sensor.senec_battery_temp"
    assert "enabled_by_default" not in temperature


async def test_discovery_is_sent_once_the_senec_is_back(fake_senec, mqtt_port):
    fake_senec.fail_with = 503
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await asyncio.sleep(0.15)
            # An empty payload only clears the battery mode selection.
            assert not any(payload for topic, payload in seen.items() if topic.startswith("homeassistant/"))
            fake_senec.fail_with = None
            await wait_for(lambda: seen.get("senec/status") == "online")
    assert any(topic.endswith("battery_temperature/config") for topic in seen)


async def test_discovery_can_be_disabled(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, ha_discovery_enabled=False)):
            await wait_for(lambda: seen.get("senec/status") == "online")
    assert not any(topic.startswith("homeassistant/") for topic in seen)


async def test_custom_topic_prefix(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, topic_prefix="home/senec")):
            await wait_for(lambda: seen.get("home/senec/status") == "online")
    assert seen["home/senec/pv/power"] == "3000.0"


async def test_commands_are_off_by_default(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, client):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")
            await client.publish("senec/command/battery_mode", payload="charge", qos=1)
            await asyncio.sleep(0.2)
    assert fake_senec.writes() == []


async def test_command_sets_battery_mode(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, client):
        async with running_gateway(make_config(fake_senec, mqtt_port, commands_enabled=True)):
            await wait_for(lambda: seen.get("senec/status") == "online")
            await asyncio.sleep(0.1)
            await client.publish("senec/command/battery_mode", payload="charge", qos=1)
            await wait_for(lambda: seen.get("senec/battery/safe_charge") == "1")
            await client.publish("senec/command/battery_mode", payload="normal", qos=1)
            await wait_for(lambda: seen.get("senec/battery/safe_charge") == "0")
    assert fake_senec.writes() == [{"SAFE_CHARGE_FORCE": "u8_01"}, {"SAFE_CHARGE_PROHIBIT": "u8_01"}]


SELECT_TOPIC = "homeassistant/select/senec_mqtt_gateway_senec/battery_mode/config"


async def test_battery_mode_is_selectable_when_commands_are_enabled(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, commands_enabled=True)):
            await wait_for(lambda: seen.get("senec/status") == "online")

    select = json.loads(seen[SELECT_TOPIC])
    assert select["command_topic"] == "senec/command/battery_mode"
    assert select["state_topic"] == "senec/battery/safe_charge"
    assert select["options"] == ["normal", "charge"]
    assert select["retain"] is False
    assert select["availability_topic"] == "senec/status"
    assert select["default_entity_id"] == "select.senec_battery_mode"


async def test_battery_mode_is_not_selectable_without_commands(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: seen.get("senec/status") == "online")
    assert not seen.get(SELECT_TOPIC)


async def test_selection_disappears_when_commands_are_turned_off(fake_senec, mqtt_port):
    async with collector(mqtt_port, "senec/status") as (status, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, commands_enabled=True)):
            await wait_for(lambda: status.get("senec/status") == "online")

    async with collector(mqtt_port, "senec/status") as (status, _):
        status.clear()
        async with running_gateway(make_config(fake_senec, mqtt_port)):
            await wait_for(lambda: status.get("senec/status") == "online")

    async with collector(mqtt_port) as (late, _):
        await wait_for(lambda: "senec/pv/power" in late)
        await asyncio.sleep(0.1)
    assert SELECT_TOPIC not in late


async def test_no_selection_without_its_state_value(fake_senec, mqtt_port):
    plan = build_plan({"battery/safe_charge": "off"})
    config = make_config(fake_senec, mqtt_port, commands_enabled=True)
    async with collector(mqtt_port) as (seen, _):
        async with running_gateway(config, plan):
            await wait_for(lambda: seen.get("senec/status") == "online")
    assert SELECT_TOPIC not in seen


async def test_command_result_is_reported_without_waiting_for_a_poll(fake_senec, mqtt_port):
    config = dataclasses.replace(make_config(fake_senec, mqtt_port, commands_enabled=True), poll_interval=60)
    async with collector(mqtt_port) as (seen, client):
        async with running_gateway(config):
            await wait_for(lambda: seen.get("senec/status") == "online")
            assert seen["senec/battery/safe_charge"] == "0"
            await asyncio.sleep(0.1)
            await client.publish("senec/command/battery_mode", payload="charge", qos=1)
            await wait_for(lambda: seen.get("senec/battery/safe_charge") == "1")


async def test_invalid_command_is_ignored(fake_senec, mqtt_port):
    async with collector(mqtt_port) as (seen, client):
        async with running_gateway(make_config(fake_senec, mqtt_port, commands_enabled=True)):
            await wait_for(lambda: seen.get("senec/status") == "online")
            await asyncio.sleep(0.1)
            await client.publish("senec/command/battery_mode", payload="explode", qos=1)
            await asyncio.sleep(0.2)
    assert fake_senec.writes() == []


async def test_retained_command_is_not_applied_on_start(fake_senec, mqtt_port):
    async with aiomqtt.Client("127.0.0.1", mqtt_port, identifier="test-retain") as client:
        await client.publish("senec/command/battery_mode", payload="charge", qos=1, retain=True)
        await asyncio.sleep(0.1)

    async with collector(mqtt_port, "senec/status") as (seen, _):
        async with running_gateway(make_config(fake_senec, mqtt_port, commands_enabled=True)):
            await wait_for(lambda: seen.get("senec/status") == "online")
            await asyncio.sleep(0.2)
    assert fake_senec.writes() == []


async def test_retained_command_really_arrives_at_the_gateway(fake_senec, mqtt_port, caplog):
    """Guards the test above: the command must be delivered and then rejected."""
    async with aiomqtt.Client("127.0.0.1", mqtt_port, identifier="test-retain2") as client:
        await client.publish("senec/command/battery_mode", payload="charge", qos=1, retain=True)
        await asyncio.sleep(0.1)

    with caplog.at_level(logging.WARNING, logger="senec_mqtt_gateway.gateway"):
        async with running_gateway(make_config(fake_senec, mqtt_port, commands_enabled=True)):
            await wait_for(lambda: "Ignoring retained command" in caplog.text)


async def test_reconnects_after_broker_restart(fake_senec, broker, monkeypatch):
    monkeypatch.setattr("senec_mqtt_gateway.gateway._RECONNECT_DELAY", 0.05)
    port = broker["port"]
    async with running_gateway(make_config(fake_senec, port)):
        async with collector(port, "senec/status") as (first, _):
            await wait_for(lambda: first.get("senec/status") == "online")

        await broker["broker"].shutdown()
        broker["broker"] = None
        await asyncio.sleep(0.2)
        broker["broker"] = await start_broker(port)

        async def seen_again():
            # If the broker goes away during a QoS 1 publish, the gateway only
            # notices when aiomqtt's 10 s publish timeout expires.
            async with collector(port, "senec/pv/power") as (seen, _):
                await wait_for(lambda: seen.get("senec/pv/power") == "3000.0", timeout=15)

        await seen_again()


async def test_stop_works_while_broker_is_gone(fake_senec, broker, monkeypatch):
    monkeypatch.setattr("senec_mqtt_gateway.gateway._RECONNECT_DELAY", 0.05)
    port = broker["port"]
    async with create_session(timeout=1) as session:
        config = make_config(fake_senec, port)
        gateway = Gateway(config, SenecClient(session, config.senec_url, retry_delay=0))
        task = asyncio.create_task(gateway.run())
        await asyncio.sleep(0.3)
        await broker["broker"].shutdown()
        broker["broker"] = None
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=4)


def test_config_defaults_are_safe():
    config = Config.from_env({"SENEC_HOST": "192.0.2.10"})
    assert config.senec_url == "https://192.0.2.10"
    assert config.commands_enabled is False
    assert config.poll_interval == 10.0


@pytest.mark.parametrize(
    ("poll", "detail", "every"),
    [
        ("10", "60", 6),
        ("30", "60", 2),
        ("10", "45", 5),
        ("10", "5", 1),
        ("10", "0", 0),
        # 6.9 / 2.3 is 3.0000000000000004 in floating point
        ("2.3", "6.9", 3),
    ],
)
def test_details_run_on_a_multiple_of_the_poll(poll, detail, every):
    config = Config.from_env({"SENEC_HOST": "h", "POLL_INTERVAL": poll, "DETAIL_INTERVAL": detail})
    assert config.detail_every == every


def test_config_parses_mqtt_uri():
    config = Config.from_env({"SENEC_HOST": "h", "MQTT_URI": "mqtts://broker.local"})
    assert (config.mqtt_host, config.mqtt_port, config.mqtt_tls) == ("broker.local", 8883, True)


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"SENEC_HOST": "h", "MQTT_URI": "http://x"},
        {"SENEC_HOST": "h", "POLL_INTERVAL": "abc"},
        {"SENEC_HOST": "h", "POLL_INTERVAL": "0.5"},
        {"SENEC_HOST": "h", "DETAIL_INTERVAL": "abc"},
        {"SENEC_HOST": "h", "DETAIL_INTERVAL": "-1"},
    ],
)
def test_config_rejects_invalid(env):
    with pytest.raises(ConfigError):
        Config.from_env(env)
