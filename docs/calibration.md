# Calibration Log

Per-node, per-assignment record of the on-node `kv = adc_mv * gain + offset`
constant, derived against a handheld fence tester. See
[firmware/README.md#calibration](../firmware/README.md#calibration) for the
workflow this log documents.

**Keyed on `(node_id, location_id, date)`** — the constant depends on both
the board's ADC and the divider chain it's wired to, so a board swap *and*
a relocation each invalidate it. Record the raw points as well as the
derived constants, and treat readings as provisional until re-verified.

**This is not the backend's authoritative calibration.** The dashboard
recomputes `kv` from raw `adc_mv` at query time via its own versioned
`calibrations` table (see `docs/dashboard-plan.md#calibration`) — that's
what actually drives the displayed voltage and history. The constant logged
here only controls this specific node's on-board fault-threshold check
(`LOW_KV_THRESHOLD`/`DOWN_KV_THRESHOLD` in `config.h`, added in firmware
Phase 2), so it's fine for it to be coarser than the backend's fit.

**`temp_c` is not logged yet.** Enclosure temperature compensation (Vf
drift is ~−2 mV/°C, worth ~7% of the 5 kV alert threshold across a seasonal
swing) is deferred until hardware settles on a temperature sensor — see
`hardware/pcb-design-plan.md`'s "Temperature sensor footprint" note. Until
then, treat every entry below as calibrated near whatever ambient
temperature the session was run at.

## Log

| Date | `node_id` | `location_id` | Points (adc_mv → handheld kV) | Fitted gain (kv/mV) | Fitted offset (kV) | Notes |
|---|---|---|---|---|---|---|
| _(none yet — no hardware built)_ | | | | | | |

## Workflow

1. At the fence, with the handheld tester in hand, hold `PIN_CALIB_MODE`
   (the devkit's BOOT button by default) LOW and power on/reset the node.
2. It streams `adc_mv`/`kv` readings once a second to serial and to
   `fence/<node_id>/calib` (unretained). Record 3–5 `(adc_mv, handheld_kv)`
   pairs spread across the fence's real operating range (5–10 kV) — vary the
   charger's input power setting or wait for natural voltage variation
   rather than trying to force artificial points.
3. Release the pin; the node reboots into normal operation.
4. Run the fit tool:

   ```sh
   python3 firmware/tools/fit_calibration.py 1872:6.93 2010:7.40 1350:5.10 --node-id <node_id>
   ```

   It prints the fitted gain/offset, residuals (so you can sanity-check
   linearity), and the `mosquitto_pub` command to push the result.
5. Run the printed `mosquitto_pub` command. The node picks it up on its next
   report wake (up to `REPORT_INTERVAL_S` later, or immediately if it's
   already mid-report), stores it in NVS, and uses it from then on.
6. Add a row to the [Log](#log) table above.
