"""GUI tier: the lead-in audit, end to end on real geometry.

`tests/test_lead_in_audit.py` covers the decision rules with synthetic paths.
This drives the addon's own adapter -- `audit_leadin_pierces` against dressups
that FreeCAD has actually generated -- and checks the three outcomes that matter
on a real job:

  * a hole big enough for the window is left alone
  * a small hole has its lead-in shortened to the arc-stabilisation floor and
    then reads clean
  * a hole with no window at all is marked and left cutting, not silently
    dropped and not silently given a lead-in too short to work

That last one is the one worth a gate. It is the case where the addon's two
requirements genuinely conflict, and the difference between "flagged" and
"quietly shortened to something that will not cut" is a scrapped part.

Teardown is mandatory and the ordering matters -- see `close_everything`.

    freecad tests/gui/test_lead_in_audit.py
"""
import contextlib
import math
import os
import sys
import traceback

_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H

H.add_freecad_to_path()

STATUS = "lead_in_audit"

KERF = 2.0
HOLE_CX, HOLE_CY = 60.0, 60.0
PLATE_W = PLATE_H = 120.0
DEFAULT_MULTIPLIER = 3.0

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


def dumbbell(cx, cy, end_radius=6.0, neck_half=3.25, span=12.0):
    """Build a two-lobed face: fat ends joined by a narrower neck.

    **Non-convex on purpose.** A round hole and a rectangular slot are both
    convex, and on a convex contour the inward normal cannot leave the waste from
    any start point -- so the audit skips relocation there and the search would
    never be exercised.

    `neck_half` was measured, not guessed. With the start point on the neck's
    lower edge, a 2 mm kerf, and the operator's x3 (a 6 mm lead-in):

        neck_half   x3 in waste   x2 in waste
          2.00           no           no     -- too tight for either
          2.50           no           no
          3.00           no          YES     <- usable
          3.50           no          YES     <- usable
          4.00          YES          YES     -- x3 fits, nothing to fix
          4.50          YES          YES

    Two earlier revisions got this wrong in opposite directions: half-width 2.5
    was so tight that x2 could not fix it either, and 4.5 was so roomy that the
    operation needed no fixing at all -- so the audit correctly did nothing and
    the relocation path was never taken. The test compares the two routes, which
    only works where both of them can succeed.

    At a lobe the path sits 5 mm from the centre, so even x3 lands inside the
    6 mm lobe. That contrast is the case for relocating rather than shortening.
    """
    import FreeCAD as App
    import Part

    lobes = [Part.Face(Part.Wire(Part.makeCircle(end_radius,
                                             App.Vector(cx - span, cy, 0)))),
             Part.Face(Part.Wire(Part.makeCircle(end_radius,
                                                 App.Vector(cx + span, cy, 0))))]
    band = Part.Face(Part.makePolygon([
        App.Vector(cx - span, cy - neck_half, 0),
        App.Vector(cx + span, cy - neck_half, 0),
        App.Vector(cx + span, cy + neck_half, 0),
        App.Vector(cx - span, cy + neck_half, 0),
        App.Vector(cx - span, cy - neck_half, 0)]))
    return band.fuse(lobes[0]).fuse(lobes[1])


