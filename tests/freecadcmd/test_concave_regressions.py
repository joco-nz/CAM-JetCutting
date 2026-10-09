"""End-to-end regression: separate notches are never merged into one profile.

This is the L3 / ISS-003 bug, tested through the real geometry pipeline rather
than at the unit level. The unit tests in `tests/test_concave_chains.py` pin
`group_concave_chains`; this one builds real Part wires and checks that the
detector never returns a chain spanning two features — because that is the
failure that produced a toolpath crossing the part.

Runs under `freecadcmd`. Gated by `tests/run.sh`; writes
`tests/freecadcmd/status/concave_regressions.status`.

    freecadcmd tests/freecadcmd/test_concave_regressions.py
"""
import os
import sys

# `freecadcmd` does not put this script's directory on sys.path, so make the
# harness importable before anything else.
_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
# One shared harness for both FreeCAD tiers; a copy per tier would drift.
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H

H.add_freecad_to_path()

import FreeCAD as App
import Part
from jetcutter_tools import geometry as G

W = 100.0
H_PART = 40.0
DEPTH = 20.0
TOL = 5.0
STATUS = "concave_regressions"


def rect_with_notches(notches, w=W, h=H_PART, depth=DEPTH):
    """Rectangle outline with rectangular notches cut into its top edge.

    Each notch is (a, b) meaning it spans X from w-b to w-a.
    """
    pts = [(0, 0), (w, 0)]
    for a, b in notches:
        pts += [(w - a, h), (w - a, depth), (w - b, depth), (w - b, h)]
    return [*pts, (0, h)]


def build_wire(points):
    edges = [
        Part.makeLine(App.Vector(*points[i], 0), App.Vector(*points[(i + 1) % len(points)], 0))
        for i in range(len(points))
    ]
    return Part.Wire(Part.__sortEdges__(edges))


def chain_x_spans(chains):
    """The X extent each chain covers, merged by identical minima."""
    spans = [
        (
            round(min(v.X for e in ch for v in e.Vertexes), 1),
            round(max(v.X for e in ch for v in e.Vertexes), 1),
        )
        for ch in chains
        for e in ch
    ]
    return [(a, max(b for a2, b2 in spans if a2 == a)) for a, b in spans]


