"""Extrude a sketch's lines through a solid and slice it apart.

The manual workflow this replaces, from the shop's own write-up:

    Select the sketch, Part > Extrude, make sure the faces cut all the way
    through the model, then select the model and the extrusion and use
    Part > Split > Slice Apart.

Both steps are `Slice Apart` doing the same boolean work, so both are done here
in one transaction -- a single undo for what used to be two, plus the
intermediate objects to clean up.

Everything that can be measured is measured. The extrusion depth is derived
from the model's own bounding box rather than typed in, because "long enough to
cut all the way through" depends on a thickness the user should not have to
know: a 2 mm sheet and a 12 mm plate need different cuts, and guessing wrong
leaves a chunk that is joined by an uncut sliver.
"""

# How far past the model's far and near faces the extruded faces reach, in mm.
#
# Overkill by construction: the point is to clear the surface on both sides, not
# to be tight. OCC's boolean is tolerant of an over-long tool and intolerant of
# one that stops exactly on the face -- a coincident face is the case where it
# most likes to fail, and the failure mode is silent.
THROUGH_MARGIN_MM = 1.0

# A chunk is degenerate if it has no solid, or its volume rounds to nothing.
#
# Both are needed. When the slice lines miss the model, or graze a cutout edge,
# `Slice Apart` returns the original solid plus a piece with no volume at all --
# it does not raise, so nothing announces the problem.
VOLUME_EPSILON = 1e-6


class SlicingError(Exception):
    """Raised when the sketch cannot be turned into a clean set of chunks."""


def model_for_sketch(sketch):
    """Return the solid to be sliced for `sketch`, or raise `SlicingError`.

    The sketch's attachment support names it. If that support is a feature
    inside a PartDesign body, the *body* is sliced instead, because slicing the
    feature leaves the body itself standing there as a second, undissolved copy
    of the panel referencing the thing that was just cut up. This matches what
    Part > Split > Slice Apart produces when a body is selected.

    Returns the body, the support, or the first solid found -- whichever is the
    outermost object with the geometry, in that preference order.
    """
    supports = list(getattr(sketch, "AttachmentSupport", None) or ())
    if not supports:
        raise SlicingError(
            f"{sketch.Label} is not attached to anything. Attach it to the "
            "model's face so the model can be found."
        )

    support = supports[0][0]
    for candidate in _owning_bodies(support):
        return candidate

    if _has_solids(support):
        return support

    raise SlicingError(
        f"{sketch.Label} is attached to {support.Label}, which has no solid to "
        "slice. Attach the sketch to the model's face rather than to a plane "
        "or another sketch."
    )


def _owning_bodies(support):
    """Yield the PartDesign bodies, innermost first, that contain `support`.

    Walks `InList` rather than trusting a known parent: a feature can be inside
    a body, inside a part, inside another body, and only the first of those is
    the object that should be sliced. Two steps is enough for any tree a user
    can build by hand, and the loop stops at a body so a cycle cannot hang it.
    """
    seen = {support.Name}
    for holder in support.InList:
        if holder.Name in seen:
            continue
        seen.add(holder.Name)
        if _is_body(holder):
            yield holder
        else:
            # A part or group between the feature and the body; look one level in.
            for parent in getattr(holder, "InList", ()):
                if parent.Name in seen:
                    continue
                seen.add(parent.Name)
                if _is_body(parent):
                    yield parent


def _is_body(obj):
    return obj.isDerivedFrom("PartDesign::Body")


def _has_solids(obj):
    shape = getattr(obj, "Shape", None)
    return bool(shape) and bool(shape.Solids)


def extrusion_depth(sketch, model):
    """Return `(forward, reverse)` lengths that cut `model` completely.

    Both directions are always non-zero, which is the whole trick. The sketch
    sits on the top face, so a one-sided extrusion points away from the material
    and cuts nothing at all; and if the sketch ever sits below the top, a
    one-sided cut stops partway and leaves the chunks joined by an uncut web.
    The depth of the model along the sketch's own normal decides both, so the
    result is independent of where in the stack the sketch happens to be.

    Measured on a real 2 mm panel: forward 1.0, reverse 3.0, giving faces from
    z -1 to z 3 around a model spanning z 0 to z 2.
    """
    normal = sketch.Placement.Rotation.multVec(_unit_z())
    box = model.Shape.BoundBox
    origin = sketch.Placement.Base.dot(normal)

    # Project the bounding box's corners onto the normal to find how far the
    # model reaches each way. Corner projection rather than the Z extent, so a
    # sketch on a rotated or vertical face still cuts through the material.
    reach = [
        _vector(x, y, z).dot(normal)
        for x in (box.XMin, box.XMax)
        for y in (box.YMin, box.YMax)
        for z in (box.ZMin, box.ZMax)
    ]

    forward = max(reach) - origin + THROUGH_MARGIN_MM
    reverse = origin - min(reach) + THROUGH_MARGIN_MM
    return forward, reverse, normal


