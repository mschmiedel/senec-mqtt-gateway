"""A simulated SENEC and a real in-process MQTT broker for the tests."""

from __future__ import annotations

import asyncio
import json
import socket
import struct

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer
from amqtt.broker import Broker


def fl(value: float) -> str:
    return "fl_" + struct.pack(">f", value).hex().upper()


class FakeSenec:
    """Answers lala.cgi like the device: requested fields are filled in, writes are stored."""

    def __init__(self) -> None:
        self.values = {
            "ENERGY": {
                "STAT_STATE": "u8_0E",
                "GUI_GRID_POW": fl(-1200.5),
                "GUI_INVERTER_POWER": fl(3000.0),
                "GUI_HOUSE_POW": fl(800.0),
                "GUI_BAT_DATA_POWER": fl(1000.0),
                "GUI_BAT_DATA_VOLTAGE": fl(52.5),
                "GUI_BAT_DATA_CURRENT": fl(19.0),
                "GUI_BAT_DATA_FUEL_CHARGE": fl(64.25),
                "SAFE_CHARGE_RUNNING": "u8_00",
            },
            "PM1OBJ1": {
                "FREQ": fl(50.012),
                "U_AC": [fl(230.1), fl(231.2), fl(232.3)],
                "I_AC": [fl(1.5), fl(-2.5), fl(3.5)],
                "P_AC": [fl(100.0), fl(-200.0), fl(300.0)],
            },
            "PM1OBJ2": {"ENABLED": "u8_00", "FREQ": fl(0.0), "P_TOTAL": fl(0.0)},
            "PV1": {
                "MPP_AVAIL": "u8_02",
                "MPP_VOL": [fl(250.0), fl(240.0), fl(0.0)],
                "MPP_CUR": [fl(6.0), fl(5.0), fl(0.0)],
                "MPP_POWER": [fl(1500.0), fl(1200.0), fl(0.0)],
            },
            "TEMPMEASURE": {"BATTERY_TEMP": fl(32.25), "CASE_TEMP": fl(28.5), "MCU_TEMP": fl(49.0)},
            "BMS": {
                "MODULES_CONFIGURED": "u8_02",
                "SOC": ["u1_0054", "u1_0053"],
                "CURRENT": [fl(23.0), fl(22.5)],
                "MAX_TEMP": "i1_0142",
                "CELL_TEMPERATURES_MODULE_A": ["u8_1B", "u8_1E", "u8_20"],
                "CELL_TEMPERATURES_MODULE_C": ["u8_00", "u8_00", "u8_00"],
                "CELL_VOLTAGES_MODULE_A": ["u1_0D3E", "u1_0D3B"],
                "CELL_VOLTAGES_MODULE_C": ["u1_0000", "u1_0000"],
            },
            "FACTORY": {"SYS_TYPE": "u8_11", "COUNTRY": "u8_00", "DEVICE_ID": "st_SECRET"},
            "WIZARD": {"APPLICATION_VERSION": "u8_05"},
            "SYS_UPDATE": {"NPU_IMAGE_VERSION": "u1_096B"},
        }
        self.requests: list[dict] = []
        self.headers: list[dict] = []
        self.fail_with: int | None = None

    async def handle(self, request: web.Request) -> web.Response:
        if self.fail_with:
            return web.Response(status=self.fail_with)
        form = json.loads(await request.text())
        self.requests.append(form)
        self.headers.append(dict(request.headers))

        energy = form.get("ENERGY", {})
        if energy.get("SAFE_CHARGE_FORCE") == "u8_01":
            self.values["ENERGY"]["SAFE_CHARGE_RUNNING"] = "u8_01"
        if energy.get("SAFE_CHARGE_PROHIBIT") == "u8_01":
            self.values["ENERGY"]["SAFE_CHARGE_RUNNING"] = "u8_00"

        answer = {
            section: {name: self.values.get(section, {}).get(name, "VARIABLE_NOT_FOUND") for name in fields}
            for section, fields in form.items()
        }
        response = web.Response(text=json.dumps(answer), content_type="text/html")
        if "FACTORY" in form:
            response.set_cookie("session", "abc")
        return response

    def writes(self) -> list[dict]:
        return [
            form["ENERGY"]
            for form in self.requests
            if any(value == "u8_01" for value in form.get("ENERGY", {}).values())
        ]


@pytest_asyncio.fixture
async def fake_senec():
    fake = FakeSenec()
    app = web.Application()
    app.router.add_post("/lala.cgi", fake.handle)
    server = TestServer(app)
    await server.start_server()
    fake.url = str(server.make_url("")).rstrip("/")
    yield fake
    await server.close()


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


async def start_broker(port: int) -> Broker:
    broker = Broker(
        {
            "listeners": {"default": {"type": "tcp", "bind": f"127.0.0.1:{port}"}},
            "plugins": {
                "amqtt.plugins.authentication.AnonymousAuthPlugin": {"allow_anonymous": True},
            },
        }
    )
    await broker.start()
    await asyncio.sleep(0.05)
    return broker


@pytest_asyncio.fixture
async def broker():
    port = _free_port()
    instance = await start_broker(port)
    holder = {"broker": instance, "port": port}
    yield holder
    if holder["broker"] is not None:
        await holder["broker"].shutdown()


@pytest_asyncio.fixture
async def mqtt_port(broker):
    return broker["port"]
