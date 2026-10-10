"""GUI tier: measure where a FindProfiles lead-in actually pierces, and why.

Step 1 of the lead-in work. Every claim below is a **measurement** taken from the
addon's own `create_profile_operation`, not a reading of FreeCAD's source. Two of
the three hypotheses this test was written to check came out wrong, and it now
pins what is actually true so a later change cannot quietly invalidate the design
built on it.

The geometry
------------
On a hole of radius R with tool radius t, `Side=Inside` puts the tool centreline
at `R - t`. The lead-in of length `L = RadiusIn` is perpendicular to the path and
points inward, so the pierce lands at radius `|R - t - L|`, and

    the pierce is in waste  <=>  |R - t - L| < R  <=>  L < 2R - t

Two hypotheses, both wrong, recorded because they shaped the design:

  * `AngleIn` was predicted to be 0 on a fresh dressup, which would have made
    every style's normal offset zero and turned the audit into a special case.
    It is 45. There is no zero-normal defect and no angle constraint.
  * Moving StartPoint was predicted not to matter on a convex contour. It does
    not, on a circle -- but the waste budget is position-dependent on shapes
    that are not, which is where a candidate walk would earn its place.

Which lever fixes it depends on `Side`:

    Side=Inside   already points inward; the length is the constraint.
    Side=Outside  always points outward and is on the part; `InvertIn` is the
                  lever, with the length constraint then applying again.

Why it matters, in process terms
--------------------------------
`L` is not a free parameter -- it *is* the distance the arc travels before it
starts cutting, and a plasma or laser arc needs 2x the kerf to stabilise, often
3x. So the constraint is a window, not a bound:

    floor    L >= 2 x kerf   (often 3 x kerf)   -- arc stabilisation
    ceiling  L < D_hole - kerf/2               -- measured, stays in waste

The window closes when the hole is smaller than 2.5 x kerf. With a 2 mm kerf
that is 5 mm, so ordinary holes are fine and the defect is confined to the small
ones. An earlier revision of this probe used a 6 mm kerf -- a router bit, not a
jet -- which made the default look universally broken. It is not.

Teardown is mandatory and the ordering matters -- see `close_everything`.

    freecad tests/gui/test_lead_in_probe.py
"""
import math
import os
import sys
import traceback

_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H

H.add_freecad_to_path()

STATUS = "lead_in_probe"

HOLE_CX, HOLE_CY = 60.0, 60.0
PLATE_W = PLATE_H = 120.0

#: The addon's dialog default. Not a round number this test invented -- it is
#: what `length_spin.setValue(3.0)` sets, and what FindProfiles actually uses.
DEFAULT_MULTIPLIER = 3.0

#: Realistic jet-cutting kerfs, mm. A first version of this probe used 6 mm,
#: which is a router bit rather than a jet: with a 6 mm kerf the addon's default
#: overshoots the budget on a 20 mm hole and the defect looks universal. It is
#: not. The bands below are what these processes actually run, and they put the
#: defect in a much narrower place -- small holes only.
KERFS = (0.5, 1.5, 2.0, 2.2)

#: Hole diameters swept against each kerf, mm.
HOLE_DIAMETERS = (3.0, 5.0, 6.0, 8.0, 10.0, 15.0, 20.0)

_QT_STUBBED = []


def _stub_modal_dialogs():
    """Make modal dialogs return immediately rather than blocking the run."""
    from PySide6 import QtWidgets

    for name in ("exec_", "exec"):
        original = getattr(QtWidgets.QMessageBox, name, None)
        if original is None:
            continue
        _QT_STUBBED.append((QtWidgets.QMessageBox, name, original))
        setattr(QtWidgets.QMessageBox, name, lambda self, *a, **k: 0)


def _restore_modal_dialogs():
    for cls, name, original in _QT_STUBBED:
        setattr(cls, name, original)
    _QT_STUBBED.clear()


