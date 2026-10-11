"""End-to-end check: slicing a real panel reproduces the shop's manual result.

The test fixture is the shop's own `preslice` file, and the oracle is the
volumes in the `UnevenSplit` file that the panel was sliced into by hand with
Part > Extrude followed by Part > Split > Slice Apart. Four chunks, with known
volumes, in a known order.

Using the real file rather than a synthetic box is the point. A box cannot
reproduce the three failure modes that actually matter here, and two of them
were only found by running against it:

  * the panel is 2 mm thick and the sketch sits on the top face, so a
    one-sided extrusion points away from the material and cuts nothing;
  * the shape is 798 faces, so the slice takes seconds and a slow path is not
    a theoretical concern;
  * every exploded chunk inherits the *parent's* bounding box, so a 579 mm
    chunk reports 2397 mm and any bbox-based sanity check is worthless.

Runs under `freecadcmd`. Gated by `tests/run.sh`; writes
`tests/status/slicing.status`.

    freecadcmd tests/freecadcmd/test_slicing.py
"""
import os
import sys

_TIER_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_TIER_DIR))

import harness_common as H

H.add_freecad_to_path()

import FreeCAD as App
import Part
from jetcutter_tools import slicing as S

STATUS = "slicing"
FIXTURE = os.path.join(
    os.path.dirname(_TIER_DIR), "TestFiles",
    "floral-decorative-pattern-panel-wall-screen-preslice.FCStd",
)

# Volumes of Slice.0 .. Slice.3 in the shop's hand-sliced UnevenSplit file.
# Read off that document's Shape.Volume, in file order.
EXPECTED_VOLUMES = (915270.0, 949620.0, 968220.0, 940700.0)


def close_everything():
    for name in list(App.listDocuments()):
        with_suppress_close(name)


def with_suppress_close(name):
    try:
        App.closeDocument(name)
    except Exception:
        pass


def open_panel():
    if not os.path.exists(FIXTURE):
        return None
    return App.openDocument(FIXTURE)


def sketch_and_model(doc):
    sketch = doc.getObject("Sketch")
    return sketch, S.model_for_sketch(sketch)


def plate_with_line(doc, thickness, line_x, name="Plate", span=(-200.0, 600.0)):
    """Build a box with one slice line drawn across it.

    Cheaper than the real panel and it isolates the geometry the test is
    actually about: how far the extruded faces have to reach to cut a solid of
    a given thickness.
    """
    plate = doc.addObject("Part::Box", name)
    plate.Length, plate.Width, plate.Height = 1200.0, 800.0, thickness
    doc.recompute()

    sketch = doc.addObject("Sketcher::SketchObject", "Slice_" + name)
    sketch.Placement = App.Placement(App.Vector(0, 0, thickness), App.Rotation())
    sketch.addGeometry(
        Part.LineSegment(
            App.Vector(line_x, span[0], 0), App.Vector(line_x, span[1], 0)
        ),
        False,
    )
    doc.recompute()
    return plate, sketch


