# Fields

Every value the gateway publishes, where it comes from on the SENEC, and anything the gateway does differently from the device.

This file is generated from `src/senec_mqtt_gateway/schema.py` and `field_docs.py`. Do not edit it by hand, run:

```bash
uv run python -m senec_mqtt_gateway.field_docs > docs/FIELDS.md
```

## Conventions

- Topics are shown without the prefix (`senec` by default). The payload is just the value, and all values are retained.
- `senec/status` is `online` or `offline`. It says whether the gateway is running and can read the SENEC.
- `poll` values are read every `POLL_INTERVAL` and republished even when unchanged, because evcc treats silence as a timeout. `detail` values are read every `DETAIL_INTERVAL`. Both can be changed, see the README.
- Placeholders: `<m>` is a battery module (`a` to `d`), `<n>` a cell number, `<s>` a PV string (`1` to `3`), `<p>` a phase (`l1` to `l3`).
- `[ ]` after a source marks one element of an array on the device.

## Where the gateway differs from the device

| What | Device | Gateway | Why |
|---|---|---|---|
| Battery power and current | Positive while charging | Positive while discharging | Matches evcc, which also treats grid import as positive |
| Phase power factor | Not reported | Calculated by the gateway as P / (U · I) | The device reports phase currents as positive RMS values, so they show no direction. See the note in the table |
| Cell voltages | Millivolts | Volts | Consistent with the other voltages |
| Some temperatures and SOH | Tenths of a degree or percent | Degrees and percent | Consistent with the other values |
| Module discharge current limit | Negative | Positive | Consistent with the system-wide limit, which the device reports as positive |
| Rounding | Full float precision | Rounded per value | Avoids meaningless digits |
| Negative zero | Reported as `-0.0` | `0.0` | |
| Hardware not installed | Zeros in arrays | Value is not published | Avoids empty modules, strings and meters |

## Values

