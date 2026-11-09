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

The payload is just the value. All values are retained. Signs match evcc: grid import and battery discharge are positive. Phase currents stay as the SENEC reports them, always positive; the direction is in the phase power and in the power factor the gateway calculates.

**[docs/FIELDS.md](docs/FIELDS.md) describes every value**: its unit, how often it is read, where it comes from on the SENEC, and anything the gateway does differently from the device.

In short:

- **Every `POLL_INTERVAL`:** grid power, current, voltage and calculated power factor per phase, grid frequency, PV, house and battery power, battery voltage, current and state of charge, forced charging, system state. evcc works with these.
- **Every `DETAIL_INTERVAL`:** PV strings, battery temperatures and limits, values per battery module and per cell, second meter, system temperatures, hours of operation.
- Battery modules, PV strings and the second meter only appear if the device reports them as present. `--once` lists everything your device delivers.
- `senec/status` is `online` or `offline`.

### Choosing what is read and how often

[docs/FIELDS.md](docs/FIELDS.md) shows the recommended split, and the gateway uses it unless you say otherwise. To change it, copy [metrics.example.toml](metrics.example.toml), list only what should differ, and set `METRICS_CONFIG` to its path:

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
    powers: # evcc takes the direction of the currents from these
      - { source: mqtt, topic: senec/grid/power/l1, timeout: 1m }
      - { source: mqtt, topic: senec/grid/power/l2, timeout: 1m }
      - { source: mqtt, topic: senec/grid/power/l3, timeout: 1m }

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