def body(report):
    report.emit("environment")
    report.emit("  %s" % H.require_real_freecad())

    # --- 1. every rotation must produce the same number of chains -----------
    # The sort seam lands on a different edge for each rotation, and the merge
    # bug only fired on some of them.
    for label, notches, want_chains in (
        ("2 notches", [(10, 20), (80, 90)], 2),
        ("3 notches", [(10, 20), (45, 55), (80, 90)], 3),
    ):
        base = rect_with_notches(notches)
        seen = set()
        bad = []
        for rot in range(len(base)):
            pts = base[rot:] + base[:rot]
            wire = build_wire(pts)
            chains = G.find_concave_chains(wire, None, TOL)
            seen.add(len(chains))
            if len(chains) != want_chains or any(len(c) != 3 for c in chains):
                bad.append("rot=%d -> %d chains %r" % (rot, len(chains), [len(c) for c in chains]))
        report.check_eq(sorted(seen), [want_chains],
                        "%s across all %d rotations" % (label, len(base)))
        report.check(not bad, "%s: every rotation gives %d chains of 3 (%s)"
                     % (label, want_chains, "; ".join(bad[:3]) or "clean"))

    # --- 2. the specific pre-fix failure ------------------------------------
    # Two notches, seam between them. Pre-fix this was ONE chain of 6 edges.
    base = rect_with_notches([(10, 20), (80, 90)])
    wire = build_wire(base[3:] + base[:3])
    chains = G.find_concave_chains(wire, None, TOL)
    report.check_eq(len(chains), 2, "previously-merged case yields 2 chains")
    report.check_eq(sorted(len(c) for c in chains), [3, 3], "both chains are 3 edges")

    spans = sorted(set(chain_x_spans(chains)))
    report.emit("    chain X spans: %r" % spans)
    report.check(all(b - a <= 50 for a, b in spans),
                 "no chain spans more than one notch (a merged chain spans 10..90)")

    # --- 3. contiguity guard on real geometry -------------------------------
    # Take the concave edges from the detected chains rather than by index:
    # `Part.__sortEdges__` reorders, so positional indices into `wire.Edges`
    # say nothing about which edges form a notch.
    conc_edges = list(chains[0]) + list(chains[1])

    # The guard is only meaningful on the edges AS RESOLVED. Sorting first
    # repairs disconnected edges into one connected sequence and makes every
    # input look contiguous, which is what commands.py used to do — so the
    # guard could never fire (CR-11). Both halves are pinned here so the
    # ordering cannot be reversed without this check failing.
    report.check(G.is_contiguous_run(conc_edges) is False,
                 "a merged chain is REJECTED by the guard as resolved")
    report.check(G.is_contiguous_run(Part.__sortEdges__(conc_edges)) is True,
                 "sorting first would mask the problem (the CR-11 defect)")

    for idx, chain in enumerate(chains):
        report.check(G.is_contiguous_run(list(chain)) is True,
                     "notch %d on its own IS a contiguous run" % (idx + 1))

    # Each chain must also survive as a genuine sub-wire: the caller builds
    # Part.Wire() from the chain before profiling it.
    for idx, chain in enumerate(chains):
        sub = Part.Wire(Part.__sortEdges__(list(chain)))
        report.check(sub.isValid(), "notch %d forms a valid wire" % (idx + 1))
        report.check(not sub.isClosed(),
                     "notch %d is open, so it is profiled as a side cut" % (idx + 1))

    # --- 3b. no legitimate chain is rejected by the guard ------------------
    # The failure modes are asymmetric: a guard that misses a merged chain cuts
    # across the part, but a guard that rejects a real notch leaves a feature
    # uncut — and that is invisible. So assert the guard accepts everything the
    # detector reports, across notch shapes, depths and every rotation.
    rejected = []
    for notches, depth, label in (
        ([(10, 20), (80, 90)], 20.0, "two deep notches"),
        ([(10, 20), (80, 90)], 8.0, "two shallow notches"),
        ([(10, 20), (80, 90)], 5.0, "notches at the 5mm threshold"),
        ([(10, 20), (45, 55), (80, 90)], 20.0, "three notches"),
        ([(5, 45)], 20.0, "one wide notch"),
    ):
        shape = rect_with_notches(notches, depth=depth)
        for rot in range(len(shape)):
            rotated = shape[rot:] + shape[:rot]
            for chain in G.find_concave_chains(build_wire(rotated), None, TOL):
                if not G.is_contiguous_run(list(chain)):
                    rejected.append("%s rot=%d (%d edges)" % (label, rot, len(chain)))
    report.check(not rejected,
                 "no legitimate chain is rejected across 5 shapes x every rotation "
                 "(%d rejected)" % len(rejected))
    if rejected:
        report.emit("    rejected: %r" % (rejected[:5],))

    # --- 4. shapes with no notches produce nothing --------------------------
    plain = build_wire([(0, 0), (W, 0), (W, H_PART), (0, H_PART)])
    report.check_eq(G.find_concave_chains(plain, None, TOL), [],
                    "plain rectangle yields no chains")

    circle = Part.Wire(Part.makeCircle(20, App.Vector(50, 20, 0)))
    report.check_eq(G.find_concave_chains(circle, None, TOL), [],
                    "full circle yields no chains")

    # A circle is the case ISS-005 records as a limitation: every point sits
    # inside its own bounding box, so nothing is ever "deep". Asserted so the
    # limitation is pinned rather than discovered.
    report.emit("    (round parts produce no concave chains — known limitation, ISS-005)")


if __name__ in ("__main__", "test_concave_regressions"):
    # `freecadcmd` does not propagate sys.exit() to the shell — that is why the
    # harness writes a status file instead. Calling run() without exiting keeps
    # the interpreter from tearing down mid-write on some builds.
    H.run(STATUS, body)