| Topic | Unit | Rate | Source | Meaning and handling |
|---|---|---|---|---|
| `grid/power` | W | poll | `ENERGY.GUI_GRID_POW` | Grid power. Positive when importing, negative when exporting. |
| `grid/power/l<p>` | W | poll | `PM1OBJ1.P_AC[ ]` | Grid power per phase. Same sign as `grid/power`. The three phases add up to it. |
| `grid/current/l<p>` | A | poll | `PM1OBJ1.I_AC[ ]` | Grid current per phase. Always positive, as reported by the device: an RMS value has no direction. The direction is in the power of the same phase. |
| `grid/voltage/l<p>` | V | poll | `PM1OBJ1.U_AC[ ]` | Grid voltage per phase. |
| `grid/power_factor/l<p>` | – | poll | Calculated from `PM1OBJ1.P_AC[ ]`, `U_AC[ ]`, `I_AC[ ]` | Grid power factor per phase. Not reported by the device, calculated by the gateway so that direction and load type are visible without combining three topics. Negative while exporting. Includes harmonics, so it is not exactly cos φ. Limited to ±1 and not published while voltage or current is zero. Unreliable at very small currents. |
| `grid/frequency` | Hz | poll | `PM1OBJ1.FREQ` | Grid frequency. |
| `pv/power` | W | poll | `ENERGY.GUI_INVERTER_POWER` | PV power. The sign while generating has not been confirmed on a real device yet. |
| `house/power` | W | poll | `ENERGY.GUI_HOUSE_POW` | House power. |
| `battery/power` | W | poll | `ENERGY.GUI_BAT_DATA_POWER` | Battery power. Positive when discharging, negative when charging. Sign inverted from the device value. |
| `battery/voltage` | V | poll | `ENERGY.GUI_BAT_DATA_VOLTAGE` | Battery voltage. |
| `battery/current` | A | poll | `ENERGY.GUI_BAT_DATA_CURRENT` | Battery current. Positive when discharging, negative when charging. Sign inverted from the device value. |
| `battery/soc` | % | poll | `ENERGY.GUI_BAT_DATA_FUEL_CHARGE` | Battery state of charge. |
| `battery/safe_charge` | 1/0 | poll | `ENERGY.SAFE_CHARGE_RUNNING` | Battery forced charging. 1 while the device is force-charging the battery, which is the mode `charge`. |
| `system/state` | – | poll | `ENERGY.STAT_STATE` | System state. Numeric state code of the device, not translated. |
| `grid/limited` | 1/0 | detail | `ENERGY.STAT_LIMITED_NET_SKEW` | Grid feed-in limited. |
| `grid/zero_export` | 1/0 | detail | `ENERGY.ZERO_EXPORT` | Zero export. Disabled by default in Home Assistant. |
| `meter2/frequency` | Hz | detail | `PM1OBJ2.FREQ` | Meter 2 frequency. Only if `PM1OBJ2.ENABLED` is at least 1. |
| `meter2/power` | W | detail | `PM1OBJ2.P_TOTAL` | Meter 2 power. Only if `PM1OBJ2.ENABLED` is at least 1. |
| `meter2/power/l<p>` | W | detail | `PM1OBJ2.P_AC[ ]` | Meter 2 power per phase. Only if `PM1OBJ2.ENABLED` is at least 1. |
| `meter2/current/l<p>` | A | detail | `PM1OBJ2.I_AC[ ]` | Meter 2 current per phase. Always positive, as reported by the device: an RMS value has no direction. The direction is in the power of the same phase. Only if `PM1OBJ2.ENABLED` is at least 1. |
| `meter2/voltage/l<p>` | V | detail | `PM1OBJ2.U_AC[ ]` | Meter 2 voltage per phase. Only if `PM1OBJ2.ENABLED` is at least 1. |
| `meter2/power_factor/l<p>` | – | detail | Calculated from `PM1OBJ2.P_AC[ ]`, `U_AC[ ]`, `I_AC[ ]` | Meter 2 power factor per phase. Not reported by the device, calculated by the gateway so that direction and load type are visible without combining three topics. Negative while exporting. Includes harmonics, so it is not exactly cos φ. Limited to ±1 and not published while voltage or current is zero. Unreliable at very small currents. Only if `PM1OBJ2.ENABLED` is at least 1. |
| `pv/power_ratio` | % | detail | `PV1.POWER_RATIO` | PV power limit. Power limit of the inverter, 100 while unlimited. |
| `pv/power_ratio/l<p>` | % | detail | `PV1.POWER_RATIO_L<p>` | PV power limit per phase. Disabled by default in Home Assistant. |
| `pv/string/<s>/voltage` | V | detail | `PV1.MPP_VOL[ ]` | PV string S voltage. Only for items present according to `PV1.MPP_AVAIL`. |
| `pv/string/<s>/current` | A | detail | `PV1.MPP_CUR[ ]` | PV string S current. Only for items present according to `PV1.MPP_AVAIL`. |
| `pv/string/<s>/power` | W | detail | `PV1.MPP_POWER[ ]` | PV string S power. Only for items present according to `PV1.MPP_AVAIL`. |
| `battery/temperature` | °C | detail | `TEMPMEASURE.BATTERY_TEMP` | Battery temperature. |
| `battery/temperature/min` | °C | detail | `BMS.MIN_TEMP` | Battery minimum temperature. Device value × 0.1. |
| `battery/temperature/max` | °C | detail | `BMS.MAX_TEMP` | Battery maximum temperature. Device value × 0.1. |
| `battery/soh` | % | detail | `BMS.SYSTEM_SOH` | Battery state of health. Device value × 0.1. |
| `battery/modules` | – | detail | `BMS.MODULES_CONFIGURED` | Battery modules. Number of configured modules. Decides which module values are published. Disabled by default in Home Assistant. |
| `battery/charge_limit` | A | detail | `BMS.SYSTEM_CHARGE_CURRENT_LIMIT` | Battery charge current limit. Disabled by default in Home Assistant. |
| `battery/discharge_limit` | A | detail | `BMS.SYSTEM_DISCHARGE_CURRENT_LIMIT` | Battery discharge current limit. Disabled by default in Home Assistant. |
| `battery/spare_capacity` | % | detail | `BAT1.SPARE_CAPACITY` | Battery spare capacity. |
| `battery/charging` | 1/0 | detail | `ENERGY.GUI_CHARGING_INFO` | Battery charging. |
| `battery/boosting` | 1/0 | detail | `ENERGY.GUI_BOOSTING_INFO` | Battery boosting. Disabled by default in Home Assistant. |
| `battery/storage_mode` | 1/0 | detail | `ENERGY.LI_STORAGE_MODE_RUNNING` | Battery storage mode. Disabled by default in Home Assistant. |
| `battery/derating` | – | detail | `BMS.DERATING` | Battery derating. Disabled by default in Home Assistant. |
| `battery/bms_status` | – | detail | `BMS.BMS_STATUS` | BMS status. Raw device code. Disabled by default in Home Assistant. |
| `battery/bms_error` | – | detail | `BMS.ERROR` | BMS error. Raw device code. Disabled by default in Home Assistant. |
| `battery/dc/voltage` | V | detail | `BAT1OBJ1.U_DC` | Battery DC voltage. Measured separately from `battery/voltage`, so the two differ slightly. Disabled by default in Home Assistant. |
| `battery/dc/current` | A | detail | `BAT1OBJ1.I_DC` | Battery DC current. Measured separately from `battery/current`, so the two differ slightly. Sign inverted from the device value. Disabled by default in Home Assistant. |
| `bat1/temperature/1` | °C | detail | `BAT1OBJ1.TEMP1` | BAT1 temperature 1. What this sensor measures is not documented. Disabled by default in Home Assistant. |
| `bat1/temperature/2` | °C | detail | `BAT1OBJ1.TEMP2` | BAT1 temperature 2. What this sensor measures is not documented. Disabled by default in Home Assistant. |
| `battery/module/<m>/soc` | % | detail | `BMS.SOC[ ]` | Battery module M state of charge. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/soh` | % | detail | `BMS.SOH[ ]` | Battery module M state of health. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/cycles` | – | detail | `BMS.CYCLES[ ]` | Battery module M cycles. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/voltage` | V | detail | `BMS.VOLTAGE[ ]` | Battery module M voltage. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/current` | A | detail | `BMS.CURRENT[ ]` | Battery module M current. Sign inverted from the device value. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/temperature/min` | °C | detail | `BMS.TEMP_MIN[ ]` | Battery module M minimum temperature. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/temperature/max` | °C | detail | `BMS.TEMP_MAX[ ]` | Battery module M maximum temperature. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/cell_voltage/min` | V | detail | `BMS.MIN_CELL_VOLTAGE[ ]` | Battery module M minimum cell voltage. Device value × 0.001. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/cell_voltage/max` | V | detail | `BMS.MAX_CELL_VOLTAGE[ ]` | Battery module M maximum cell voltage. Device value × 0.001. Only for items present according to `BMS.MODULES_CONFIGURED`. |
| `battery/module/<m>/charge_limit` | A | detail | `BMS.CHARGE_CURRENT_LIMIT[ ]` | Battery module M charge current limit. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `battery/module/<m>/discharge_limit` | A | detail | `BMS.DISCHARGE_CURRENT_LIMIT[ ]` | Battery module M discharge current limit. Sign inverted from the device value. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `battery/module/<m>/status` | – | detail | `BMS.STATUS[ ]` | Battery module M status. Raw device code. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `battery/module/<m>/balancing` | – | detail | `BMS.CELL_BALANCE_STATUS[ ]` | Battery module M cell balancing status. Raw device code. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `battery/module/<m>/charged_counter` | – | detail | `BMS.CHARGED_ENERGY[ ]` | Battery module M charged energy counter. Raw counter. Its unit is not documented, so none is claimed. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `battery/module/<m>/discharged_counter` | – | detail | `BMS.DISCHARGED_ENERGY[ ]` | Battery module M discharged energy counter. Raw counter. Its unit is not documented, so none is claimed. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `battery/module/<m>/cell/<n>/temperature` | °C | detail | `BMS.CELL_TEMPERATURES_MODULE_<M>[ ]` | Battery module M cell N temperature. Up to 6 sensors per module, as far as the device reports them. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `battery/module/<m>/cell/<n>/voltage` | V | detail | `BMS.CELL_VOLTAGES_MODULE_<M>[ ]` | Battery module M cell N voltage. Up to 16 cells per module, as far as the device reports them. Device value × 0.001. Only for items present according to `BMS.MODULES_CONFIGURED`. Disabled by default in Home Assistant. |
| `system/temperature/case` | °C | detail | `TEMPMEASURE.CASE_TEMP` | Case temperature. |
| `system/temperature/mcu` | °C | detail | `TEMPMEASURE.MCU_TEMP` | MCU temperature. |
| `system/hours_of_operation` | h | detail | `ENERGY.STAT_HOURS_OF_OPERATION` | Hours of operation. |

## Not published

- Identity data: serial numbers, device ids and other text values.
- Configuration and commissioning: `GRIDCONFIG`, `BMS_PARA`, wizard and self-test fields, and the charge test fields in `ENERGY`.
- Readable, but not published yet: `WALLBOX`, `AMPACE`, `SOCKETS`, `PWR_UNIT`, `STECA`. See [possible extensions](CONCEPT.md#possible-extensions).
- `STATISTIC` is empty on current firmware.
