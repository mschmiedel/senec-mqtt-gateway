# senec-mqtt-gateway

Local MQTT gateway for SENEC.Home V2 and V3. It reads the storage system through `lala.cgi`, publishes the values to MQTT and can optionally accept battery commands. That leaves **a single client talking to the SENEC**, while evcc, Home Assistant and everything else read from MQTT.

> **Status: prototype.** Reading has been tried on one real device (V3 LFP, NPU 2608). Continuous operation and battery commands have only been tested against a simulated SENEC so far.

SENEC.Home P4 is not supported.

## Quick start

Read once first, without MQTT and without changing anything:

```bash
SENEC_HOST=192.168.1.50 uv run senec-mqtt-gateway --once
```

Then run it as a service:

```bash
docker compose -f compose.example.yaml up -d
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SENEC_HOST` | – (required) | IP address or hostname of the SENEC |
| `SENEC_SCHEME` | `https` | `http` or `https` |
| `POLL_INTERVAL` | `10` | Seconds between reads of the main values, at least 2 |
| `DETAIL_INTERVAL` | `60` | Seconds between reads of the detail values, rounded up to a multiple of `POLL_INTERVAL`; `0` turns them off |
| `METRICS_CONFIG` | – | Optional file that changes which values are read how often, see [below](#choosing-what-is-read-and-how-often) |
| `MQTT_URI` | `mqtt://localhost:1883` | Broker, `mqtt://` or `mqtts://` |
| `MQTT_USER`, `MQTT_PASSWORD` | – | Broker credentials |
| `MQTT_TOPIC` | `senec` | Prefix of all topics |
| `HA_DISCOVERY_ENABLED` | `true` | Home Assistant MQTT discovery |
| `HA_DISCOVERY_PREFIX` | `homeassistant` | Prefix for discovery |
| `COMMANDS_ENABLED` | `false` | Accept battery commands |
| `LOG_LEVEL` | `INFO` | Log level |

## MQTT topics

The payload is just the value. All values are retained. Signs match evcc: grid import and battery discharge are positive.

### Main values, every `POLL_INTERVAL`

These are also republished when unchanged, because evcc treats silence as a timeout.

| Topic | Unit | Source |
|---|---|---|
| `senec/grid/power` | W | `ENERGY.GUI_GRID_POW` |
| `senec/grid/power/l1` … `l3` | W | `PM1OBJ1.P_AC` |
| `senec/grid/current/l1` … `l3` | A | `PM1OBJ1.I_AC` |
| `senec/grid/voltage/l1` … `l3` | V | `PM1OBJ1.U_AC` |
| `senec/grid/frequency` | Hz | `PM1OBJ1.FREQ` |
| `senec/pv/power` | W | `ENERGY.GUI_INVERTER_POWER` |
| `senec/house/power` | W | `ENERGY.GUI_HOUSE_POW` |
| `senec/battery/power` | W | `ENERGY.GUI_BAT_DATA_POWER` × −1 |
| `senec/battery/voltage` | V | `ENERGY.GUI_BAT_DATA_VOLTAGE` |
| `senec/battery/current` | A | `ENERGY.GUI_BAT_DATA_CURRENT` × −1 |
| `senec/battery/soc` | % | `ENERGY.GUI_BAT_DATA_FUEL_CHARGE` |
| `senec/battery/safe_charge` | 1/0 | `ENERGY.SAFE_CHARGE_RUNNING` |
| `senec/system/state` | number | `ENERGY.STAT_STATE` |
| `senec/status` | `online`/`offline` | Gateway and SENEC reachable |

### Detail values, every `DETAIL_INTERVAL`

Details are read in the same cycle as the main values, so both are published together on those cycles.

Battery modules (`a` … `d`), PV strings and the second meter only appear if the device reports them as present. `--once` lists everything your device delivers.

| Topic | Unit | Source |
|---|---|---|
| `senec/grid/limited`, `senec/grid/zero_export` | 1/0 | `ENERGY.STAT_LIMITED_NET_SKEW`, `ENERGY.ZERO_EXPORT` |
| `senec/meter2/{power,frequency}` | W, Hz | `PM1OBJ2` |
| `senec/meter2/{power,current,voltage}/l1` … `l3` | W, A, V | `PM1OBJ2` |
| `senec/pv/power_ratio`, `.../l1` … `l3` | % | `PV1.POWER_RATIO*` |
| `senec/pv/string/1` … `3/{voltage,current,power}` | V, A, W | `PV1.MPP_*` |
| `senec/battery/temperature` | °C | `TEMPMEASURE.BATTERY_TEMP` |
| `senec/battery/temperature/{min,max}` | °C | `BMS.MIN_TEMP`, `BMS.MAX_TEMP` |
| `senec/battery/soh` | % | `BMS.SYSTEM_SOH` |
| `senec/battery/{charge_limit,discharge_limit}` | A | `BMS.SYSTEM_*_CURRENT_LIMIT` |
| `senec/battery/spare_capacity` | % | `BAT1.SPARE_CAPACITY` |
| `senec/battery/{charging,boosting,storage_mode}` | 1/0 | `ENERGY` |
| `senec/battery/{modules,derating,bms_status,bms_error}` | number | `BMS` |
| `senec/battery/dc/{voltage,current}` | V, A | `BAT1OBJ1.U_DC`, `I_DC` × −1 |
| `senec/bat1/temperature/1`, `2` | °C | `BAT1OBJ1.TEMP1`, `TEMP2` (meaning undocumented) |
| `senec/battery/module/<m>/{soc,soh}` | % | `BMS.SOC`, `BMS.SOH` |
| `senec/battery/module/<m>/cycles` | count | `BMS.CYCLES` |
| `senec/battery/module/<m>/{voltage,current}` | V, A | `BMS.VOLTAGE`, `BMS.CURRENT` × −1 |
| `senec/battery/module/<m>/temperature/{min,max}` | °C | `BMS.TEMP_MIN`, `BMS.TEMP_MAX` |
| `senec/battery/module/<m>/cell_voltage/{min,max}` | V | `BMS.MIN_CELL_VOLTAGE`, `BMS.MAX_CELL_VOLTAGE` |
| `senec/battery/module/<m>/{charge_limit,discharge_limit}` | A | `BMS.*_CURRENT_LIMIT` |
| `senec/battery/module/<m>/{status,balancing}` | number | `BMS.STATUS`, `BMS.CELL_BALANCE_STATUS` |
| `senec/battery/module/<m>/{charged_counter,discharged_counter}` | raw | `BMS.*_ENERGY` (unit undocumented) |
| `senec/battery/module/<m>/cell/<n>/temperature` | °C | `BMS.CELL_TEMPERATURES_MODULE_*` |
| `senec/battery/module/<m>/cell/<n>/voltage` | V | `BMS.CELL_VOLTAGES_MODULE_*` |
| `senec/system/temperature/{case,mcu}` | °C | `TEMPMEASURE` |
| `senec/system/hours_of_operation` | h | `ENERGY.STAT_HOURS_OF_OPERATION` |

Not published: configuration and commissioning data (`GRIDCONFIG`, `BMS_PARA`, wizard and self-test fields), serial numbers and other text values. The `WALLBOX` section is not published yet, see [possible extensions](docs/CONCEPT.md#possible-extensions).

### Choosing what is read and how often

The two tables above are the recommended split, and the gateway uses it unless you say otherwise. To change it, copy [metrics.example.toml](metrics.example.toml), list only what should differ, and set `METRICS_CONFIG` to its path:

```toml
[rates]
"battery/temperature" = "poll"
"battery/module/*/cell/*" = "off"
"battery/module/a/cell/1/voltage" = "detail"
```

Each value is `poll`, `detail` or `off`. Keys are topics without the prefix, and `*` matches any characters, including `/`. The most exact rule wins, so a topic written out in full beats any wildcard. On a tie, the later rule wins.

- A rule that matches nothing, or an unknown rate, stops the gateway at start, so typos cannot go unnoticed.
- At least one value must stay on `poll`, because that read decides whether the SENEC counts as online.
- `off` values are no longer read, unless they share a request field with an enabled value. Their retained messages and Home Assistant entities are removed on start.
- The gateway warns at start if a value used by the evcc configuration below is no longer on `poll`, or if the poll values no longer fit into one request.

### Commands

`senec/command/battery_mode` with `normal` or `charge`. Commands must be sent **not retained**. Retained messages are rejected so that an old command cannot take effect again after a restart.

## evcc

Three custom meters replace the `senec-home` template:

```yaml
meters:
  - name: senec_grid
    type: custom
    power:
      source: mqtt
      topic: senec/grid/power
      timeout: 1m
    currents:
      - { source: mqtt, topic: senec/grid/current/l1, timeout: 1m }
      - { source: mqtt, topic: senec/grid/current/l2, timeout: 1m }
      - { source: mqtt, topic: senec/grid/current/l3, timeout: 1m }

  - name: senec_pv
    type: custom
    power:
      source: mqtt
      topic: senec/pv/power
      timeout: 1m

  - name: senec_battery
    type: custom
    power:
      source: mqtt
      topic: senec/battery/power
      timeout: 1m
    soc:
      source: mqtt
      topic: senec/battery/soc
      timeout: 1m
    capacity: 10 # kWh
    batterymode:
      source: switch
      switch:
        - case: 1 # normal
          set: { source: mqtt, topic: senec/command/battery_mode, payload: normal }
        - case: 3 # charge
          set: { source: mqtt, topic: senec/command/battery_mode, payload: charge }
```

`batterymode` needs `COMMANDS_ENABLED=true`. This configuration has not been tried on a real evcc yet.

## Home Assistant

With discovery, the sensors appear automatically under the device "SENEC.Home". Only values the device actually delivers are announced, so restart the gateway after adding a battery module.

Per-cell values and other rarely needed sensors are announced but disabled by default. Enable them in Home Assistant if you want them.

Where the meaning is unchanged, entity ids match those of the `ha-senec-v3` integration to make switching easier. Battery power and current have new ids because their sign differs.

## Behaviour on failures

- If the SENEC stops answering the main read, `senec/status` goes to `offline` and the gateway keeps trying at the normal rate.
- If only the detail read fails, the status stays `online`, because the values evcc depends on still arrive.
- If the gateway fails, the broker sets `senec/status` to `offline` through the last will.
- The gateway does **not** set a battery mode on start. The SENEC stays in whatever mode was set last.

## Development

```bash
uv sync
uv run pytest
```

The tests run against a simulated SENEC and a real in-process MQTT broker. Background is in [docs/CONCEPT.md](docs/CONCEPT.md).

## License and attribution

Apache-2.0, see [LICENSE](LICENSE) and [NOTICE](NOTICE). The decoding and request sequence are derived from [ha-senec-v3](https://github.com/marq24/ha-senec-v3) by Matthias Marquardt.

This project is not affiliated with SENEC GmbH.
