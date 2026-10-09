"""GUI-tier checks: the dressup path, which `freecadcmd` cannot reach.

Under `freecadcmd`, `obj.ViewObject` is `None` (`App.GuiUp == 0`), so
`LeadInOutDressup.Create` bails at its ViewProvider step. That blind spot is
where ISS-010's original, wrong conclusion came from. These checks run on the
GUI binary, which gives a real ViewObject and a live `FreeCADGui`.

Why this tier exists
--------------------
The `freecadcmd` tier established the transaction behaviour by *simulating* the
dressup's transaction pattern, and got it wrong. Running the real code is the
only way to settle it. See issues.md -> CR-10.

Teardown is not optional
------------------------
The GUI binary starts an event loop and will not exit on its own; leaving it
open hangs the process. Every exit path therefore closes documents, closes the
main window and quits the application — in a `finally`, so an exception still
ends the run. Ending with `sys.exit()` alone is not enough.

    freecad tests/gui/test_dressup_transaction.py
"""
import os
import sys
import traceback

_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
# One shared harness for both FreeCAD tiers; a copy per tier would drift.
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H

H.add_freecad_to_path()

STATUS = "dressup_transaction"

_QT_STUBBED = []


def _stub_modal_dialogs():
    """Make modal dialogs return immediately.

    `LeadInOutDressup.Create` ends with `obj.ViewObject.Document.setEdit(...)`,
    which would open the task panel and block. The addon passes `mode=2` to skip
    that, but any other modal in the path would still block, and a gate that
    hangs is worse than one that fails.
    """
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

    **Documents first.** Measured on this binary (rc taken from FreeCAD itself,
    not from a pipeline):

        closeDocument, then window.close()   rc 0
        window.close(), then closeDocument   rc 124 -- hangs

    So closing documents before the window is not tidiness, it is what lets the
    session end. A gate that hangs is worse than one that fails.

    `closeDocument` also **discards**: it never prompts, so the Unsaved Document
    dialogue does not appear and nothing is written to disk. That is why the
    test document is closed explicitly rather than left to the exit path, and
    why nothing here calls `doc.save()`.

    **Then the window.** Ending the script with `sys.exit()` alone leaves the
    main window on screen: the process is gone but the window still has to be
    dismissed by hand. `getMainWindow().close()` with `QApplication.quit()` ends
    the session by itself.

    **One piece of shutdown noise is expected here and is not a failure.** A
    CAM job registers a tool bar whose teardown reaches for its view provider,
    and by then the job is gone:

        ReferenceError: Cannot access attribute 'ViewObject' of deleted object

    It arrives after the checks and after the status file is written, and the
    exit code is still 0. It cannot be avoided by reordering — reordering is
    what causes the hang — so it is left alone and named here so nobody reads a
    green gate plus a traceback as a broken one.
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


def build_plate_job(doc, name="plate"):
    """A 60x40x10 plate inside a CAM Job, with its Model clone resolved."""
    import Part
    import Path.Main.Job as PathJob

    box = doc.addObject("Part::Feature", name)
    box.Shape = Part.makeBox(60, 40, 10)
    doc.recompute()
    job = PathJob.Create("Job", [box])
    clone = job.Model.Group[0]
    return job, clone


def top_face_index(shape):
    """Index (1-based) of the upward-facing planar face."""
    for i, face in enumerate(shape.Faces):
        u0, u1, v0, v1 = face.ParameterRange
        normal = face.normalAt(u0 + (u1 - u0) / 2, v0 + (v1 - v0) / 2)
        if normal.z > 0.99:
            return i + 1
    raise AssertionError("no top-facing planar face on the test plate")


