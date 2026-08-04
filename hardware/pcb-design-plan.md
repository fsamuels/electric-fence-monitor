# Custom PCB — Design & Fab Plan

Evaluates whether/when a custom PCB makes sense for this project, what it should
cover, which tool to design it in, and how to get it fabricated. This is a
planning document, not a build phase gate by itself — it slots into the
[hardware development plan](hardware-plan.md) at Phase 4/6 (see
[Sequencing](#sequencing-why-not-now)).

---

## TL;DR

- **Don't design the PCB yet.** [Phase 1–2](hardware-plan.md#phase-1--breadboard-the-sensing-chain-highest-risk-first)
  (breadboard + empirical `Rbleed` tuning) haven't happened. A PCB locks in
  values that are explicitly still TBD.
- **Split the build into two physical pieces.** The 1 GΩ HV divider needs
  real air-gap spacing and stays hand-wired, exactly as the hardware plan
  already says. Everything downstream of the Zener clamp — peak detector
  interface, charge/power management, ESP32 — is a normal low-voltage 2-layer
  board and is what actually benefits from being a PCB.
- **Design in KiCad**, not Flux.ai. Free, no subscription, huge tutorial base,
  exports Gerbers/BOM/CPL directly in JLCPCB's expected format. EasyEDA
  (JLCPCB's own sister tool) is a reasonable lower-friction alternative if you
  want zero local install.
- **Fab at JLCPCB.** 2-layer prototype boards are a few dollars for a 5-board
  minimum order; hand-solder rather than using their SMT assembly service for
  this low-volume, connector-heavy board.

---

## Sequencing: why not now

The project is explicitly in the design phase — no hardware built, no
breadboard testing done. The hardware plan calls out `Rbleed` (the peak
detector's bleed resistor) as **empirical**: it has to be tuned on a
breadboard against real charger pulses (Phase 2), not calculated and trusted.
Any PCB designed before that tuning is done is a PCB designed around a guess.

PCB fab/ship is a real turnaround (days to two weeks depending on
shipping), unlike swapping a resistor on a breadboard. Sequence:

1. Phase 0–2 (parts, breadboard, RC tuning) on the bench, as already planned.
2. Once `Rbleed`, the Zener choices, and the firmware pinout are stable
   (end of Phase 2 / into Phase 4), design the PCB covered by this doc.
3. Fab a small run (5–10 boards) — enough for the prototype plus the
   2–5-node fleet the hardware plan already scopes for, with spares.

This also directly serves the hardware plan's own scaling goal: a finalized,
sourced BOM so nodes 2+ are "order and assemble," not re-engineered per unit.

---

## Split the board in two

The hardware plan already says the HV divider chain (10× 100 MΩ resistors,
up to 10 kV across the chain) needs real air-gap spacing and is "not suitable
for a tightly packed... layout." That constraint doesn't go away on a PCB —
creepage/clearance distance at 10 kV doesn't shrink because the substrate is
fiberglass instead of a breadboard, and a compact PCB layout is exactly the
kind of tightly-packed arrangement the plan already rules out.

So:

- **HV sensing chain — stays hand-wired, off-board.** Divider, `Rsense`,
  peak detector, both Zener clamps. See
  [voltage-divider-schematic.md](voltage-divider-schematic.md) — unchanged by
  this plan. Feeds the PCB through a single 2-pin terminal (`J1`) carrying the
  already-clamped, already-safe `FENCE_ADC_IN` signal (post second Zener,
  ≤3.6 V).
- **"Logic & Power" board — the actual PCB.** Charge management, buck/boost
  regulator, battery/solar terminals, ESP32 interface, battery-sense divider.
  All low voltage, all ordinary 2-layer PCB design. See
  [logic-power-board-schematic.md](logic-power-board-schematic.md) for the
  full schematic and BOM.

This keeps the one genuinely dangerous part of the circuit exactly where the
hardware plan already puts it — spaced out, inspectable, and not buried under
soldermask — while still getting a repeatable board for the parts that
benefit most from repeatability.

---

## Tool choice

| Tool | Verdict | Why |
|---|---|---|
| **KiCad** | **Recommended** | Free, no subscription, the de facto hobbyist/pro standard, huge tutorial base. Exports Gerbers, BOM, and pick-and-place (CPL) files directly in the format JLCPCB's order flow expects. Not locked to one fab. |
| **EasyEDA** | Reasonable alternative | Browser-based, zero install, live LCSC part search/pricing inline, one-click "order at JLCPCB" (EasyEDA and JLCPCB/LCSC are the same corporate family). Lower friction if you don't want to install anything, at the cost of being more locked into that ecosystem. |
| **Flux.ai** | Not recommended for this board | AI-assisted design is a genuine draw, but it adds a subscription and a learning curve this board doesn't need — no BGAs, no high-speed routing, 2 layers, a dozen-ish parts. Overkill for the complexity here. |

This board (TP4056 + buck/boost regulator + ESP32 header + a handful of
passives and terminals) has no routing complexity that would make KiCad's
manual layout painful. Reach for something like Flux.ai when a board's
complexity actually demands layout assistance — this one doesn't.

---

## JLCPCB ordering walkthrough

1. **Finish the schematic and layout in KiCad** (see
   [logic-power-board-schematic.md](logic-power-board-schematic.md) for the
   circuit this board needs to implement).
2. **Generate fab outputs:** KiCad PCB Editor → *File → Fabrication Outputs →
   Gerbers* (and *Drill Files*). JLCPCB reads KiCad's default Gerber job file
   directly — no manual layer-mapping needed with modern KiCad versions.
3. **Upload to JLCPCB** (jlcpcb.com → Instant Quote → upload the Gerber ZIP).
   Defaults to check/change:
   - Layers: **2** (this board doesn't need more)
   - Qty: **5** (JLCPCB's minimum; costs a few dollars total at that
     quantity, plus shipping) — order more than one prototype run needs so
     spares exist for the 2–5-node fleet without a second fab cycle
   - Everything else (thickness, color, surface finish) — defaults are fine
     for a prototype
4. **Assembly:** skip JLCPCB's SMT assembly service for this board. It's
   low-volume, has several connectors/headers that are easier to place by
   hand than to fixture for pick-and-place, and hand-soldering lets you
   inspect each joint on a board that's going to live outdoors for months.
   Order the bare board only.
5. **Order the BOM parts separately** (LCSC, since JLCPCB's parts stock is
   the same catalog — see [logic-power-board-schematic.md](logic-power-board-schematic.md#bom)
   for specific part numbers) and hand-solder.

---

## What's still open

- Exact ESP32 mounting approach (bare WROOM-32 module vs. socketed DevKit via
  pin headers) — see the note in
  [logic-power-board-schematic.md](logic-power-board-schematic.md). Leaning
  toward socketed headers for the first board revision: much lower risk than
  laying out RF/antenna keepout for a bare module, and a dead ESP32 becomes a
  field swap instead of a board respin.
- Buck/boost regulator IC hasn't been breadboard-validated yet — the part
  named in the BOM ([TPS63001](logic-power-board-schematic.md#bom)) is a
  reasonable, commonly available choice for single-cell Li-ion → fixed 3.3V,
  but should be confirmed on the bench during Phase 3 (power system) before
  it's treated as final.
- Enclosure cutout/mounting layout for two physical boards (HV hand-wired
  section + Logic & Power PCB) instead of one — feeds into
  [hardware-plan.md Phase 5](hardware-plan.md#phase-5--enclosure--weatherproofing).
