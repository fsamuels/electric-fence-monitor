# Roadmap

Prioritized by what unblocks the project's stated primary goal (remote
visibility + alerting) fastest, not by document order. See
[current-status.md](current-status.md) for what's already done and
[dashboard-plan.md](dashboard-plan.md) / [software-plan.md](software-plan.md) /
[hardware-plan.md](hardware-plan.md) for full phase detail behind each item.

## Short-term (next up)

- **Dashboard Phase D5.5 — Minimal push alerting** _(not started)_. `status_transitions` table, edge-triggered scheduler over the existing `derive_status()`, one delivery channel (ntfy or Pushover) with `low`/`down`/`silent` severity split, and an external dead-man's switch. This is the project's stated primary requirement and the one piece the custom-stack decision doesn't provide for free — see [dashboard-plan.md#phase-d55](dashboard-plan.md#phase-d55--minimal-push-alerting).
- **Hardware Phase 0 — Parts sourcing & bench setup** _(not started)_. Can run fully in parallel with D5.5; nothing dashboard-side blocks it.
- **Hardware Phase 1 — Breadboard sensing chain** _(not started)_. Highest technical risk in the whole project (peak detector RC tuning); start this as early as possible since it gates almost everything else hardware-side.

## Medium-term

- **Hardware Phase 2 — Peak detector RC tuning** (empirical, breadboard-based).
- **Hardware Phase 3 — Power system** (battery/solar budget, real energy-per-wake-cycle measurement — this is also what validates or invalidates the cadence recommendation in [dashboard-plan.md#reporting-cadence-and-alert-latency](dashboard-plan.md#reporting-cadence-and-alert-latency)).
- **Firmware Phase 3 — Calibration support** (calibration mode, multi-point fit, NVS-stored on-node constant, `temp_c` logging).
- **Firmware Phase 4 — Hardening & OTA** (OTA updates, watchdog/brown-out handling, Wi-Fi backoff, buffered readings across outages).
- **Hardware Phase 4 — Integrated prototype & calibration** (first end-to-end real node).
- **Dashboard Phase D6 — Real hardware cutover** _(blocked on hardware Phase 4/6 + firmware Phase 2)_. Not a config change — re-tune every time-based threshold against real ~10–15 min cadence, retire mock nodes, insert real calibration rows, bind the first real node to a location.

## Long-term

- **Hardware Phase 5 — Enclosure & weatherproofing.**
- **Hardware Phase 6 — Field deployment & horse-proofing.**
- **Software Phase 7 — Multi-node & fault localization**: onboard nodes 2–5 via config only, comparative multi-node chart view, revisit dashboard layout for a fleet instead of a single gauge.
- **Remote access**: Tailscale/WireGuard (or equivalent) once there's a real off-property access need — tracked in [dashboard-plan.md#security](dashboard-plan.md#security), intentionally deferred until then.
- **Full alert management**: acknowledgment, quiet hours, escalation chains, multiple delivery channels, the slow-decline trend tier — explicitly out of scope for the D5.5 minimal pass; revisit once the minimal version has been running against real alerts for a while.

## Technical debt

- **`docs/software-plan.md` phase checkboxes are stale** relative to actual firmware/dashboard progress — needs a sync pass once D5.5 lands, or an explicit note pointing readers at `dashboard-plan.md`/`firmware/README.md` as the current trackers.
- **No per-node MQTT credentials/broker ACLs** — anonymous access is fine on a trusted LAN today but is a named prerequisite before any wider deployment.
- **No API authentication or TLS** — same category as above.
- **`docker-compose.yml` now runs 7 services** (broker, db, migrate, api, ingest, mock-publisher, frontend); some plan text still says "six" from before `ingest` was split into its own container — cosmetic, worth fixing when touching that section.

## Nice-to-haves

- `/ws/live` WebSocket instead of polling — explicitly a stretch goal, not required; polling is adequate for the real 10–15 min firmware duty cycle.
- Dashboard-side surfacing of firmware version per node to spot a bad OTA rollout, once OTA (firmware Phase 4) exists.
- Seasonal `temp_c` drift characterization feeding back into calibration compensation, once enough real field data exists.
