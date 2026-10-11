"""GUI tier: the Slice Model command's gating and its visibility side effects.

These two behaviours are only observable with a live view layer, which is why
they are here and not in `tests/freecadcmd/test_slicing.py`:

  * the toolbar button must grey out until a usable sketch is selected;
  * the Slice feature itself must end up hidden, because the chunks are filters
    reading `Base=Slice` and a visible parent re-renders the unsplit panel.

Teardown is mandatory and the ordering matters -- see `close_everything`.

    freecad tests/gui/test_slice_model.py
"""
import os
import sys

_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H

H.add_freecad_to_path()

STATUS = "slice_model"

_KERF = 2.0


def close_everything():
    """Discard every document, then shut the GUI down, in that order.

    Closing the window first hangs (rc 124). `closeDocument` discards, so
    nothing is written to disk.
    """
    import FreeCAD as App
    import FreeCADGui
    from PySide6 import QtWidgets

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


def build_panel(doc, name="Plate", thickness=10.0):
    """Build a plate with an attached sketch carrying one line across it."""
    import FreeCAD as App
    import Part

    plate = doc.addObject("Part::Box", name)
    plate.Length, plate.Width, plate.Height = 800.0, 400.0, thickness
    doc.recompute()

    # Face6 is the +Z face of a Part::Box, so the sketch lands on the top. The
    # line runs corner to corner across the plate: at x=400 in a plate spanning
    # x 0..800, and y 0..400, which is a full crossing.
    sketch = doc.addObject("Sketcher::SketchObject", "Sk")
    sketch.AttachmentSupport = [(plate, "Face6")]
    sketch.MapMode = "FlatFace"
    sketch.addGeometry(
        Part.LineSegment(App.Vector(400, 0, 0), App.Vector(400, 400, 0)), False
    )
    doc.recompute()
    return plate, sketch


def selection_of(*names):
    import FreeCADGui as Gui

    Gui.Selection.clearSelection()
    for name in names:
        Gui.Selection.addSelection(name[0], name[1])


def body(report):
    import FreeCAD as App
    from jetcutter_tools import commands as C

    doc = App.newDocument("slice_gui")
    plate, _sketch = build_panel(doc)
    command = C.SliceModel()

    # -- the button greys out until a usable sketch is selected ------------
    report.check(
        hasattr(command, "IsActive"),
        "the command exposes IsActive, which is the name FreeCAD looks up",
    )
    report.check(
        not hasattr(command, "IsEnabled"),
        "and no IsEnabled, which FreeCAD never calls -- Gui's "
        "PythonCommand::isActive() looks up IsActive by name only",
    )

    selection_of()
    report.check(command.IsActive() is False,
                 "nothing selected: the command is inactive")
    selection_of((doc.Name, "Plate"))
    report.check(command.IsActive() is False,
                 "a solid selected: inactive, it is not a sketch")
    selection_of((doc.Name, "Sk"))
    report.check(command.IsActive() is True,
                 "one attached sketch selected: active")
    selection_of((doc.Name, "Sk"), (doc.Name, "Plate"))
    report.check(command.IsActive() is False,
                 "sketch and solid both selected: inactive, the target is ambiguous")

    import Part

    loose = doc.addObject("Sketcher::SketchObject", "Loose")
    loose.addGeometry(
        Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), False
    )
    doc.recompute()
    selection_of((doc.Name, "Loose"))
    report.check(command.IsActive() is False,
                 "an unattached sketch is inactive, since there is no model to slice")

    # IsActive must hand back a real bool, not a truthy value: the C++ side
    # rejects anything that is not Py_True, so a returned non-bool reads False.
    selection_of((doc.Name, "Sk"))
    report.check(type(command.IsActive()) is bool,
                 "IsActive returns a real bool, not a truthy value")

    # -- the other two commands have the same naming contract -------------
    for cls in (C.SameEdgesAsHighlighted, C.FindProfiles):
        report.check(
            hasattr(cls, "IsActive") and not hasattr(cls, "IsEnabled"),
            "%s uses IsActive like SliceModel" % cls.__name__,
        )
        try:
            report.check(type(cls().IsActive()) is bool,
                         "%s.IsActive returns a real bool" % cls.__name__)
        except Exception as exc:
            report.check(False, "%s.IsActive raised: %s" % (cls.__name__, exc))

    # -- run it for real and check what ends up visible --------------------
    selection_of((doc.Name, "Sk"))
    command.Activated()

    groups = [o for o in doc.Objects if o.TypeId == "App::DocumentObjectGroup"
              and o.Label.startswith("Exploded")]
    report.check_eq(len(groups), 1, "the command produced one Exploded group")

    chunk_names = [c.Name for c in groups[0].Group] if groups else []
    report.check_eq(len(chunk_names), 2, "a single line splits the plate in two")

    report.check(
        plate.ViewObject.Visibility is False,
        "the model is hidden afterwards",
    )
    extrusions = [o for o in doc.Objects if o.TypeId == "Part::Extrusion"]
    report.check_eq(len(extrusions), 1, "one extrusion was made")
    if extrusions:
        report.check(
            extrusions[0].ViewObject.Visibility is False,
            "the extrusion is hidden afterwards",
        )

    # The exploded chunks are also Part::FeaturePython and are named Slice.0,
    # Slice.1 -- so match on the feature's own name, which is bare "Slice",
    # rather than a prefix that would sweep the chunks in too.
    slices = [o for o in doc.Objects if o.Name == "Slice"]
    report.check_eq(len(slices), 1, "one Slice feature was made")
    if slices:
        report.check(
            slices[0].ViewObject.Visibility is False,
            "the Slice feature itself is hidden -- the chunks read Base=Slice, so "
            "leaving it visible re-renders the whole unsplit panel",
        )
        sources = {dep.Name for c in groups[0].Group
                   for dep in c.OutListRecursive}
        report.check(
            "Slice" in sources,
            "and it really is what the chunks depend on: their OutList reaches "
            "the Slice feature, so leaving it visible re-renders the panel",
        )
        for chunk in groups[0].Group:
            report.check(
                chunk.ViewObject.Visibility is True,
                "the chunk %s stays visible" % chunk.Label,
            )

    # -- nothing was deleted ---------------------------------------------
    report.check(
        plate is not None and extrusions and slices,
        "the model, extrusion and Slice all still exist, hidden rather than removed",
    )
    report.check(
        len(App.listDocuments()) == 1,
        "and they are all in the one document, still undoable together",
    )


if __name__ in ("__main__", "test_slice_model"):
    try:
        H.run(STATUS, body)
    finally:
        close_everything()
