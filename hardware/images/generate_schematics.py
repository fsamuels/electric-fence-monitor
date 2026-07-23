#!/usr/bin/env python3
"""Regenerate the schematic images in this directory.

Requires: pip install schemdraw cairosvg

    python3 hardware/images/generate_schematics.py

Produces hv-sensing-chain-schematic.{svg,png} and
logic-power-board-schematic.{svg,png} in this directory. Edit the drawings
below and re-run rather than hand-editing the SVG/PNG output.
"""
import os

import schemdraw
import schemdraw.elements as elm
from schemdraw.elements import Ic, IcPin

OUTDIR = os.path.dirname(os.path.abspath(__file__))


def _png(svg_path):
    try:
        import cairosvg
        cairosvg.svg2png(url=svg_path, write_to=svg_path.replace('.svg', '.png'), scale=2)
    except ImportError:
        print(f'cairosvg not installed, skipping PNG for {svg_path}')


def gen_hv_sensing_chain():
    """HV divider + peak detector — the hand-wired, off-board sensing chain.

    Mirrors the ASCII schematic in voltage-divider-schematic.md; this is a
    rendered view of the same circuit for easier at-a-glance reading.
    """
    schemdraw.theme('default')
    d = schemdraw.Drawing(fontsize=11)
    d.config(unit=2.4)

    def branch_down(start_xy, dx, element, label, ground=True):
        nonlocal d
        d.push()
        d += elm.Line().at(start_xy).right().length(dx)
        d += element.down().label(label, loc='right', fontsize=8.5)
        if ground:
            d += elm.Ground()
        d.pop()

    d += elm.Dot(open=True).label('FENCE\n(HV, ~10 kV pulses)', loc='top', fontsize=10)
    d += elm.Line().down().length(1.1)
    d += elm.Resistor().down().label(
        'R1–R10\n10×100MΩ series\n(air-gap spaced)', loc='right', fontsize=9)
    nodeA = elm.Dot()
    d += nodeA

    branch_down(nodeA.start, 0.0, elm.Resistor(), 'Rsense\n270kΩ 1%')
    branch_down(nodeA.start, 2.0, elm.Zener(), 'D1\n3.3V clamp')

    d += elm.Line().right().at(nodeA.start).length(4.0)
    d += elm.Diode().right().label('D_peak\n1N4148 (fast)', loc='top', fontsize=9)
    nodeB = elm.Dot()
    d += nodeB

    branch_down(nodeB.start, 0.0, elm.Capacitor(), 'Cpk\n0.1µF')
    branch_down(nodeB.start, 2.0, elm.Resistor(), 'Rbleed\n1–3MΩ*')
    branch_down(nodeB.start, 4.0, elm.Zener(), 'D2\n3.3–3.6V clamp')

    d += elm.Line().right().at(nodeB.start).length(6.2)
    d += elm.Dot(open=True)
    d += elm.Gap().label('TO PCB\n(J1: FENCE_ADC_IN)', loc='bottom', fontsize=9)

    d += elm.Rect(corner1=(-1.3, -6.3), corner2=(24.5, 3.0)).at((0, 0)).linestyle('--').color('gray')
    d += elm.Label().at((-1.1, 2.7)).label(
        'Hand-wired, off-board - real air-gap spacing required, not a PCB (up to 10 kV)',
        fontsize=10.5, halign='left')
    d += elm.Label().at((-1.1, -6.05)).label(
        '* Rbleed is empirical - tune on breadboard (Phase 2), record in docs/rc-tuning-results.md',
        fontsize=8.5, halign='left')

    svg_path = os.path.join(OUTDIR, 'hv-sensing-chain-schematic.svg')
    d.save(svg_path)
    _png(svg_path)