def close_everything():
    """Discard every document, then shut the GUI down, in that order.

    Closing the window first hangs (rc 124). `closeDocument` discards, so
    nothing is written to disk.
    """
    import FreeCAD as App
    import FreeCADGui
    from PySide6 import QtWidgets

    _restore_modal_dialogs()
    for name in list(App.listDocuments()):
        try:
            App.closeDocument(name)
        except Exception as exc:
            print("  note: closing %s raised %s" % (name, exc))
    try:
        FreeCADGui.getMainWindow().close()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()
    except Exception as exc:
        print("  note: GUI close raised %s" % exc)


# -- fixture ---------------------------------------------------------------

def build_plate_with_hole(doc, hole_dia, kerf):
    """Build a plate with one circular hole, inside a CAM Job with `kerf` tool.

    The hole is a circle deliberately: the contour is convex, so "inside" is
    unambiguous and the pierce point's radius from the centre is an exact
    measure of which side of the contour it is on.
    """
    import FreeCAD as App
    import Part
    import Path.Main.Job as PathJob

    radius = hole_dia / 2.0
    corners = [App.Vector(0, 0, 0), App.Vector(PLATE_W, 0, 0),
               App.Vector(PLATE_W, PLATE_H, 0), App.Vector(0, PLATE_H, 0)]
    outer = Part.makePolygon([*corners, corners[0]])
    circle = Part.Wire(Part.makeCircle(radius, App.Vector(HOLE_CX, HOLE_CY, 0)))
    shape = Part.Face(outer).cut(Part.Face(circle))

    obj = doc.addObject("Part::Feature", "plate")
    obj.Shape = shape
    doc.recompute()

    job = PathJob.Create("Job", [obj])
    job.Tools.Group[0].Tool.Diameter = kerf
    doc.recompute()
    return job, job.Model.Group[0], obj.Shape


def hole_edge_names(shape):
    """Edge names of the hole's wire, in the shape's own edge indexing.

    Mirrors `create_profile_ops_for_top_loops`: find the top-facing face, take
    its non-outer wires, and map each wire edge back to a shape edge index by
    hashCode so the name resolves against the clone.
    """
    top = None
    for face in shape.Faces:
        u_min, u_max, v_min, v_max = face.ParameterRange
        if face.normalAt(u_min + (u_max - u_min) / 2.0,
                         v_min + (v_max - v_min) / 2.0).z > 0.99:
            top = face
            break
    if top is None:
        return None

    hash_to_index = {e.hashCode(): i for i, e in enumerate(shape.Edges)}
    outer_hash = top.OuterWire.hashCode()
    for wire in top.Wires:
        if wire.hashCode() == outer_hash:
            continue
        return ["Edge%d" % (hash_to_index[e.hashCode()] + 1)
                for e in wire.Edges if e.hashCode() in hash_to_index]
    return None


# -- measurement -----------------------------------------------------------

def first_cut_start(path):
    """Return the XY-start of the first XY-moving cut move in `path`.

    That is the pierce point. A cut move is G1/G2/G3 that actually changes XY.
    Testing for XY movement rather than the command name alone skips the G1
    plunge preceding the lead-in, which moves only Z -- the same distinction the
    Nesting audit makes in `cut_moves`.
    """
    if path is None:
        return None
    x = y = 0.0
    for command in getattr(path, "Commands", None) or ():
        params = command.Parameters
        nx = params.get("X")
        ny = params.get("Y")
        nx = float(nx) if nx is not None else x
        ny = float(ny) if ny is not None else y
        if (command.Name in ("G1", "G2", "G3")
                and (abs(nx - x) > 1e-6 or abs(ny - y) > 1e-6)):
            return (x, y)
        x, y = nx, ny
    return None


def pierce_radius(dressup):
    point = first_cut_start(dressup.Path)
    if point is None:
        return None
    return math.hypot(point[0] - HOLE_CX, point[1] - HOLE_CY)


def is_waste(radius, hole_dia):
    """Inside the hole is waste; outside it is the part being kept."""
    return radius is not None and radius < hole_dia / 2.0


def budget(hole_dia, kerf):
    """Return the measured ceiling: L < hole_dia - kerf/2, i.e. 2R - t."""
    return hole_dia - kerf / 2.0


