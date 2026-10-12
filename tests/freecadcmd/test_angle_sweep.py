"""Angle sweep for fixed-start (concave) chains.

Verifies the analytic geometry in lead_in_audit.py:
- angle_candidates alternates from 90°
- lead_in_pierce radius is invariant
- flip negates normal only
- narrow-mouth notch finds working angle > 90°
- open chain waste polygon implicitly closes along boundary
"""
import os
import sys

_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H

H.add_freecad_to_path()

import math
from jetcutter_tools.lead_in_audit import (
    angle_candidates,
    lead_in_pierce,
    is_in_waste,
    waste_polygon,
)

STATUS = "angle_sweep"


def body(report):
    # --- angle_candidates alternates from 90° ---
    candidates = list(angle_candidates(step=10, low=20, high=160))
    report.check_eq(candidates, [90, 80, 100, 70, 110, 60, 120, 50, 130, 40, 140, 30, 150, 20, 160],
                    "angle_candidates alternates from 90")

    # --- pierce radius invariant ---
    start = (0.0, 0.0)
    tangent = (1.0, 0.0)
    normal = (0.0, 1.0)
    length = 3.6
    for ang in angle_candidates(step=2, low=2, high=178):
        p = lead_in_pierce(start, tangent, normal, length, ang)
        dist = math.hypot(p[0] - start[0], p[1] - start[1])
        report.check(math.isclose(dist, length, abs_tol=1e-9),
                     f"angle {ang}: pierce radius {dist:.6f} == {length}")

    # --- 0° and 180° are degenerate (tangent only) ---
    p0 = lead_in_pierce(start, tangent, normal, length, 0)
    p180 = lead_in_pierce(start, tangent, normal, length, 180)
    report.check(math.isclose(p0[0], -3.6) and math.isclose(p0[1], 0.0, abs_tol=1e-9),
                 "0° pierce behind start on tangent")
    report.check(math.isclose(p180[0], 3.6, abs_tol=1e-9) and math.isclose(p180[1], 0.0, abs_tol=1e-9),
                 "180° pierce ahead of start on tangent")

    # --- flip negates normal only ---
    p_norm = lead_in_pierce(start, tangent, normal, length, 90)
    p_flip = lead_in_pierce(start, tangent, (-normal[0], -normal[1]), length, 90)
    report.check(math.isclose(p_norm[0], 0.0, abs_tol=1e-9) and math.isclose(p_norm[1], 3.6),
                 "90° normal: (0, L)")
    report.check(math.isclose(p_flip[0], 0.0, abs_tol=1e-9) and math.isclose(p_flip[1], -3.6),
                 "90° flipped: (0, -L)")

    # --- narrow-mouth notch: 90° fails, 120° works ---
    # Waste polygon: chain edges + closing segment along top edge
    # Counter-clockwise: (90,100) -> (90,80) -> (110,80) -> (110,100) -> close
    waste_pts = [
        (90.0, 100.0), (90.0, 80.0), (110.0, 80.0), (110.0, 100.0),
        (90.0, 100.0),
    ]
    region = waste_polygon(waste_pts)
    report.check(region is not None and not region.is_empty,
                 "waste region should be valid")

    # Geometry at LEFT corner of notch mouth, normal pointing RIGHT (into notch)
    start = (90.0, 100.0)
    tangent = (0.0, -1.0)
    normal = (1.0, 0.0)
    length = 5.0

    # 90° (Perpendicular): pierce on top edge inside notch mouth - zero clearance
    p90 = lead_in_pierce(start, tangent, normal, length, 90)
    report.check(math.isclose(p90[0], 95.0, abs_tol=1e-9) and math.isclose(p90[1], 100.0, abs_tol=1e-9),
                 "90° pierce at (95, 100) on boundary")
    report.check(not is_in_waste(p90, region, margin=0.6),
                 "90° fails kerf/2 clearance")

    # 120°: points back and down into the notch
    p120 = lead_in_pierce(start, tangent, normal, length, 120)
    report.check(math.isclose(p120[0], 94.33, abs_tol=0.01) and math.isclose(p120[1], 97.5, abs_tol=0.01),
                 "120° pierce at (94.33, 97.5) inside waste")
    report.check(is_in_waste(p120, region, margin=0.6),
                 "120° clears waste with kerf/2 margin")

    # First working angle in sweep order should be > 90
    working = []
    for ang in angle_candidates(step=10, low=20, high=160):
        p = lead_in_pierce(start, tangent, normal, length, ang)
        if is_in_waste(p, region, margin=0.6):
            working.append(ang)
    report.check(len(working) > 0, "at least one angle works")
    first_working = working[0]
    report.check(first_working > 90, f"first working angle {first_working} > 90")

    # --- waste polygon closure for open chain ---
    pts = [(90.0, 100.0), (90.0, 80.0), (110.0, 80.0), (110.0, 100.0)]
    region = waste_polygon(pts)
    report.check(region is not None and region.area > 0, "open chain closes implicitly")
    # Area = 20 wide * 20 deep = 400
    report.check(math.isclose(region.area, 400.0, abs_tol=1.0),
                 f"closed area = 400, got {region.area:.1f}")


if __name__ in ("__main__", "test_angle_sweep"):
    H.run(STATUS, body)