def build_extrusion(doc, sketch, model, depth=None):
    """Create the cutting tool: `sketch`'s lines swept through `model`.

    `Solid` is False and has to stay False. The sketch holds open lines, and
    asking for a solid from open lines fails with "Part::FaceMaker: result shape
    is null" -- a message that names neither the cause nor the fix. The slice
    wants faces, which is what sweeping lines produces.
    """
    forward, reverse, normal = depth or extrusion_depth(sketch, model)
    extrusion = doc.addObject("Part::Extrusion", "PartExtrude")
    extrusion.Base = sketch
    extrusion.DirMode = "Custom"
    extrusion.Dir = normal
    extrusion.LengthFwd = forward
    extrusion.LengthRev = reverse
    extrusion.Solid = False
    return extrusion


def find_degenerate(chunks):
    """Return the chunks that are not real pieces of material.

    Accepts either document objects (the exploded chunk filters, which carry a
    ``.Shape``) or raw ``Part`` shapes taken straight off a compound (which
    carry the geometry themselves). Passing the wrong kind silently rejects
    everything: a raw solid has no ``.Shape``, so an object-only check reads it
    as empty.

    Volume and solid count, never the bounding box: the exploded chunk filters
    inherit their parent's box, so a 579 mm chunk reports the full 2397 mm
    panel and a bbox check would pass anything.
    """
    bad = []
    for chunk in chunks:
        shape = getattr(chunk, "Shape", chunk)
        if not shape:
            bad.append(chunk)
            continue
        if not shape.Solids or shape.Volume <= VOLUME_EPSILON:
            bad.append(chunk)
    return bad


def slice_model(doc, sketch, model, depth=None, progress=None):
    """Extrude `sketch` through `model` and slice it apart.

    Returns `(extrusion, slice_feature, group, chunks)`.

    Raises `SlicingError` if the result is not a clean partition -- specifically
    if the slice lines miss the model, or graze a cutout, which yields the
    original solid plus an empty piece and no complaint from FreeCAD.
    """
    from BOPTools import SplitFeatures

    _notify(progress, "Extruding the sketch through the model")
    extrusion = build_extrusion(doc, sketch, model, depth)
    doc.recompute()

    if not extrusion.Shape or not extrusion.Shape.Faces:
        raise SlicingError(
            "The sketch extruded to nothing. It has no lines, or they lie "
            "outside the model."
        )

    _notify(progress, "Slicing the model")
    feature = SplitFeatures.makeSlice(name="Slice")
    feature.Base = model
    feature.Tools = [extrusion]
    feature.Mode = "Split"
    feature.Proxy.execute(feature)
    doc.recompute()

    if not feature.Shape or not feature.Shape.Solids:
        raise SlicingError("The slice produced no solids.")

    bad = find_degenerate(feature.Shape.Solids)
    if bad:
        raise SlicingError(
            f"The slice produced {len(bad)} empty piece(s). The sketch's lines "
            "do not cut across the model cleanly -- check that they cross it, "
            "and that no internal cut opens onto one of them by a very small "
            "amount."
        )

    _notify(progress, "Separating the chunks")
    group, chunks = explode(feature)
    return extrusion, feature, group, chunks


def explode(feature, make_group=True):
    """Split `feature`'s compound into one object per chunk.

    Uses FreeCAD's own Explode, which is what Part > Split > Slice Apart calls,
    so the resulting tree matches that command exactly: a group labelled
    "Exploded Slice" holding "Slice.N" children, left where they were.
    """
    from CompoundTools import Explode

    # A single chunk gets no group, matching Explode's own choice.
    group, chunks = Explode.explodeCompound(feature, make_group)
    feature.Document.recompute()
    return group, chunks


def _notify(progress, message):
    if progress is not None:
        progress(message)


def _unit_z():
    import FreeCAD as App

    return App.Vector(0, 0, 1)


def _vector(x, y, z):
    import FreeCAD as App

    return App.Vector(x, y, z)