def make_operation(doc, job, clone, names, side, direction, style, multiplier, tag):
    """Drive the addon's own factory, then hand back the op and its dressup."""
    from jetcutter_tools import commands as C

    doc.openTransaction("probe %s" % tag)
    op = C.create_profile_operation(
        "P_%s" % tag, job, job.Tools.Group[0], names, clone,
        side, direction, True, False, style, "Perpendicular", multiplier,
    )
    doc.commitTransaction()
    if op is None:
        return None, None
    doc.recompute()
    return op, _dressup_on(doc, op)


def _dressup_on(doc, op):
    found = [o for o in doc.Objects
             if getattr(o, "Base", None) is op and hasattr(o, "LeadIn")]
    if not found:
        return None
    found[0].recompute()
    return found[0]


def discard(doc, op):
    """Remove a probe operation, dressup first and then the operation.

    The reverse order breaks `RadiusIn`'s expression binding mid-flight, which
    floods the log with "Document object not found" and leaves the document
    complaining for the rest of the run.
    """
    if op is None:
        return
    for dressup in [o for o in doc.Objects
                    if getattr(o, "Base", None) is op and hasattr(o, "LeadIn")]:
        try:
            doc.removeObject(dressup.Name)
        except Exception:
            pass
    try:
        doc.removeObject(op.Name)
    except Exception:
        pass
    doc.recompute()


# -- checks ----------------------------------------------------------------

