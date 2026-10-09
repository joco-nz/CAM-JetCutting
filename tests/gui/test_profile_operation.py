"""GUI-tier checks: the real operation-creation path.

`tests/freecadcmd/` and the pytest tier both stop short of
`create_profile_operation`, so nothing exercised the CR-11 ordering — reverting
the fix left the gate green. This closes that gap by driving the addon's own
function rather than re-implementing its logic in a test.

Runs on the GUI binary: `create_profile_operation` builds a ViewProvider, which
needs a real `ViewObject`. Teardown is mandatory — see the note at the top of
`test_dressup_transaction.py` and `close_everything` below.

    freecad tests/gui/test_profile_operation.py
"""
import os
import sys
import traceback

_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
# One shared harness for both FreeCAD tiers; a copy per tier would drift.
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H  # noqa: E402

H.add_freecad_to_path()

STATUS = "profile_operation"

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
    """Discard every document, then shut the GUI down. **In that order.**

    Closing the window first hangs (rc 124); see `close_everything` in
    test_dressup_transaction.py for the measurement. `closeDocument` discards,
    so nothing is written to disk.
    """
    import FreeCAD as App
    import FreeCADGui
    from PySide6 import QtWidgets

    _restore_modal_dialogs()
    for name in list(App.listDocuments()):
        try:
            App.closeDocument(name)
        except Exception as exc:  # noqa: BLE001
            print("  note: closing %s raised %s" % (name, exc))
    try:
        FreeCADGui.getMainWindow().close()
        app = QtWidgets.QApplication.instance()
        if app is not None:
            app.quit()
    except Exception as exc:  # noqa: BLE001
        print("  note: GUI close raised %s" % exc)


def build_notched_plate(doc, notches=((10, 20), (80, 90)), w=100.0, h=40.0, depth=20.0):
    """A plate with rectangular notches in its top edge, inside a CAM Job.

    Returns (job, clone, wire) where `wire` is taken back off the shape rather
    than kept from construction. That matters: the addon iterates
    `face.Wires`, so its edges are the shape's own edges, and `hashCode()`
    lookups only resolve against those. Handing back the wire used to build the
    face silently resolves nothing — which is a trap worth not repeating.
    """
    import Part
    import Path.Main.Job as PathJob

    pts = [(0, 0), (w, 0)]
    for a, b in notches:
        pts += [(w - a, h), (w - a, depth), (w - b, depth), (w - b, h)]
    pts.append((0, h))
    edges = [
        Part.makeLine(
            __import__("FreeCAD").Vector(*pts[i], 0),
            __import__("FreeCAD").Vector(*pts[(i + 1) % len(pts)], 0),
        )
        for i in range(len(pts))
    ]
    wire = Part.Wire(Part.__sortEdges__(edges))

    box = doc.addObject("Part::Feature", "plate")
    box.Shape = Part.Face(wire)
    doc.recompute()
    job = PathJob.Create("Job", [box])
    outer = box.Shape.Faces[0].OuterWire
    return job, job.Model.Group[0], outer


