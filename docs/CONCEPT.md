# Concept

Why this gateway exists and the decisions behind it. Topics and configuration are in the [README](../README.md).

## Goal

The local `lala.cgi` endpoint of a SENEC copes badly with parallel access. If evcc, Home Assistant and other systems each poll it themselves, the load adds up. The evcc template `senec-home` also only reads total power values, so voltage, current and power per phase, the frequency and all battery details never reach it.

The gateway is therefore the only client of the storage system, and everyone else reads from MQTT. The model is the [SAIC MQTT gateway](https://github.com/SAIC-iSmart-API/saic-python-mqtt-gateway), which evcc integrates through the slim `mg2mqtt` template.

**Not supported**

- SENEC.Home P4, where local access does not work.
- A "hold battery" mode. The SENEC only knows normal operation and forced charging.

## Possible extensions

Not part of the prototype, but deliberately left open:

- **Wallbox readings and control.** The `WALLBOX` section of `lala.cgi` is readable (state, charging power, current per phase, limits), and the integration this project derives from also writes to it. Readings would fit the existing detail read. Control needs its own commands and the same safeguards as the battery commands.
- **mein-senec.de or app API.** Some data is not available locally, for example lifetime energy totals: the `STATISTIC` section is empty on current firmware. A cloud source would need login handling and has to stay optional, so that the local path keeps working without an account.
- **Further `lala.cgi` sections.** `AMPACE` (vendor-specific battery alarm states, cell temperatures in 0.1 °C), `SOCKETS` and `PWR_UNIT` are readable but not published yet.

## Architecture

```text
SENEC.Home V2/V3
      |  HTTPS POST /lala.cgi (the gateway only)
      v
senec-mqtt-gateway  --retained-->  MQTT broker  -->  evcc (type: custom, source: mqtt)
      ^                                 |       -->  Home Assistant (MQTT discovery)
      |                                 |
      +------ <prefix>/command/... <----+  (commands, not retained)
```

## Decisions

| Topic | Decision | Reason |
|---|---|---|
| MQTT schema | Own schema | There is no market-standard schema that evcc understands |
| Signs | Same as evcc | Saves converting in every evcc configuration |
| Publishing | Every cycle, even unchanged values | evcc treats missing messages as an error after `timeout` |
| Access | One lock for reads and writes | Requests to the SENEC never overlap |
| Read rates | Main values every 10 s, details on every 6th poll | Keeps the load low; evcc only controls with the main values. Counting polls keeps the rate exact despite varying response times |
| Split | Recommended split in code, optional TOML file on top | Works without any file; TOML needs no extra dependency; only differences have to be written down |
| Requests | Below 600 bytes, with a pause between detail requests | The web UI splits its requests at that size |
| Fields | Explicit field lists, not whole sections | Reads only what is published and never touches identity data |
| Absent hardware | Modules, strings and meter 2 are hidden | The device reports zeros for hardware it does not have |
| Detail failure | Status stays `online` | The values evcc needs are not affected |
| Commands | Off by default, not retained | A first run cannot change the battery; old commands have no effect |
| Start | Do not set a battery mode | Would abort a running charge after a restart |
| Code | Taken selectively from `ha-senec-v3` | `pysenec_ha` depends on Home Assistant and is mostly cloud code |

## Rolling out on an existing system

1. Run `--once` and compare the values with the SENEC web UI.
2. Run the gateway in parallel with `POLL_INTERVAL=30` and no commands. Only during this phase do two clients poll.
3. In evcc, switch PV, then grid, then battery to MQTT. After that set `POLL_INTERVAL=10`.
4. Set `COMMANDS_ENABLED=true` and test `charge` and `normal`.

Way back: set the evcc meters to `senec-home` again and stop the gateway.

## Open points

- [ ] Test on real V2 and V3 devices, including TLS with old cipher suites.
- [ ] Is the initial `FACTORY` request needed on every device? The evcc template works without it.
- [ ] Are 10 s enough for evcc's control loop?
- [ ] Confirm the PV sign during daylight. The battery sign is confirmed on a real device during forced charging.
- [ ] Do older firmware versions cope with the detail requests, and do they deliver the same fields?
- [ ] Fall back to `normal` if evcc fails: evcc's `watchdog` plugin might do this, but may bypass the mode detection of `switch`.
- [ ] Add energy counters so that evcc gets `energy`.
- [ ] Submit an evcc template `senec-mqtt` as a PR.