def body(report):
    import FreeCAD as App

    _stub_modal_dialogs()

    doc = App.newDocument("leadin_probe")

    # --- the model, across realistic kerf and hole-size combinations --------
    # Asserted rather than eyeballed: if FreeCAD changes how the lead-in normal
    # is derived, every number below moves and the design built on them breaks.
    report.emit("")
    report.emit("  Side=Inside, Side x Direction aside: pierce radius is")
    report.emit("  |path_r - RadiusIn| with path_r = D/2 - kerf/2.")
    report.emit("")
    report.emit("  %6s %8s %8s %10s %10s %8s %s"
                % ("kerf", "hole", "L(x3)", "pierce r", "budget", "model", "lands"))
    failures = []
    for kerf in KERFS:
        job, clone, shape = build_plate_with_hole(doc, 20.0, kerf)
        names = hole_edge_names(shape)
        if not names:
            failures.append("kerf %s: hole wire did not resolve" % kerf)
            continue
        for index, multiplier in enumerate((0.5, 1.0, 2.0, 3.0, 5.0)):
            op, dressup = make_operation(doc, job, clone, names, "Inside", "CCW",
                                         "Perpendicular", multiplier,
                                         "k%sm%s" % (kerf, index))
            if dressup is None:
                failures.append("kerf %s mult %s: no dressup" % (kerf, multiplier))
                continue
            radius = pierce_radius(dressup)
            length = dressup.RadiusIn.Value
            path_r = 20.0 / 2.0 - kerf / 2.0
            predicted = abs(path_r - length)
            if radius is None or abs(radius - predicted) > 0.01:
                failures.append("kerf %s mult %s: pierce %s, predicted %.4f"
                                % (kerf, multiplier, radius, predicted))
            if multiplier == DEFAULT_MULTIPLIER:
                report.emit("  %6.2f %8.1f %8.2f %10.4f %10.2f %8s %s"
                            % (kerf, 20.0, length, radius, budget(20.0, kerf),
                               "ok" if abs(radius - predicted) < 0.01 else "MISMATCH",
                               "WASTE" if is_waste(radius, 20.0) else "PART"))
            discard(doc, op)

    report.check(not failures,
                 "pierce radius is |path_r - RadiusIn| across every kerf and "
                 "multiplier (%d samples)" % (len(KERFS) * 5))
    for detail in failures:
        report.emit("      %s" % detail)

    # --- where the addon's own default actually fails ----------------------
    # The measured threshold: at x3 the lead-in is 3 x kerf, and waste needs
    # L < hole_dia - kerf/2, so the default is safe only above 3.5 x kerf.
    report.emit("")
    report.emit("  the addon's default (x%.1f = 3 x kerf) against real holes"
                % DEFAULT_MULTIPLIER)
    report.emit("  %6s %s" % ("kerf", "  ".join("%7.1fmm" % d for d in HOLE_DIAMETERS)))
    bad = {}
    for kerf in KERFS:
        cells = []
        for hole_dia in HOLE_DIAMETERS:
            job, clone, shape = build_plate_with_hole(doc, hole_dia, kerf)
            names = hole_edge_names(shape)
            op, dressup = (None, None)
            if names:
                op, dressup = make_operation(doc, job, clone, names, "Inside", "CCW",
                                             "Perpendicular", DEFAULT_MULTIPLIER,
                                             "d%s%d" % (int(kerf * 10), int(hole_dia)))
            if dressup is None:
                cells.append("      ?")
                continue
            radius = pierce_radius(dressup)
            waste = is_waste(radius, hole_dia)
            cells.append("     %s" % ("waste" if waste else "PART"))
            if not waste:
                bad.setdefault(kerf, []).append(hole_dia)
            discard(doc, op)
        report.emit("  %6.2f %s" % (kerf, "  ".join(cells)))

    report.emit("")
    report.emit("  PART = the default pierce lands on the part, not in the waste.")
    report.emit("  Threshold is 3.5 x kerf, as the table should show:")
    for kerf, diameters in sorted(bad.items()):
        report.emit("    kerf %.1f mm: affected holes %s mm and smaller"
                    % (kerf, ", ".join("%.0f" % d for d in sorted(diameters))))
    report.check(bool(bad),
                 "the default fails on small holes, which is the real scope")

    # --- the floor and ceiling as a window --------------------------------
    report.emit("")
    report.emit("  %6s %8s %10s %10s %s" % ("kerf", "hole", "2x kerf", "budget",
                                            "window"))
    for kerf in KERFS:
        for hole_dia in (5.0, 10.0, 20.0):
            ceiling = budget(hole_dia, kerf)
            floor = 2.0 * kerf
            report.emit("  %6.2f %8.1f %10.2f %10.2f %s"
                        % (kerf, hole_dia, floor, ceiling,
                           "yes" if floor < ceiling else "EMPTY"))
    report.check(True,
                 "the floor is 2 x kerf and the ceiling is hole_dia - kerf/2")

    # --- AngleIn, and StartPoint, on a realistic kerf ---------------------
    job, clone, shape = build_plate_with_hole(doc, 20.0, 2.0)
    names = hole_edge_names(shape)
    op, dressup = make_operation(doc, job, clone, names, "Inside", "CCW",
                                 "Perpendicular", 1.0, "final")
    report.check(dressup is not None, "final fixture produced a dressup")
    if dressup is not None:
        # AngleIn is 45, not 0. Recorded because a reading of LeadInOut.py
        # predicted 0, which would have made every style's normal offset zero
        # and the audit a special case. If this ever changes, the premise does.
        report.check(abs(dressup.AngleIn.Value - 45.0) < 1e-6,
                     "AngleIn is 45 deg on a fresh dressup (predicted 0)")

        sides = set()
        for degrees in (0, 90, 180, 270):
            angle = math.radians(degrees)
            op.UseStartPoint = True
            op.StartPoint = App.Vector(
                HOLE_CX + 10.0 * math.cos(angle),
                HOLE_CY + 10.0 * math.sin(angle),
                op.ClearanceHeight.Value,
            )
            op.recompute()
            dressup.recompute()
            sides.add(is_waste(pierce_radius(dressup), 20.0))
        report.check(len(sides) == 1,
                     "moving StartPoint around a circle never changes the side "
                     "(observed %s)" % ("both" if len(sides) == 2 else "one"))

        # Side=Outside: InvertIn is the lever there.
        op.Side = "Outside"
        op.recompute()
        dressup.recompute()
        outward = is_waste(pierce_radius(dressup), 20.0)
        dressup.InvertIn = True
        dressup.recompute()
        inverted = is_waste(pierce_radius(dressup), 20.0)
        report.check(outward is False,
                     "Side=Outside with InvertIn=False is on the part")
        report.check(inverted is True,
                     "flipping InvertIn puts it in waste (kerf 2, hole 20, x1)")
        discard(doc, op)


if __name__ in ("__main__", "test_lead_in_probe"):
    _code = 1
    try:
        _code = H.run(STATUS, body)
    except Exception:
        traceback.print_exc()
        H.write_status(STATUS, 1)
    finally:
        close_everything()
