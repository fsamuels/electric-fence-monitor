# Current Status

_Last updated: 2026-08-06, after Firmware Phase 3 (calibration mode)._

## Where things stand

- **Hardware**: design phase only. No physical unit has been built; Phases 0–6 in [hardware-plan.md](hardware-plan.md) are all still open. The custom PCB ([hardware/logic-power-board-schematic.md](../hardware/logic-power-board-schematic.md)) and the hand-wired HV sensing chain ([hardware/voltage-divider-schematic.md](../hardware/voltage-divider-schematic.md)) are designed on paper but unvalidated on a breadboard.
- **Firmware**: covers software-plan Phases 1–3. Phase 2's split sample/report duty cycle (`SAMPLE_INTERVAL_S`/`REPORT_INTERVAL_S`, RTC-held fault-threshold edge-triggering) and Phase 3's calibration mode (held-pin trigger, NVS-backed gain/offset updatable over a retained MQTT command, offline multi-point fit tool) are both done — see [firmware/README.md](../firmware/README.md) and `firmware/src/main.cpp`. **`temp_c` logging (part of Phase 3) is blocked on hardware**: no temperature sensor exists yet, still an open PCB-layout decision. Hardening/OTA (Phase 4) is not implemented.
- **Dashboard**: the most mature part of the project. Built entirely ahead of hardware against mock data, using the firmware's real MQTT contract. **Phases D0–D5 are complete**; D5.5 and D6 are not started. See [dashboard-plan.md](dashboard-plan.md) for full phase detail.

## Features completed (dashboard D0–D5)

- ✅ Six-service Docker Compose stack (Mosquitto, Postgres/TimescaleDB, migrate, FastAPI, ingest, mock-publisher) plus a React/TS frontend — seven containers total.
- ✅ Contract-first MQTT payload (`contract/fence-state.schema.json`), validated in CI on every push.
- ✅ Ingest with correct retained-message handling (no phantom readings on restart), node/location/assignment schema with non-overlapping validity windows, continuous aggregates + retention/compression policies.
- ✅ Mock publisher: 6 fault scenarios (normal, slow-decline, low-voltage, fence-down, node-silent, battery-drain), live + backfill modes, board-swap/relocation/uncalibrated history seeding.
- ✅ REST API: `/healthz` (3-way + ingest heartbeat), `/locations` (+ readings, bucketed), `/nodes` (+ per-node detail with full assignment/calibration history), assignment writes, `/fence-events` read/write. Status derivation is a pure, unit-tested function.
- ✅ Frontend: multi-location grid, per-location voltage chart with `fence_events` annotations, provisional-kV marking for uncalibrated readings, **unassigned-node inbox**, assignment dialog (covers both provisioning and board-swap/relocation), link-quality indicators (RSSI/Wi-Fi/failed-pub), "Log a change" affordance, node detail modal (assignment + calibration timeline), and polling-based live updates for both the location grid and each card's chart.
- ✅ CI: firmware lint, contract validation, backend tests, mock-publisher tests, frontend lint/typecheck/test, and an API-schema-drift check that fails the build if the generated TS client is out of sync with the backend's OpenAPI schema.

## Features in progress / not started

- ⬜ **Phase D5.5 — Minimal push alerting.** No `status_transitions` table, no scheduler, no notification channel (ntfy/Pushover), no dead-man's switch. This is the project's stated primary requirement and isn't built yet — see [Recommended next actions](#recommended-next-actions).
- ⬜ **Phase D6 — Real hardware cutover.** Firmware Phases 2–3 have landed; still blocked on hardware Phase 4/6. Nothing to do here until hardware exists.
- ⬜ **Firmware Phase 3's `temp_c` logging** — blocked on hardware (no temperature sensor exists; sensor choice is still an open PCB-layout decision).
- ⬜ **Firmware Phase 4 (OTA/watchdog/backoff/buffered readings)** — not started.
- ⬜ **Hardware Phases 0–6** — parts sourcing through field deployment, all open. Highest-risk item (peak detector RC tuning) hasn't been touched on a breadboard yet.