def build_case(doc, tag, hole_dia=None, profile=None):
    """Create a Profile operation on an internal feature, with its dressup.

    `hole_dia` makes a round hole; `profile` overrides it with any face to cut
    out of the plate.

    Returns `(job, operation, dressup, wire)`: the first because the settings
    dialog needs one, the rest the shape `audit_leadin_pierces` takes.
    Built through the addon's own `create_profile_operation`, so the operation is
    the one FindProfiles would make rather than a stand-in.
    """
    import FreeCAD as App
    import Part
    import Path.Main.Job as PathJob
    from jetcutter_tools import commands as C

    corners = [App.Vector(0, 0, 0), App.Vector(PLATE_W, 0, 0),
               App.Vector(PLATE_W, PLATE_H, 0), App.Vector(0, PLATE_H, 0)]
    outer = Part.makePolygon([*corners, corners[0]])
    if profile is not None:
        shape = Part.Face(outer).cut(profile)
    else:
        radius = hole_dia / 2.0
        shape = Part.Face(outer).cut(
            Part.Face(Part.Wire(Part.makeCircle(radius,
                                                App.Vector(HOLE_CX, HOLE_CY, 0)))))

    obj = doc.addObject("Part::Feature", "plate_%s" % tag)
    obj.Shape = shape
    doc.recompute()
    job = PathJob.Create("Job_%s" % tag, [obj])
    job.Tools.Group[0].Tool.Diameter = KERF
    doc.recompute()
    clone = job.Model.Group[0]

    top = None
    for face in shape.Faces:
        u_min, u_max, v_min, v_max = face.ParameterRange
        if face.normalAt(u_min + (u_max - u_min) / 2.0,
                         v_min + (v_max - v_min) / 2.0).z > 0.99:
            top = face
            break
    if top is None:
        return None, None, None, None
    hash_to_index = {e.hashCode(): i for i, e in enumerate(shape.Edges)}
    outer_hash = top.OuterWire.hashCode()
    wire = None
    names = []
    for candidate in top.Wires:
        if candidate.hashCode() == outer_hash:
            continue
        wire = candidate
        names = ["Edge%d" % (hash_to_index[e.hashCode()] + 1)
                 for e in candidate.Edges if e.hashCode() in hash_to_index]
    if wire is None or not names:
        return None, None, None, None

    doc.openTransaction("case %s" % tag)
    operation = C.create_profile_operation(
        "P_%s" % tag, job, job.Tools.Group[0], names, clone,
        "Inside", "CCW", True, False, "Perpendicular", "Perpendicular",
        DEFAULT_MULTIPLIER,
    )
    doc.commitTransaction()
    if operation is None:
        return None, None, None, None
    doc.recompute()
    return job, operation, C.find_dressup_on(doc, operation), wire


def pierce_of(dressup):
    """Return the dressup's pierce point, or None."""
    from jetcutter_tools import lead_in_audit as A

    return A.pierce_point(dressup.Path)


def radius_of(dressup):
    """Return the pierce's radius from the hole centre.

    Only meaningful for the round fixtures -- a dumbbell is not centred and not
    circular, so its waste test uses `in_region` below.
    """
    point = pierce_of(dressup)
    if point is None:
        return None
    return math.hypot(point[0] - HOLE_CX, point[1] - HOLE_CY)


def in_region(dressup, region):
    """Whether the pierce is inside an arbitrary waste region."""
    from jetcutter_tools import lead_in_audit as A

    return A.is_in_waste(pierce_of(dressup), region)