def body(report):
    doc = open_panel()
    if doc is None:
        report.check(False, "fixture %s is present" % os.path.basename(FIXTURE))
        return
    report.check(True, "fixture %s is present" % os.path.basename(FIXTURE))

    sketch, model = sketch_and_model(doc)

    # -- resolving the model from the sketch -----------------------------
    report.check(
        sketch.isDerivedFrom("Sketcher::SketchObject"),
        "the slice sketch is found and is a sketch",
    )
    report.check(
        [s[0].Name for s in sketch.AttachmentSupport] == ["BaseFeature"],
        "the sketch's attachment support names the model's base feature",
    )
    report.check(
        model.Name == "Body001",
        "resolution walks up to the owning PartDesign Body, not the bare "
        "feature: got %s" % model.Name,
    )
    report.check(
        model.Tip is not None and model.Tip.Name == "BaseFeature",
        "that body's tip is the feature the sketch is attached to",
    )
    report.check(
        abs(model.Shape.Volume - sketch.AttachmentSupport[0][0].Shape.Volume) < 1e-6,
        "body and feature carry identical geometry, so either slices the same",
    )

    # -- the depth must clear the model both ways ------------------------
    forward, reverse, normal = S.extrusion_depth(sketch, model)
    box = model.Shape.BoundBox
    report.emit(
        "    extrusion: normal=(%.0f,%.0f,%.0f) forward=%.2f reverse=%.2f "
        "over a %.1f mm panel" % (normal.x, normal.y, normal.z, forward, reverse,
                                  box.ZLength)
    )
    report.check(
        forward > 0 and reverse > 0,
        "both directions are positive, so the cut works from either face: "
        "forward=%.2f reverse=%.2f" % (forward, reverse),
    )

    # -- the whole thing -------------------------------------------------
    extrusion, feature, group, chunks = S.slice_model(doc, sketch, model)
    doc.recompute()

    report.check_eq(len(chunks), 4, "the panel splits into four chunks")
    report.check_eq(
        len(feature.Shape.Solids), 4, "the slice itself holds four solids"
    )
    report.check(
        all(len(c.Shape.Solids) == 1 for c in chunks),
        "every chunk is exactly one solid",
    )

    volumes = tuple(c.Shape.Volume for c in chunks)
    report.emit("    volumes: %s" % ", ".join("%.1f" % v for v in volumes))
    for got, want in zip(volumes, EXPECTED_VOLUMES, strict=True):
        report.check(
            abs(got - want) / want < 0.001,
            "chunk volume %.1f matches the hand-sliced file's %.1f (%.3f%% off)"
            % (got, want, abs(got - want) / want * 100),
        )

    total = sum(volumes)
    report.check(
        abs(total - model.Shape.Volume) / model.Shape.Volume < 1e-6,
        "chunk volumes sum to the source volume exactly: %.6f vs %.6f"
        % (total, model.Shape.Volume),
    )

    # -- the exploded chunks' bboxes are not to be trusted ---------------
    tight = [c.Shape.Solids[0].optimalBoundingBox(True).YLength for c in chunks]
    reported = [c.Shape.BoundBox.YLength for c in chunks]
    report.emit(
        "    tight heights: %s" % ", ".join("%.1f" % t for t in tight)
    )
    report.emit(
        "    reported heights: %s" % ", ".join("%.1f" % r for r in reported)
    )
    report.check(
        all(abs(r - box.YLength) < 1e-6 for r in reported),
        "every chunk reports the FULL panel height -- confirming a bbox check "
        "would be meaningless here",
    )
    report.check(
        all(t < box.YLength * 0.5 for t in tight),
        "while the true heights are all under half the panel, so the chunks "
        "really are separate pieces",
    )

    # -- degenerate detection -------------------------------------------
    report.check_eq(
        S.find_degenerate(feature.Shape.Solids), [],
        "no degenerate chunk is reported for a clean slice",
    )
    report.check_eq(
        S.find_degenerate(chunks), [],
        "and none when handed the exploded chunk objects instead of raw solids",
    )
    report.check_eq(
        len(S.find_degenerate(feature.Shape.Solids)),
        len(S.find_degenerate(chunks)),
        "both input kinds agree -- a raw Part.Solid has no .Shape, so an "
        "object-only check would call every good chunk empty",
    )

    # A genuine zero-volume piece must still be caught. This is the shape a
    # grazing slice line actually leaves behind: a face where a chunk should
    # be. Not a zero-thickness box -- OCC gives one of those a real volume
    # (measured: 200000.0 for a 400x50 box), so it would not be caught and the
    # check would pass for the wrong reason.
    sliver = Part.Face(Part.makePlane(400.0, 50.0))
    report.check(
        not sliver.Solids and sliver.Volume == 0.0,
        "the sliver really has no volume and no solid: vol=%r solids=%d"
        % (sliver.Volume, len(sliver.Solids)),
    )
    report.check_eq(
        len(S.find_degenerate(chunks + [sliver])), 1,
        "a zero-volume sliver is reported degenerate",
    )

    # -- the tree matches Slice Apart -----------------------------------
    report.check(
        group is not None and group.Label.startswith("Exploded"),
        "the chunks sit in an Exploded group: %s" % (group.Label if group else None),
    )
    report.check_eq(
        [c.Label for c in chunks],
        ["Slice.0", "Slice.1", "Slice.2", "Slice.3"],
        "chunks are named as Slice Apart names them",
    )
    report.check(
        extrusion.Solid is False,
        "the extrusion stays a face-maker, since open lines cannot make a solid",
    )

    # -- slicing at other thicknesses still cuts through -----------------
    for thickness in (2.0, 10.0, 25.0):
        plate, sk = plate_with_line(doc, thickness, 600.0, name="T%s" % thickness)
        ext = S.build_extrusion(doc, sk, plate)
        doc.recompute()
        eb = ext.Shape.BoundBox
        report.check(
            eb.ZMin < plate.Shape.BoundBox.ZMin
            and eb.ZMax > plate.Shape.BoundBox.ZMax,
            "a %.0f mm plate is cut through: faces z[%.1f..%.1f] vs plate "
            "z[%.1f..%.1f]" % (thickness, eb.ZMin, eb.ZMax,
                               plate.Shape.BoundBox.ZMin,
                               plate.Shape.BoundBox.ZMax),
        )

    # -- a sketch on a side face, not the top ----------------------------
    # The case that separates computing the depth along the sketch's own normal
    # from reading the bounding box's Z extent. For an axis-aligned top-face
    # sketch the two are the same number, so the panel above cannot tell them
    # apart -- but here the normal is horizontal, and the Z extent is the wrong
    # measurement entirely. Measured with the shortcut in place: reverse comes
    # out NEGATIVE (-19.0) and the faces span y[-401..1], missing the plate's
    # far side at y=20.
    side = doc.addObject("Part::Box", "SidePlate")
    side.Length, side.Width, side.Height = 300.0, 20.0, 400.0
    doc.recompute()
    side_sketch = doc.addObject("Sketcher::SketchObject", "SideSketch")
    side_sketch.Placement = App.Placement(
        App.Vector(0, 20, 0), App.Rotation(App.Vector(1, 0, 0), 90)
    )
    side_sketch.addGeometry(
        Part.LineSegment(App.Vector(0, 0, 200), App.Vector(300, 0, 200)), False
    )
    doc.recompute()

    fwd, rev, side_normal = S.extrusion_depth(side_sketch, side)
    report.emit(
        "    side face: normal=(%.0f,%.0f,%.0f) forward=%.1f reverse=%.1f"
        % (side_normal.x, side_normal.y, side_normal.z, fwd, rev)
    )
    report.check(
        fwd > 0 and rev > 0,
        "a side-face sketch still gets both depths positive: "
        "forward=%.1f reverse=%.1f" % (fwd, rev),
    )

    side_ext = S.build_extrusion(doc, side_sketch, side)
    doc.recompute()
    sb = side.Shape.BoundBox
    eb = side_ext.Shape.BoundBox
    report.check(
        eb.YMin < sb.YMin and eb.YMax > sb.YMax,
        "and cuts the 20 mm thickness, measured along Y not Z: faces "
        "y[%.1f..%.1f] vs plate y[%.1f..%.1f]"
        % (eb.YMin, eb.YMax, sb.YMin, sb.YMax),
    )

    # -- a fixed depth, as the manual process uses ------------------------
    # The shop's own file hard-codes Fwd=10 / Rev=40. That happens to clear a
    # 2 mm panel, so the volume checks above pass either way and cannot tell the
    # two apart -- but it is a fixed guess that only works for thin stock. On a
    # plate thicker than the guess, the faces stop short and the chunks stay
    # joined. Asserted against the derived depth rather than the outcome, so the
    # check fails on the arithmetic instead of waiting for a thick part to
    # appear in someone's workflow.
    thick = doc.addObject("Part::Box", "Thick")
    thick.Length, thick.Width, thick.Height = 1200.0, 800.0, 60.0
    doc.recompute()
    thick_sketch = doc.addObject("Sketcher::SketchObject", "ThickSketch")
    thick_sketch.Placement = App.Placement(App.Vector(0, 0, 60), App.Rotation())
    thick_sketch.addGeometry(
        Part.LineSegment(App.Vector(600, 0, 0), App.Vector(600, 800, 0)), False
    )
    doc.recompute()
    _tf, tr, _ = S.extrusion_depth(thick_sketch, thick)
    report.check(
        tr > thick.Shape.BoundBox.ZLength,
        "a 60 mm plate gets a reverse depth longer than the plate, which a "
        "fixed 40 mm guess would not: reverse=%.1f vs thickness=%.1f"
        % (tr, thick.Shape.BoundBox.ZLength),
    )
    thick_ext = S.build_extrusion(doc, thick_sketch, thick)
    doc.recompute()
    tb = thick.Shape.BoundBox
    eb = thick_ext.Shape.BoundBox
    report.check(
        eb.ZMin < tb.ZMin and eb.ZMax > tb.ZMax,
        "and 60 mm of stock is cut through: faces z[%.1f..%.1f] vs plate "
        "z[%.1f..%.1f]" % (eb.ZMin, eb.ZMax, tb.ZMin, tb.ZMax),
    )

    # -- a sketch that misses the model is refused -----------------------
    # Expected to be refused either way. Which check catches it depends on the
    # geometry: a line wholly outside gives no solids at all, while one that
    # clips an edge can give the untouched solid plus an empty sliver. Both are
    # refused; asserting on the message text would pin the accidental one.
    plate, sk = plate_with_line(doc, 10.0, 5000.0, name="Miss", span=(-200.0, 200.0))
    try:
        S.slice_model(doc, sk, plate)
        report.check(False, "a slice line missing the model is refused")
    except S.SlicingError as exc:
        report.check(
            "no solids" in str(exc).lower() or "empty piece" in str(exc).lower(),
            "a slice line missing the model is refused, and says why: %s" % exc,
        )

    # -- an unattached sketch is refused --------------------------------
    loose = doc.addObject("Sketcher::SketchObject", "Loose")
    loose.addGeometry(
        Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), False
    )
    doc.recompute()
    try:
        S.model_for_sketch(loose)
        report.check(False, "an unattached sketch is refused")
    except S.SlicingError as exc:
        report.check(
            "not attached" in str(exc),
            "an unattached sketch is refused, and says why: %s" % exc,
        )

    # -- a sketch attached to something without solids is refused -------
    empty_plane = doc.addObject("Part::Plane", "Plane")
    doc.recompute()
    on_plane = doc.addObject("Sketcher::SketchObject", "OnPlane")
    on_plane.AttachmentSupport = [(empty_plane, "")]
    on_plane.MapMode = "FlatFace"
    on_plane.addGeometry(
        Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)), False
    )
    doc.recompute()
    try:
        S.model_for_sketch(on_plane)
        report.check(False, "a sketch attached to a solid-less object is refused")
    except S.SlicingError as exc:
        report.check(
            "no solid" in str(exc),
            "a sketch attached to a solid-less object is refused, and says "
            "why: %s" % exc,
        )


if __name__ in ("__main__", "test_slicing"):
    # `freecadcmd` sets `__name__` to the module's own name rather than
    # "__main__", so this guard needs both spellings -- and it does not call
    # sys.exit(), because freecadcmd does not propagate an exit code. That is
    # why the harness writes a status file instead. Same shape as
    # test_concave_regressions.py; a copy per test would drift.
    try:
        H.run(STATUS, body)
    finally:
        close_everything()