## Known issues / open technical concerns

- **`docs/software-plan.md`'s phase checkboxes are partially stale.** Firmware Phases 2 and 3 are now checked (except `temp_c`, correctly left open), but Phase 1 items are still unchecked despite the corresponding firmware code existing, and its Phase 5 (Backend Bring-Up) checkboxes don't reflect that D0–D5 are done. Treat `dashboard-plan.md` as the authoritative tracker for dashboard work; don't infer dashboard status from `software-plan.md`.
- **No security hardening yet**, by design but still a real gap against the project's "check remotely" goal: anonymous MQTT, no per-node broker ACLs/credentials, no API auth, no TLS, no VPN/remote-access path. Fine for the current trusted-LAN dev setup; a hard blocker before any off-property or public-internet use.
- **Deployment target undecided** (Raspberry Pi vs. cloud VM vs. NAS) — deliberately deferred; Docker Compose keeps all options open.
- **Mock-vs-real time gap is large (~60×)** and has to be re-validated at the real cadence (`--cadence realtime`) before cutover — chart ranges, `silent` timeouts, and consecutive-reading debounce were tuned primarily against fast mock data.

## Recent major changes

- **2026-08-06 — Firmware Phase 3 (calibration mode)**: held-pin (`PIN_CALIB_MODE`) calibration mode streaming fast readings to serial and an unretained `fence/<node_id>/calib` topic; NVS-backed on-node `gain`/`offset` (cold-start default from `config.h`, real value updatable via a retained `fence/<node_id>/calib/set` MQTT command checked on each report wake); `firmware/tools/fit_calibration.py` for an offline multi-point least-squares fit; new `docs/calibration.md` log. `temp_c` logging deliberately not implemented — no hardware temperature sensor exists yet. See `firmware/src/main.cpp` and [firmware/README.md#calibration](../firmware/README.md#calibration).
- **2026-08-05 — Firmware Phase 2 duty-cycle split**: replaced the single `SLEEP_INTERVAL_S` with `SAMPLE_INTERVAL_S`/`REPORT_INTERVAL_S`, RTC-held fault-status edge-triggering for immediate out-of-band reports, and `ts`/`seq`/`sample_interval_s`/`report_interval_s` in the payload (best-effort NTP for `ts`). Backend ingest already handled all four fields, so this needed no backend changes. See `firmware/src/main.cpp` and [dashboard-plan.md#reporting-cadence-and-alert-latency](dashboard-plan.md#reporting-cadence-and-alert-latency).
- **2026-08-05 — Dashboard Phase D5 (Multi-node & live updates)**: added `GET /nodes/{node_id}` (assignment + calibration history), unassigned-node inbox, assignment/reassignment dialog, link-quality indicators, "Log a change" fence-event form, node detail timeline modal, and live-refreshing per-card charts. See branch `feature/dashboard-d5-multi-node`.
- Prior: Phases D0 (scaffolding) → D1 (data contract/storage) → D2 (mock publisher) → D3 (API) → D4 (frontend MVP) landed sequentially; see git history and `dashboard-plan.md` for phase-by-phase detail.

## Recommended next actions

1. **Phase D5.5 — minimal push alerting.** This is the project's primary stated requirement and the one thing the custom-stack decision doesn't give away for free. Build the `status_transitions` table, an edge-triggered scheduler over `derive_status()`, one delivery channel, and the dead-man's switch before doing anything else dashboard-side.
2. **Start hardware Phase 0/1** (parts sourcing, breadboard sensing chain) in parallel — it's the long pole and has the highest technical risk (peak detector RC tuning), and nothing on the dashboard side is blocked by waiting on it.
3. Once D5.5 lands, consider syncing `software-plan.md`'s checkboxes to reality so it stops silently diverging from actual progress.