def body(report):
    import FreeCAD as App
    from jetcutter_tools import commands as C
    from jetcutter_tools import lead_in_audit as A

    _stub_modal_dialogs()
    doc = App.newDocument("leadin_audit")

    # --- a big hole: nothing to do ---------------------------------------
    big_job, big_op, big_dressup, big_wire = build_case(doc, "big", 20.0)
    report.check(big_op is not None, "built a 20 mm hole case")
    report.check(big_job is not None, "and it has a Job to drive the dialog")
    if big_op is None:
        return
    before = radius_of(big_dressup)
    findings = C.audit_leadin_pierces([(big_op, big_dressup, big_wire)],
                                      side="Inside",
                                      configured=DEFAULT_MULTIPLIER)
    report.check_eq(len(findings), 1, "the audit reported one finding")
    report.check_eq(findings[0].status, A.CLEAN,
                    "a 20 mm hole with a 2 mm kerf is left clean")
    report.check_eq(findings[0].multiplier, DEFAULT_MULTIPLIER,
                    "and its multiplier is untouched at x%.1f" % DEFAULT_MULTIPLIER)
    report.check(radius_of(big_dressup) == before,
                 "the big hole's toolpath did not change")
    report.check(not big_dressup.Label.startswith(A.ONPART_PREFIX),
                 "and it is not marked")

    # --- a small hole with a window: shortened to the floor --------------
    # 7 mm, not 6. The pierce has to clear the waste edge by kerf/2 as well as
    # being inside it, which tightens the budget from D - kerf/2 to D - kerf and
    # so moves the smallest fixable hole from 6 mm to 7 mm at a 2 mm kerf.
    # Measured: D > 3 x kerf predicts every boundary in the sweep.
    _small_job, small_op, small_dressup, small_wire = build_case(doc, "small", 7.0)
    report.check(small_op is not None, "built a 7 mm hole case")
    if small_op is None:
        return
    was_outside = radius_of(small_dressup) > 3.0
    report.check(was_outside,
                 "at x%.1f the 7 mm hole's pierce starts on the part (r=%.4f)"
                 % (DEFAULT_MULTIPLIER, radius_of(small_dressup)))

    findings = C.audit_leadin_pierces([(small_op, small_dressup, small_wire)],
                                      side="Inside",
                                      configured=DEFAULT_MULTIPLIER)
    report.check_eq(findings[0].status, A.CLEAN,
                    "the small hole now reads clean")
    report.check_eq(findings[0].multiplier, A.MINIMUM_MULTIPLIER,
                    "because its lead-in was shortened to x%.1f"
                    % A.MINIMUM_MULTIPLIER)
    report.check(radius_of(small_dressup) < 3.0,
                 "and its pierce now lands inside the hole (r=%.4f)"
                 % radius_of(small_dressup))
    report.check("shortened" in findings[0].detail,
                 "and the report says it was shortened, not changed silently")
    report.check(not small_dressup.Label.startswith(A.ONPART_PREFIX),
                 "a corrected operation is not marked as needing a human")
    # The guard behind the whole cost argument, on a *second* identical hole with
    # relocation explicitly allowed. The first one cannot show it: the opt-in
    # gate fires before the convexity test, so a disallowed search tells you
    # nothing about the guard. Here the operator has said yes and the only
    # reason left not to search is that the contour is convex -- where the
    # inward normal cannot leave the waste from any position.
    _cg_job, guard_op, guard_dressup, guard_wire = build_case(doc, "convex", 7.0)
    report.check(guard_op is not None, "built a second 7 mm hole for the guard")
    if guard_op is not None:
        C.audit_leadin_pierces([(guard_op, guard_dressup, guard_wire)],
                               side="Inside", configured=DEFAULT_MULTIPLIER,
                               allow_start_move=True)
        report.check(not getattr(guard_op, "UseStartPoint", False),
                     "with relocation allowed, a convex hole is still not "
                     "searched -- nothing was left to try that could work")
    report.check_eq(small_dressup.RadiusIn.Value, A.MINIMUM_MULTIPLIER * KERF,
                    "RadiusIn is now %.2f mm (x%.1f x %.1f mm kerf)"
                    % (A.MINIMUM_MULTIPLIER * KERF, A.MINIMUM_MULTIPLIER, KERF))

    # --- a hole the edge-clearance rule newly rejects ---------------------
    # This one shortened to x2 cleanly before the margin existed. At x2 its
    # pierce centre sits 0.997 mm from the hole edge and the pierce hole's own
    # radius is 1 mm, so it exactly reaches the finished edge. Requiring
    # kerf/2 of clearance misses it by 0.003 mm and it now flags.
    _mg_job, margin_op, margin_dressup, margin_wire = build_case(doc, "margin", 6.0)
    report.check(margin_op is not None, "built a 6 mm hole for the margin rule")
    if margin_op is not None:
        margin_region = C.waste_region_from_wire(margin_wire)
        findings = C.audit_leadin_pierces(
            [(margin_op, margin_dressup, margin_wire)], side="Inside",
            configured=DEFAULT_MULTIPLIER)
        report.check_eq(findings[0].status, A.ON_PART,
                        "a 6 mm hole now flags: x2 would put the pierce hole "
                        "against the finished edge")
        report.check(margin_dressup.Label.startswith(A.ONPART_PREFIX),
                     "and is marked rather than given an unsafe lead-in")
        with contextlib.suppress(Exception):
            margin_dressup.recompute()
        clearance = A.pierce_clearance(pierce_of(margin_dressup), margin_region)
        # At the configured length, which is where the audit left it: it tried
        # x2, found the clearance still short, and restored. At x2 this hole's
        # pierce sits 0.997 mm in against a 1.0 mm requirement.
        report.emit("    with its length restored, the pierce clearance is "
                    "%.4f mm against a %.4f mm requirement"
                    % (clearance if clearance is not None else float("nan"),
                       KERF / 2.0))

    # --- a hole with no window at all: flagged, and still left cutting -----
    # 5 mm hole, 2 mm kerf: the waste offers 4 mm and the arc needs 4 mm. There
    # is no lead-in that satisfies both, and which way to resolve that is the
    # operator's call -- a smaller nozzle, a different process, or accepting the
    # pierce on the part.
    _tight_job, tight_op, tight_dressup, tight_wire = build_case(doc, "tight", 5.0)
    report.check(tight_op is not None, "built a 5 mm hole case")
    if tight_op is None:
        return
    length_before = tight_dressup.RadiusIn.Value

    findings = C.audit_leadin_pierces([(tight_op, tight_dressup, tight_wire)],
                                      side="Inside",
                                      configured=DEFAULT_MULTIPLIER)
    report.check_eq(findings[0].status, A.ON_PART,
                    "the 5 mm hole has no window and is reported on the part")
    report.check_eq(findings[0].action, A.ACTION_FLAG,
                    "and is flagged rather than fixed")
    report.check(tight_dressup.Label.startswith(A.ONPART_PREFIX),
                 "the dressup is marked %r in the tree" % A.ONPART_PREFIX)
    report.check(A.PROP_ONPART in tight_dressup.PropertiesList,
                 "and carries a %s property saying why" % A.PROP_ONPART)
    report.check_eq(tight_dressup.RadiusIn.Value, length_before,
                    "its lead-in length is back to %.2f mm -- a trial shortening "
                    "is restored rather than left behind on a flag" % length_before)
    report.check(tight_dressup.LeadIn is True,
                 "and it still has a lead-in: it was not disabled behind the "
                 "operator's back")

    # --- Side=Outside is corrected by InvertIn, not by shortening ----------
    _out_job, outside_op, outside_dressup, outside_wire = build_case(doc, "out", 20.0)
    report.check(outside_op is not None, "built a Side=Outside case")
    if outside_op is not None:
        outside_op.Side = "Outside"
        outside_op.recompute()
        outside_dressup.recompute()
        was_outside = radius_of(outside_dressup) > 10.0
        report.check(was_outside,
                     "Side=Outside pierces away from the hole (r=%.4f)"
                     % radius_of(outside_dressup))
        findings = C.audit_leadin_pierces(
            [(outside_op, outside_dressup, outside_wire)], side="Outside",
            configured=DEFAULT_MULTIPLIER)
        report.check_eq(findings[0].status, A.CLEAN,
                        "flipping InvertIn corrects it without shortening")
        report.check(outside_dressup.InvertIn is True, "InvertIn is now set")
        report.check_eq(outside_dressup.RadiusIn.Value,
                        DEFAULT_MULTIPLIER * KERF,
                        "and the multiplier is untouched at x%.1f, because "
                        "flipping was enough" % DEFAULT_MULTIPLIER)
        report.check(radius_of(outside_dressup) < 10.0,
                     "the pierce is now inside the hole (r=%.4f)"
                     % radius_of(outside_dressup))

    # --- relocation, on a contour where it can actually help --------------
    # The case the whole placement search exists for: a dumbbell whose neck is
    # too narrow for the operator's x3 but whose ends have room to spare. What
    # matters is not that it gets fixed -- the length step would fix that too --
    # but *how*: relocation keeps their x3, shortening spends it.
    _db_job, dumb_op, dumb_dressup, dumb_wire = build_case(
        doc, "dumb", profile=dumbbell(HOLE_CX, HOLE_CY))
    report.check(dumb_op is not None, "built a dumbbell case")
    if dumb_op is not None:
        region = C.waste_region_from_wire(dumb_wire)
        # Put the cut's start on the neck explicitly. Where the contour's first
        # vertex happens to fall is not something a test should depend on, and
        # with roomy lobes it can land somewhere that needs no fixing at all --
        # in which case the audit correctly does nothing and the relocation path
        # is never taken.
        C.apply_start_point(dumb_op, dumb_dressup, (HOLE_CX, HOLE_CY - 3.25))
        report.check(not in_region(dumb_dressup, region),
                     "a start point on the neck pierces onto the part: %s"
                     % (pierce_of(dumb_dressup),))
        report.check(C.LeadInAudit.is_convex(region) is False,
                     "the dumbbell is non-convex, so relocation is not skipped")

        findings = C.audit_leadin_pierces(
            [(dumb_op, dumb_dressup, dumb_wire)], side="Inside",
            configured=DEFAULT_MULTIPLIER, allow_start_move=True)
        report.check_eq(findings[0].status, A.CLEAN,
                        "relocation puts the pierce in the waste")
        report.check_eq(findings[0].action, A.ACTION_SEARCH,
                        "and it did so by moving the start point")
        report.check_eq(dumb_dressup.RadiusIn.Value, DEFAULT_MULTIPLIER * KERF,
                        "with the operator's full x%.1f length kept -- the "
                        "arc-stabilisation margin is not spent"
                        % DEFAULT_MULTIPLIER)
        report.check(in_region(dumb_dressup, region),
                     "and the pierce now lands in waste: %s"
                     % (pierce_of(dumb_dressup),))
        report.check("moved" in findings[0].detail,
                     "and the report says where it moved it")
        dumb_op.recompute()
        dumb_dressup.recompute()
        report.check(dumb_op.UseStartPoint is True,
                     "UseStartPoint is set -- without it the point is discarded")

    # --- the same case with relocation not permitted ----------------------
    # Opt-in means opt-in: with it off, the search must not happen at all and
    # the operation must end up shortened rather than relocated.
    _do_job, opt_op, opt_dressup, opt_wire = build_case(
        doc, "dumbopt", profile=dumbbell(HOLE_CX, HOLE_CY))
    report.check(opt_op is not None, "built a second dumbbell for the opt-out")
    if opt_op is not None:
        C.apply_start_point(opt_op, opt_dressup, (HOLE_CX, HOLE_CY - 3.25))
        # Compared rather than merely asserted absent: this test set the start
        # itself to put the operation in a tight spot, so "UseStartPoint is
        # false" would fail for the test's own doing and prove nothing.
        start_before = tuple(opt_op.StartPoint)
        report.check(not in_region(opt_dressup,
                                   C.waste_region_from_wire(opt_wire)),
                     "the opt-out case starts on the neck too, so it also "
                     "pierces onto the part")
        findings = C.audit_leadin_pierces(
            [(opt_op, opt_dressup, opt_wire)], side="Inside",
            configured=DEFAULT_MULTIPLIER, allow_start_move=False)
        report.check_eq(findings[0].status, A.CLEAN,
                        "it is still fixed with relocation off")
        report.check_eq(findings[0].action, A.ACTION_SHORTEN,
                        "by shortening instead of moving the cut's start")
        report.check_eq(opt_dressup.RadiusIn.Value, A.MINIMUM_MULTIPLIER * KERF,
                        "down to the x%.1f floor" % A.MINIMUM_MULTIPLIER)
        report.check(tuple(opt_op.StartPoint) == start_before,
                     "and its start point is where the test put it, unmoved")

    # --- the dialog will not offer a lead-in that cannot stabilise an arc ---
    # The floor is asserted in the fast tier, but that only proves the constant.
    # This proves the dialog *uses* it, which is the part that can regress: the
    # range used to start at 0.01, offering a 0.06 mm lead-in on a 6 mm kerf.
    # Resolved exactly as `show_profile_settings_dialog` resolves it. The PySide
    # compatibility shim exposes widgets on `QtGui`, whereas PySide6 proper does
    # not, so reaching for PySide6 here would spy on a class the dialog never
    # constructs -- and would have passed while testing nothing.
    try:
        from PySide import QtGui as _QtGui
    except ImportError:
        from PySide6 import QtGui as _QtGui

    seen = []
    original_set_range = _QtGui.QDoubleSpinBox.setRange
    original_exec = _QtGui.QDialog.exec

    def _spy_range(self, low, high):
        seen.append((low, high))
        return original_set_range(self, low, high)

    try:
        _QtGui.QDoubleSpinBox.setRange = _spy_range
        _QtGui.QDialog.exec = lambda self: _QtGui.QDialog.Rejected
        result = C.show_profile_settings_dialog(big_job)
    finally:
        _QtGui.QDoubleSpinBox.setRange = original_set_range
        _QtGui.QDialog.exec = original_exec

    report.check(result is None,
                 "the dialog ran and returned None when rejected")
    report.check(seen, "and configured at least one spin box (saw %s)" % (seen,))
    # Asserted positively, as an exact pair. The dialog has more than one spin
    # box sharing the 100.0 ceiling -- the concave-depth tolerance is (0.1, 100)
    # and is legitimately allowed small values -- so a ceiling is not enough to
    # identify the length box, and a negative "nothing is below the floor" would
    # be false for that one. Regressing the floor to 0.01 makes the exact pair
    # disappear, which is the property that matters.
    report.check((A.MINIMUM_MULTIPLIER, 100.0) in seen,
                 "the length box's range is (%.1f, 100.0) -- the floor is the "
                 "arc-stabilisation one (saw %s)"
                 % (A.MINIMUM_MULTIPLIER, seen))


if __name__ in ("__main__", "test_lead_in_audit"):
    _code = 1
    try:
        _code = H.run(STATUS, body)
    except Exception:
        traceback.print_exc()
        H.write_status(STATUS, 1)
    finally:
        close_everything()