def gen_logic_power_board():
    """Solar/battery power path + ESP32 interface — the actual PCB target."""
    schemdraw.theme('default')
    d = schemdraw.Drawing(fontsize=10)
    d.config(unit=2.0)

    def box(x, y, w, h, left_pins, right_pins, title):
        pins = []
        n = len(left_pins)
        for i, nm in enumerate(left_pins):
            pins.append(IcPin(name=nm, side='left', slot=f'{i+1}/{n}', anchorname=f'L{i}'))
        n = len(right_pins)
        for i, nm in enumerate(right_pins):
            pins.append(IcPin(name=nm, side='right', slot=f'{i+1}/{n}', anchorname=f'R{i}'))
        ic = Ic(pins=pins, size=(w, h)).theta(0).at((x, y)).anchor('L0')
        ic = ic.label(title, 'top', fontsize=9.5)
        return ic

    def gnd(xy):
        nonlocal d
        d.push()
        d += elm.Line().at(xy).down().length(0.5)
        d += elm.Ground()
        d.pop()

    def route(p1, p2, midx=None):
        """Orthogonal right-then-vertical-then-right wire between two points."""
        nonlocal d
        x1, y1 = p1
        x2, y2 = p2
        if midx is None:
            midx = (x1 + x2) / 2
        d += elm.Line().at((x1, y1)).right().tox(midx)
        if y2 > y1:
            d += elm.Line().up().toy(y2)
        elif y2 < y1:
            d += elm.Line().down().toy(y2)
        d += elm.Line().right().tox(x2)

    tp = box(4.2, 0, 2.6, 2.2, ['IN-', 'IN+'], ['BAT-', 'BAT+'], 'TP4056\ncharge ctrl')
    d += tp
    reg = box(10.4, 0, 2.4, 1.6, ['IN-', 'IN+'], ['GND', '3V3'], 'Buck/\nBoost')
    d += reg
    esp = box(17.2, 0, 3.0, 3.4, ['ADC35', 'ADC34', 'GND', '3V3'], ['ANT'], 'ESP32\nmodule')
    d += esp

    route(tp.R0, reg.L0)   # BAT- -> IN-
    route(tp.R1, reg.L1)   # BAT+ -> IN+
    route(reg.R0, esp.L2)  # GND  -> ESP32 GND
    route(reg.R1, esp.L3)  # 3V3  -> ESP32 3V3
    gnd(reg.R0)

    d += elm.Solar().theta(0).at((-3.4, 1.6)).label('SOLAR\n5-6W', 'left', fontsize=9)
    d += elm.Line().right().length(0.9)
    d += elm.Dot().label('J2.1\nSOLAR+', 'top', fontsize=7.5)
    route((-2.5, 1.6), tp.L1)

    d += elm.Line().at((-3.4, 1.6)).down().length(0.7)
    d += elm.Line().right().length(0.9)
    d += elm.Dot().label('J2.2\nSOLAR-', 'bottom', fontsize=7.5)
    route((-2.5, 0.9), tp.L0)

    d += elm.Battery().theta(0).at((7.4, -3.2)).label('18650\nLi-ion', 'bottom', fontsize=9)
    route((7.4, -3.2), tp.R0, midx=7.4)
    route((9.4, -3.2), tp.R1, midx=9.9)

    j1_pt = (14.4, 4.4)
    d += elm.Dot(open=True).at(j1_pt).label(
        'J1: FENCE_ADC_IN (from HV sensing\nchain, post-Zener clamp)', 'top', fontsize=8)
    route(j1_pt, esp.L1, midx=15.8)

    # Battery sense divider, routed well below the battery so it clears the leads.
    sense_x = 15.3
    d += elm.Line().at((7.8, 1.2)).right().tox(10.0)
    d += elm.Line().down().toy(-4.8)
    d += elm.Line().right().tox(sense_x)
    d += elm.Line().down().length(0.4)
    d += elm.Resistor().down().label('100k', 'right', fontsize=8)
    tap = d.here
    d += elm.Resistor().down().label('100k', 'right', fontsize=8)
    d += elm.Ground()
    d += elm.Dot().at(tap)
    d += elm.Line().at(tap).right().tox(sense_x + 1.0)
    d += elm.Line().up().toy(esp.L0[1])
    d += elm.Line().right().tox(esp.L0[0])

    d += elm.Rect(corner1=(-4.6, -7.0), corner2=(21.3, 5.6)).at((0, 0)).linestyle('--').color('gray')
    d += elm.Label().at((-4.2, 5.15)).label(
        'PCB - Logic and Power board  (KiCad target, fab at JLCPCB)', fontsize=11, halign='left')
    d += elm.Label().at((-4.2, -6.7)).label(
        'HV sensing chain (divider + peak detector) stays hand-wired off-board.\n'
        'See voltage-divider-schematic.svg', fontsize=8.5, halign='left')

    svg_path = os.path.join(OUTDIR, 'logic-power-board-schematic.svg')
    d.save(svg_path)
    _png(svg_path)


if __name__ == '__main__':
    gen_hv_sensing_chain()
    gen_logic_power_board()
    print('Done.')
