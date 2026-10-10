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


def build_case(doc, tag, hole_dia):
    """Create a Profile operation on a hole of `hole_dia`, with its dressup.

    Returns `(job, operation, dressup, wire)`: the first because the settings
    dialog needs one, the rest the shape `audit_leadin_pierces` takes.
    Built through the addon's own `create_profile_operation`, so the operation is
    the one FindProfiles would make rather than a stand-in.
    """
    import FreeCAD as App
    import Part
    import Path.Main.Job as PathJob
    from jetcutter_tools import commands as C

    radius = hole_dia / 2.0
    corners = [App.Vector(0, 0, 0), App.Vector(PLATE_W, 0, 0),
               App.Vector(PLATE_W, PLATE_H, 0), App.Vector(0, PLATE_H, 0)]
    outer = Part.makePolygon([*corners, corners[0]])
    circle = Part.Wire(Part.makeCircle(radius, App.Vector(HOLE_CX, HOLE_CY, 0)))
    shape = Part.Face(outer).cut(Part.Face(circle))

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


def radius_of(dressup):
    from jetcutter_tools import lead_in_audit as A

    point = A.pierce_point(dressup.Path)
    if point is None:
        return None
    return math.hypot(point[0] - HOLE_CX, point[1] - HOLE_CY)


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
    # Measured on the probe: at x3 the pierce lands outside a 6 mm hole, while
    # x2 fits inside it. So this one must be corrected, not merely reported.
    _small_job, small_op, small_dressup, small_wire = build_case(doc, "small", 6.0)
    report.check(small_op is not None, "built a 6 mm hole case")
    if small_op is None:
        return
    was_outside = radius_of(small_dressup) > 3.0
    report.check(was_outside,
                 "at x%.1f the 6 mm hole's pierce starts on the part (r=%.4f)"
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
    report.check_eq(small_dressup.RadiusIn.Value, A.MINIMUM_MULTIPLIER * KERF,
                    "RadiusIn is now %.2f mm (x%.1f x %.1f mm kerf)"
                    % (A.MINIMUM_MULTIPLIER * KERF, A.MINIMUM_MULTIPLIER, KERF))

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