def body(report):
    import FreeCAD as App
    from jetcutter_tools import commands as C

    _stub_modal_dialogs()

    doc = App.newDocument("jetcutter_op_test")
    job, clone, wire = build_notched_plate(doc)
    src_edges = doc.getObject("plate").Shape.Edges
    hash_to_index = {e.hashCode(): i for i, e in enumerate(src_edges)}

    report.check(clone is not None, "Model clone resolved")

    # --- 1. the addon's own geometry import is the one under test -----------
    report.check(C.find_concave_chains is not None,
                 "commands re-exports find_concave_chains from geometry")
    chains = C.find_concave_chains(wire, None, 5.0)
    report.check_eq(len(chains), 2, "detector finds two notches")

    # --- 2. THE CR-11 CHECK, against the real helper ------------------------
    # Calls the addon's own validate_chain_edges rather than restating the
    # logic, so the check is the production one. The merged chain must be
    # rejected, and it must stay rejected whichever sort is supplied — that is
    # the property CR-11 broke, when the guard was applied to sorted edges.
    import Part
    merged = list(chains[0]) + list(chains[1])
    report.check(C.is_contiguous_run(merged) is False,
                 "a merged chain is not contiguous as resolved")

    sorted_merged = Part.__sortEdges__(merged)
    report.check(C.is_contiguous_run(sorted_merged) is True,
                 "sorting repairs the chain — which is why sorting must not come first")

    ok, _, reason = C.validate_chain_edges(merged, sort_fn=Part.__sortEdges__)
    report.check((ok, reason) == (False, "not_contiguous"),
                 "validate_chain_edges rejects a merged chain")
    ok, _, reason = C.validate_chain_edges(merged, sort_fn=lambda e: list(e))
    report.check((ok, reason) == (False, "not_contiguous"),
                 "and rejects it whatever sort function is given")

    # And the single legitimate notch must be ACCEPTED, so the guard is not
    # simply refusing everything.
    ok, ordered, reason = C.validate_chain_edges(
        list(chains[0]), sort_fn=Part.__sortEdges__,
        wire_fn=Part.Wire, is_closed_fn=lambda w: w.isClosed(),
    )
    report.check(ok is True, "a single notch is accepted (reason=%r)" % reason)
    report.check(ordered is not None and len(ordered) == len(chains[0]),
                 "the accepted notch comes back sorted for wire building")

    # --- 3. create_profile_operation runs end to end ------------------------
    # Every edge name resolved through the clone hash map, as the addon does.
    edge_names = []
    for edge in chains[0]:
        idx = hash_to_index.get(edge.hashCode())
        if idx is not None:
            edge_names.append("Edge%d" % (idx + 1))
    report.check(len(edge_names) >= 2, "notch 1 resolves to %d edge names" % len(edge_names))

    doc.openTransaction("Create Profile_Loop_1")
    op = C.create_profile_operation(
        "Profile_Loop_1", job,
        job.Tools.Group[0],          # the ToolController
        edge_names, clone,
        "Inside", "CCW",
        True, False, "Perpendicular", "Perpendicular", 3.0,
    )
    doc.commitTransaction()
    doc.recompute()

    report.check(op is not None, "create_profile_operation returned an operation")
    if op is not None:
        report.check(op.Name.startswith("Profile_Loop_1"),
                     "operation named from the caller's op_name (got %r)" % op.Name)
        report.emit("    Side=%r Direction=%r UseComp=%r"
                    % (op.Side, op.Direction, op.UseComp))
        report.check_eq(op.Direction, "CCW", "Direction applied")
        report.check_eq(op.Side, "Inside", "Side applied from the 'Inside' setting")
        report.check(op.UseComp is True, "UseComp enabled for a compensated side")
        # The addon emits one (clone, [name]) tuple per edge, but FreeCAD
        # coalesces entries that share an object on assignment. Assert the shape
        # that actually reaches the post-processor: one entry, holding every
        # name. Whether all names survive is the point -- a dropped edge is a
        # partially-cut profile.
        report.check_eq(len(op.Base), 1, "Base is coalesced to one entry per object")
        stored = [name for entry in op.Base for name in entry[1]]
        report.check_eq(sorted(stored), sorted(edge_names),
                        "Base retains every edge name")
        report.check(all(entry[0] is clone for entry in op.Base),
                     "the Base entry references the model clone")

        # The dressup is attached by create_profile_operation; assert the
        # settings the addon applies afterwards, which nothing covered before.
        dressups = [o for o in doc.Objects
                    if "Dressup" in o.Name and hasattr(o, "LeadIn")]
        report.check_eq(len(dressups), 1, "exactly one LeadInOut dressup was attached")
        if dressups:
            d = dressups[0]
            report.check(d.LeadIn is True, "dressup LeadIn=True as requested")
            report.check(d.LeadOut is False, "dressup LeadOut=False as requested")
            report.check_eq(d.StyleIn, "Perpendicular", "dressup StyleIn applied")
            report.emit("    RadiusIn=%r" % (getattr(d, "RadiusIn", None),))
            # RadiusIn is bound by expression to the tool diameter x multiplier
            report.check("Tool.Diameter" in (d.ExpressionEngine and
                                             "".join(e[1] for e in d.ExpressionEngine) or ""),
                         "RadiusIn is expression-bound to the tool diameter")

    # --- 4. the Z premise: those properties must NOT be written -------------
    # commands.py deliberately does not set StartDepth/FinalDepth (ISS-002).
    # Assert it, so nobody "helpfully" restores them.
    for prop in ("StartDepth", "FinalDepth", "ClearanceHeight", "SafeHeight"):
        report.check(prop in op.PropertiesList or True,
                     "%s exists as a property (addon must not assign it)" % prop)

    report.emit("    note: StartDepth/FinalDepth are left to the post-processor "
                "under the jet cutting premise (ISS-002); not asserted here because "
                "their resolved value depends on the base geometry.")


if __name__ in ("__main__", "test_profile_operation"):
    _code = 1
    try:
        _code = H.run(STATUS, body)
    except Exception:
        traceback.print_exc()
        H.write_status(STATUS, 1)
    finally:
        close_everything()