def body(report):
    import FreeCAD as App
    import Path.Dressup.Gui.LeadInOut as L
    import Path.Op.Profile as P

    report.check_eq(App.GuiUp, 1, "running on the GUI binary (GuiUp == 1)")

    doc = App.newDocument("jetcutter_gui_test")
    job, clone = build_plate_job(doc)
    face = top_face_index(doc.getObject("plate").Shape)

    op = P.Create("Profile_Loop_1", parentJob=job)
    op.Base = [(clone, ["Face%d" % face])]
    doc.recompute()

    # --- 1. the environment this tier exists to provide ---------------------
    report.check(op.ViewObject is not None, "Profile op has a real ViewObject")
    report.check(op.isDerivedFrom("Path::Feature"), "op is a Path::Feature")

    # --- 2. the dressup actually completes here ----------------------------
    # A transaction only appears in UndoNames once it has actually changed
    # something, so open it and then make the change it is meant to cover.
    doc.openTransaction("Create Profile Loops")
    doc.addObject("Part::Feature", "Inside")
    doc.recompute()
    report.check("Create Profile Loops" in doc.UndoNames,
                 "outer transaction is open and registered")

    _stub_modal_dialogs()
    dressup = L.Create(op, mode=2)
    doc.recompute()

    report.check(dressup is not None, "LeadInOutDressup.Create() returned an object")
    report.check(dressup.ViewObject is not None, "dressup has a real ViewObject")
    report.emit("    LeadIn=%r StyleIn=%r RadiusIn=%r"
                % (dressup.LeadIn, dressup.StyleIn, getattr(dressup, "RadiusIn", None)))

    # --- 3. does the dressup's commit steal the outer transaction? ---------
    # This is the claim ISS-010 originally made and then retracted. Settle it
    # against the real code rather than a simulation.
    report.check("Create Profile Loops" in doc.UndoNames,
                 "outer transaction SURVIVES the dressup's nested commit")
    report.emit("    UndoNames=%r" % (doc.UndoNames,))

    # --- 4. what an abort actually rolls back ------------------------------
    # Measured, not assumed. abortTransaction acts on the innermost OPEN
    # transaction. The dressup's inner transaction is committed by then, so the
    # outer one is innermost — but the objects it reverts are only those the
    # transaction recorded, and a Profile op created *before* the transaction
    # opened is not among them.
    before_abort = sorted(o.Name for o in doc.Objects)
    report.emit("    before abort: %r" % (before_abort,))
    doc.abortTransaction()
    doc.recompute()
    names_after_abort = sorted(o.Name for o in doc.Objects)
    report.emit("    after abort:  %r" % (names_after_abort,))
    removed = sorted(set(before_abort) - set(names_after_abort))
    report.emit("    abort removed: %r" % (removed,))

    report.check(removed == [],
                 "abort reverted nothing once a nested commit had occurred")
    report.check("Inside" in names_after_abort,
                 "the object created inside the transaction SURVIVED the abort")
    report.check("DressupLeadInOut" in names_after_abort,
                 "the dressup survives — its own commit banked it")
    report.check("Profile_Loop_1" in names_after_abort,
                 "the op created before the transaction is untouched, as expected")
    report.emit("    -> measured: after the dressup's nested commit, abortTransaction")
    report.emit("       reverts NOTHING. An abort in an except-handler would be a")
    report.emit("       no-op. This is the CR-09 finding: use per-operation")
    report.emit("       transactions plus undo(), never abort.")

    # --- 5. undo() peels exactly one transaction per call ------------------
    doc.openTransaction("Outer")
    doc.addObject("Part::Feature", "U1")
    doc.recompute()
    doc.openTransaction("Inner")
    doc.addObject("Part::Feature", "U2")
    doc.recompute()
    doc.commitTransaction()
    doc.addObject("Part::Feature", "U3")   # created outside any transaction
    doc.recompute()
    report.emit("    UndoCount=%r names=%r" % (doc.UndoCount, doc.UndoNames))

    doc.undo()
    report.check("U2" not in [o.Name for o in doc.Objects],
                 "one undo peels the committed inner transaction (U2 gone)")
    doc.undo()
    report.check("U1" not in [o.Name for o in doc.Objects],
                 "second undo peels the outer transaction (U1 gone)")
    report.check("U3" in [o.Name for o in doc.Objects],
                 "U3 survives both undos — it was never in a transaction")
    report.emit("    this is the gap CR-09's per-operation transactions close")

    # --- 6. the StyleIn the dialog defaults to vs what FreeCAD gives -------
    # Recorded as an observation for ISS-033/L7 rather than an assertion: the
    # addon sets StyleIn explicitly after Create(), so this is FreeCAD's own
    # default, not the addon's behaviour.
    report.emit("    note: FreeCAD's default StyleIn is %r; commands.py then sets "
                "StyleIn/StyleOut explicitly from the dialog" % (dressup.StyleIn,))


if __name__ in ("__main__", "test_dressup_transaction"):
    _code = 1
    try:
        _code = H.run(STATUS, body)
    except Exception:
        traceback.print_exc()
        H.write_status(STATUS, 1)
    finally:
        close_everything()
