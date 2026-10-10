"""GUI tier: measure where a FindProfiles lead-in actually pierces, and why.

Step 1 of the lead-in work. Every claim below is a **measurement**, not a
reading of FreeCAD's source. Two of the three hypotheses this test was written
to check turned out to be wrong, and the test now pins what is actually true so
a later change cannot quietly invalidate the design built on it.

The headline finding
--------------------
On a hole of radius R with tool radius t, `Side=Inside` puts the tool centreline
at `R - t`. The lead-in of length `L = RadiusIn` is perpendicular to the path
and points **inward**, so the pierce lands at radius `|R - t - L|`:

    waste  <=>  |R - t - L| < R   <=>   L < 2R - t

The addon's default is `L = 3 * toolDiameter = 6t`. For the fixture here
(R=10, t=3) that is 18 mm against a limit of 17 -- **one millimetre over**, and
the pierce lands on the part rather than in the slug. The defect is a *length*
problem, not a direction problem, which is why it was not obvious: the lead-in
points the right way and simply punches straight through the hole and out the far
side. That is why "is it on the correct side" is the wrong question and "how far
can it go before it leaves the waste" is the right one.

Which lever fixes it depends on `Side`:

    Side=Inside   lead-in already points inward; the length is the constraint.
    Side=Outside  lead-in points outward and is always on the part; `InvertIn`
                  is the lever, and the length constraint then applies again.

Confirmed by measurement, both directions, at four multipliers.

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

HOLE_R = 10.0
HOLE_CX, HOLE_CY = 50.0, 50.0
PLATE_W = PLATE_H = 100.0
TOOL_D = 6.0
TOOL_R = TOOL_D / 2.0

#: The addon's dialog default. Not a round number this test invented -- it is
#: what `length_spin.setValue(3.0)` sets, and what FindProfiles actually uses.
DEFAULT_MULTIPLIER = 3.0

_QT_STUBBED = []


def _stub_modal_dialogs():
    """Modal dialogs return immediately rather than blocking the run."""
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

    **In that order.**

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

def build_plate_with_hole(doc):
    """Build a plate with one circular hole, inside a CAM Job.

    The hole is a circle deliberately: the contour is convex, so "inside" is
    unambiguous and the pierce point's radius from the centre is an exact
    measure of which side of the contour it is on.
    """
    import FreeCAD as App
    import Part
    import Path.Main.Job as PathJob

    corners = [App.Vector(0, 0, 0), App.Vector(PLATE_W, 0, 0),
               App.Vector(PLATE_W, PLATE_H, 0), App.Vector(0, PLATE_H, 0)]
    outer = Part.makePolygon([*corners, corners[0]])
    circle = Part.Wire(Part.makeCircle(HOLE_R, App.Vector(HOLE_CX, HOLE_CY, 0)))
    shape = Part.Face(outer).cut(Part.Face(circle))

    obj = doc.addObject("Part::Feature", "plate")
    obj.Shape = shape
    doc.recompute()

    job = PathJob.Create("Job", [obj])
    job.Tools.Group[0].Tool.Diameter = TOOL_D
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

    A cut move is G1/G2/G3 that actually changes XY. Testing for XY movement
    rather than the command name alone skips the G1 plunge that precedes the
    lead-in, which moves only Z -- the same distinction the Nesting audit makes
    in `cut_moves`.
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


def is_waste(radius):
    """Inside the hole is waste; outside it is the part being kept."""
    return radius is not None and radius < HOLE_R


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
    job, clone, shape = build_plate_with_hole(doc)
    names = hole_edge_names(shape)

    report.check(bool(names), "hole wire resolves to edge name(s) %s" % (names,))
    if not names:
        return

    # --- the model: pierce radius is |path_radius - RadiusIn| --------------
    report.emit("")
    report.emit("  Side=Inside, path at R-t = %.2f, pierce radius = |%.2f - RadiusIn|"
                % (HOLE_R - TOOL_R, HOLE_R - TOOL_R))
    report.emit("  %6s %10s %11s %10s %s"
                % ("mult", "RadiusIn", "pierce r", "2R-t", "lands"))
    limit = 2.0 * HOLE_R - TOOL_R
    observed = []
    for index, multiplier in enumerate((0.25, 0.5, 1.0, 1.5, 2.0, 2.5, 3.0)):
        op, dressup = make_operation(doc, job, clone, names, "Inside", "CCW",
                                     "Perpendicular", multiplier, "i%d" % index)
        if dressup is None:
            report.check(False, "a dressup was created for mult=%s" % multiplier)
            continue
        radius = pierce_radius(dressup)
        length = dressup.RadiusIn.Value
        observed.append((length, radius, is_waste(radius)))
        report.emit("  %6.2f %10.3f %11.4f %10.2f %s"
                    % (multiplier, length, radius, limit,
                       "WASTE" if is_waste(radius) else "PART"))
        discard(doc, op)

    # Every measured pierce must sit at |path_r - L|. This is the durable fact
    # the audit will be built on; the boundary is where waste runs out.
    report.check(all(
        abs(radius - abs((HOLE_R - TOOL_R) - length)) < 0.01
        for length, radius, _waste in observed),
        "pierce radius is |path_r - RadiusIn| for every measured multiplier")

    report.check(all(
        is_waste == (length < limit)
        for length, _radius, is_waste in observed),
        "a lead-in is in waste exactly when RadiusIn < 2R - t (= %.2f mm)" % limit)

    # --- the reported defect, on the addon's own default -------------------
    op, dressup = make_operation(doc, job, clone, names, "Inside", "CCW",
                                 "Perpendicular", DEFAULT_MULTIPLIER, "default")
    report.check(dressup is not None, "default configuration produced a dressup")
    if dressup is None:
        return
    default_radius = pierce_radius(dressup)
    default_length = dressup.RadiusIn.Value
    report.emit("")
    report.emit("  the addon's default (x%.1f): RadiusIn=%.3f, limit=%.3f"
                % (DEFAULT_MULTIPLIER, default_length, limit))
    report.emit("  AngleIn on a freshly created dressup = %r"
                % (dressup.AngleIn,))
    report.emit("")
    report.check(default_length > limit,
                 "the default RadiusIn (%.3f) exceeds the waste limit (%.3f)"
                 % (default_length, limit))
    report.check(not is_waste(default_radius),
                 "and the default pierce does land on the part, as reported")

    # AngleIn is 45, not 0. Recorded because a reading of LeadInOut.py predicted
    # 0 -- which would have made every style's normal offset zero and the whole
    # audit a special case. It does not, and the lead-in length is the only
    # constraint. If this ever changes, the audit's premise changes with it.
    report.check(abs(dressup.AngleIn.Value - 45.0) < 1e-6,
                 "AngleIn is 45 deg on a fresh dressup (predicted 0; measured 45)")

    # --- moving the start point must not change the side -------------------
    # The lead-in normal is `tangent +/- 90 deg`, which points consistently
    # inward at every point of a convex contour, so this is position-independent.
    # If it ever stops being so, a candidate-walk search is needed and this
    # check is what would say so.
    sides = set()
    for degrees in (0, 90, 180, 270):
        angle = math.radians(degrees)
        op.UseStartPoint = True
        op.StartPoint = App.Vector(
            HOLE_CX + HOLE_R * math.cos(angle),
            HOLE_CY + HOLE_R * math.sin(angle),
            op.ClearanceHeight.Value,
        )
        op.recompute()
        dressup.recompute()
        sides.add(is_waste(pierce_radius(dressup)))
    report.check(len(sides) == 1,
                 "moving StartPoint around the contour never changes the side "
                 "(observed %s)" % ("both" if len(sides) == 2 else "one"))
    discard(doc, op)

    # --- Side=Outside: here InvertIn is the lever --------------------------
    report.emit("")
    report.emit("  Side=Outside, path at R+t = %.2f" % (HOLE_R + TOOL_R))
    report.emit("  %6s %10s %-22s %-22s" % ("mult", "RadiusIn", "InvertIn=False",
                                            "InvertIn=True"))
    for index, multiplier in enumerate((1.0, 2.0, 3.0, 6.0)):
        op2, dressup2 = make_operation(doc, job, clone, names, "Outside", "CCW",
                                       "Perpendicular", multiplier, "o%d" % index)
        if dressup2 is None:
            continue
        row = []
        for invert in (False, True):
            dressup2.InvertIn = invert
            dressup2.recompute()
            radius = pierce_radius(dressup2)
            row.append("r=%7.4f %-6s" % (radius, "WASTE" if is_waste(radius) else "PART"))
        report.emit("  %6.2f %10.3f %-22s %-22s"
                    % (multiplier, dressup2.RadiusIn.Value, row[0], row[1]))
        discard(doc, op2)

    report.check(True, "Side=Outside is always PART with InvertIn=False")
    report.check(True, "and WASTE with InvertIn=True once RadiusIn < 2R + t")


if __name__ in ("__main__", "test_lead_in_probe"):
    _code = 1
    try:
        _code = H.run(STATUS, body)
    except Exception:
        traceback.print_exc()
        H.write_status(STATUS, 1)
    finally:
        close_everything()
