"""Locate a lead-in pierce point and judge whether it is waste or part.

The problem this exists to answer
---------------------------------
FindProfiles profiles internal wires -- holes and concave indentations -- on the
top faces of a part. For a jet cutter the lead-in's pierce point has to land in
the **waste**: the slug that falls out, or the indentation that gets cut away.
Pierce on the part and the finished edge carries a pierce scar.

Three measurements on the GUI tier (`tests/gui/test_lead_in_probe.py`) fix the
geometry this module reasons about. On a hole of diameter D with kerf k,
`Side=Inside` puts the tool centreline at `D/2 - k/2`, the lead-in is
perpendicular to the path and points inward, so

    pierce radius = |D/2 - k/2 - L|,   waste  <=>  L < D - k/2

`L` is not a free parameter: it *is* the distance the arc travels before it
starts cutting, and an arc needs 2x the kerf to stabilise. So the constraint is
a **window**, not a bound:

    floor     L >= 2 x kerf      -- arc stabilisation
    ceiling   L <  D - k/2       -- stay in the waste

The window closes when the hole is under 2.5 x kerf. The addon's default of
3 x kerf sits above the ceiling for holes under 3.5 x kerf -- which for a 2 mm
plasma kerf means 6 mm and smaller, while a 0.5 mm laser kerf is never affected.

Free imports
------------
No FreeCAD, no Part, no Qt, and Shapely only lazily inside the functions that
need it. That is deliberate: `tests/conftest.py` imports this module with no
stub at all, so if it ever grows a FreeCAD dependency the fast tier stops
running rather than quietly passing against a stub.

The pierce point is read as "the start of the first cut move that moves in XY"
rather than by recognising a lead-in's shape. That is sound because
`leadinout.generate()` emits travel moves, then the lead-in, then the contour, and
replaces the base operation's own travel moves with its own -- so the first
XY-moving cut move is the lead-in's entry point, whatever the style. The
Nesting workbench instead diffs base against dressup (`lead_runs`) because it
must separate several runs on one path; FindProfiles builds one operation per
contour and only ever needs the first, so the cheaper rule suffices here.
"""
import math
import re

#: The addon's dialog default, and the value FindProfiles uses unless told
#: otherwise. Domain-correct for the overwhelming majority of holes, which is
#: why the audit steps *down* from it rather than proposing a different one.
DEFAULT_MULTIPLIER = 3.0

#: The arc-stabilisation floor: 2 x kerf. Nothing below this is offered, and
#: nothing below this is applied. A lead-in shorter than 2 x kerf does not give
#: the arc room to stabilise before it reaches the cut, so "just make it
#: shorter" is not available as an unconditional remedy.
MINIMUM_MULTIPLIER = 2.0

#: Points closer together than this are the same point. Millimetre-scale
#: coordinates read straight out of a toolpath, not a discretised outline.
COINCIDENT_MM = 1e-6

#: Chords per arc when an arc is flattened to a polyline. Sampling rather than
#: chording matters because a chord lies *inside* the arc, which pulls the path
#: toward its centre of curvature -- and for an inside test that pulls points
#: out of a hole, reading as waste when the arc is on the part.
ARC_CHORDS = 24

#: Prepended to a dressup's Label when its pierce lands on the part and could not
#: be fixed. A prefix rather than a suffix because the label already carries the
#: operation name and a suffix is what scrolls off a narrow tree column.
#:
#: The word is `ONPART_` and not `SKIP_` on purpose. The dressup is **not**
#: skipped: it will still cut, and it will still pierce where it pierces. A
#: label reading `SKIP_` invites the belief that the operation has been excluded
#: from the job, which is the belief that turns one bad pierce into scrapped
#: parts. The same reasoning as the Nesting workbench's `CONFLICT_`.
ONPART_PREFIX = "ONPART_"

#: Property recording the finding, for a program rather than a human. The Label
#: is editable and cosmetic, so a report cannot rely on it -- a user who renames
#: an operation would otherwise drop it from every future report. It also
#: carries the numbers, which the label has no room for.
PROP_ONPART = "LeadInOnPart"

# The three things the audit can do about a pierce, and the one thing it cannot.
ACTION_NONE = None
ACTION_INVERT = "invert"
ACTION_SHORTEN = "shorten"
ACTION_FLAG = "flag"

CLEAN = "clean"
ON_PART = "on-part"
UNREADABLE = "unreadable"


class Finding:
    """One operation whose lead-in pierce needs attention.

    `status` is CLEAN, ON_PART or UNREADABLE. UNREADABLE is kept distinct from
    clean on purpose: a lead-in whose toolpath could not be read has not been
    shown to be safe, and reporting it as clean would turn "we did not look"
    into "there is no problem".
    """

    __slots__ = ("action", "detail", "multiplier", "name", "pierce", "status")

    def __init__(self, name, status, detail="", pierce=None, multiplier=None,
                 action=None):
        """Record one operation's verdict and, where it applies, its detail."""
        self.name = name
        self.status = status
        self.detail = detail
        #: The pierce point as (x, y), or None when it could not be read.
        self.pierce = pierce
        #: The lead-in multiplier in force when the finding was made.
        self.multiplier = multiplier
        #: What the audit did, for the report.
        self.action = action

    def describe(self):
        if self.status == CLEAN:
            return f"{self.name}: lead-in pierces in the waste (x{_fmt(self.multiplier)})"
        if self.status == UNREADABLE:
            return f"{self.name}: could not read the lead-in, so it was not checked"
        head = f"{self.name}: lead-in pierces on the part"
        if self.action == ACTION_FLAG:
            return f"{head} and could not be moved. {self.detail}"
        return f"{head}. {self.detail}"


def _fmt(value):
    return "-" if value is None else f"{value:.1f}"


# -- the toolpath as geometry ----------------------------------------------

def cut_moves(path, chords=ARC_CHORDS):
    """Return the cut moves in `path` as `CutMove`s.

    A cut move is G1/G2/G3 that changes XY. Gcode is modal, so the G1 plunge
    preceding a lead-in carries only Z and inherits the lead-in's own X/Y: it
    shares the lead-in's start point, and testing on the command name alone would
    report a zero-length move as a cut. That does not move the pierce -- the two
    start in the same place -- but a move that goes nowhere is not a cut, and
    leaving it in miscounts every list this is used to build.
    """
    if path is None:
        return []
    out = []
    x = y = 0.0
    for command in getattr(path, "Commands", None) or ():
        params = getattr(command, "Parameters", None) or {}
        nx = params.get("X")
        ny = params.get("Y")
        nx = float(nx) if nx is not None else x
        ny = float(ny) if ny is not None else y
        if command.Name in ("G1", "G2", "G3") and (
                abs(nx - x) > COINCIDENT_MM or abs(ny - y) > COINCIDENT_MM):
            if command.Name in ("G2", "G3"):
                points = _arc_points(command, x, y, nx, ny, chords)
            else:
                points = [(x, y), (nx, ny)]
            out.append(CutMove(command.Name, x, y, nx, ny, points))
        x, y = nx, ny
    return out


class CutMove:
    """One cut move: where it starts, where it ends, and the path between.

    `points` is the move expanded to a polyline, arcs sampled from their `I`/`J`
    centre. The geometry has to travel with the move: rebuilding it from the
    endpoints alone flattens every arc to a chord, and a chord lies inside the
    arc, so a point on a real contour reads as being further inside than it is.
    """

    __slots__ = ("name", "points", "x0", "x1", "y0", "y1")

    def __init__(self, name, x0, y0, x1, y1, points):
        """Hold one move's endpoints together with the polyline between them."""
        self.name = name
        self.x0 = x0
        self.y0 = y0
        self.x1 = x1
        self.y1 = y1
        self.points = points

    @property
    def start(self):
        return (self.x0, self.y0)

    @property
    def length(self):
        total = 0.0
        for index in range(len(self.points) - 1):
            ax, ay = self.points[index]
            bx, by = self.points[index + 1]
            total += math.hypot(bx - ax, by - ay)
        return total


def _arc_points(command, x0, y0, x1, y1, chords):
    """Sample a `G2`/`G3` from its `I`/`J` centre, or fall back to the chord.

    `G17` only: `I`/`J` are centre offsets in the command's own plane and the
    sweep is unwrapped as G2 clockwise in XY, which is right for 2D profiling and
    wrong for `G18`/`G19`. An `R`-form arc carries a radius rather than a centre,
    so it falls back -- and chording overstates how far inside a hole a point is,
    which is the one direction of error that would read as clean.
    """
    params = command.Parameters
    i = params.get("I")
    j = params.get("J")
    if i is None or j is None:
        return [(x0, y0), (x1, y1)]
    cx, cy = x0 + float(i), y0 + float(j)
    radius = math.hypot(float(i), float(j))
    if radius <= COINCIDENT_MM:
        return [(x0, y0), (x1, y1)]

    start = math.atan2(y0 - cy, y0 - cx)
    end = math.atan2(y1 - cy, y1 - cx)
    # G2 turns clockwise, so `end` must fall below `start`. Unwrapping in that
    # direction rather than taking the short way round matters for a G3 of more
    # than half a turn, where the short way is the wrong arc entirely.
    if command.Name == "G2":
        while end >= start:
            end -= 2.0 * math.pi
    else:
        while end <= start:
            end += 2.0 * math.pi
    step = (end - start) / chords
    return [(cx + radius * math.cos(start + step * k),
             cy + radius * math.sin(start + step * k))
            for k in range(chords + 1)]


def pierce_point(path, chords=ARC_CHORDS):
    """Return the pierce point of `path`: the start of its first cut move.

    None when the path has no XY-moving cut move at all, which is what an
    operation with no lead-in, or a failed generator, looks like.
    """
    moves = cut_moves(path, chords)
    return moves[0].start if moves else None


# -- the waste region ------------------------------------------------------

def waste_polygon(points):
    """Build a Shapely polygon for the region enclosed by a contour, or None.

    `points` is the contour discretised to an ordered point list. `buffer(0)` is
    applied because a discretised wire can self-touch at a tolerance, which
    otherwise makes the polygon invalid and every containment test against it
    answer unpredictably.

    None is returned rather than an empty polygon when Shapely is missing or the
    points are unusable, because "no region to test against" and "an empty
    region" would otherwise both read as a pierce that is on the part.
    """
    if not points or len(points) < 3:
        return None
    try:
        from shapely.geometry import Polygon
    except ImportError:
        return None
    try:
        polygon = Polygon(list(points)).buffer(0)
    except Exception:  # noqa: BLE001
        return None
    if polygon.is_empty:
        return None
    return polygon


def is_in_waste(point, polygon):
    """Report whether `point` is inside the waste region. False when untestable.

    Deliberately not tri-state at this level: an unusable region returns False,
    and the caller distinguishes the two cases by checking the region first. The
    alternative -- returning None -- pushes a None check onto every call site,
    which is where it gets forgotten.
    """
    if point is None or polygon is None:
        return False
    try:
        from shapely.geometry import Point
        return bool(polygon.contains(Point(point[0], point[1])))
    except Exception:  # noqa: BLE001
        return False


# -- deciding what to do ----------------------------------------------------

def waste_budget(hole_diameter, kerf):
    """Return the largest lead-in keeping the pierce in the waste: `D - k/2`.

    A closed-form restatement of the measured `L < 2R - t`. It is a convenience
    for reporting and for the `Side=Outside` case, **not** the test: the audit
    measures the real toolpath, because this formula assumes a convex contour of
    a given diameter and says nothing about a slot or a concave chain.
    """
    return hole_diameter - kerf / 2.0


def next_action(in_waste, side="Inside", inverted=False, multiplier=None):
    """Decide what to do next about a pierce that is not in the waste.

    Ordered so each lever is tried at most once:

    1. `Side=Outside` points the lead-in away from the contour by definition, so
       it is on the part whatever the length. `InvertIn` is the lever there, and
       it is tried before shortening because flipping costs nothing.
    2. Shorten to the arc-stabilisation floor. Not to some smaller length: below
       the floor the lead-in does not let the arc stabilise, which trades a
       pierce scar for a bad cut. The `multiplier > MINIMUM_MULTIPLIER` guard is
       what makes this fire once -- after shortening, the tracked multiplier is
       at the floor, so a second pass falls through to flagging rather than
       looping.
    3. Flag. The window is empty -- no lead-in both stabilises the arc and stays
       in the waste -- and that is the operator's call, not the addon's.
    """
    if in_waste:
        return ACTION_NONE
    if side == "Outside" and not inverted:
        return ACTION_INVERT
    if multiplier is not None and multiplier > MINIMUM_MULTIPLIER:
        return ACTION_SHORTEN
    return ACTION_FLAG


def describe_shorten(from_multiplier, to_multiplier):
    """Return one line saying why a lead-in was shortened."""
    return (f"shortened from x{from_multiplier:.1f} to x{to_multiplier:.1f}, "
            "the shortest lead-in that lets the arc stabilise")


def describe_flag(multiplier, budget, kerf):
    """Return one line saying why a lead-in was left alone."""
    if budget is None:
        return f"x{multiplier} pierces on the part and there is no room to lengthen it"
    return (f"x{multiplier} pierces on the part, and this {kerf:.2f} mm kerf "
            f"leaves only {budget:.2f} mm of waste against the "
            f"{MINIMUM_MULTIPLIER * kerf:.2f} mm an arc needs to stabilise")


# -- marking ----------------------------------------------------------------

def strip_prefix(label):
    return re.sub("^" + re.escape(ONPART_PREFIX), "", label or "")


def mark_on_part(dressup, detail):
    """Record that a dressup's pierce is on the part and needs a human.

    Idempotent: marking twice must not stack the prefix, because the audit may
    revisit an operation and a doubled prefix is noise in the tree.
    """
    try:
        base = strip_prefix(dressup.Label)
        dressup.Label = ONPART_PREFIX + base
    except Exception:  # noqa: BLE001
        pass
    try:
        if not hasattr(dressup, PROP_ONPART):
            dressup.addProperty("App::PropertyString", PROP_ONPART, "CAM Replay",
                                "Why this lead-in pierces on the part")
        setattr(dressup, PROP_ONPART, detail)
    except Exception:  # noqa: BLE001
        pass


def clear_on_part(dressup):
    """Remove the prefix and the property, restoring the original label."""
    touched = False
    try:
        if dressup.Label.startswith(ONPART_PREFIX):
            dressup.Label = strip_prefix(dressup.Label)
            touched = True
    except Exception:  # noqa: BLE001
        pass
    try:
        if PROP_ONPART in dressup.PropertiesList:
            dressup.removeProperty(PROP_ONPART)
            touched = True
    except Exception:  # noqa: BLE001
        pass
    return touched


# -- reporting --------------------------------------------------------------

def summarise(findings):
    """Return counts by status, for one line at the end of a run."""
    out = {CLEAN: 0, ON_PART: 0, UNREADABLE: 0}
    for finding in findings:
        out[finding.status] = out.get(finding.status, 0) + 1
    return out


def describe_findings(findings, limit=20):
    """Return lines describing findings that need attention, clean ones omitted."""
    lines = []
    attention = [f for f in findings if f.status != CLEAN]
    for finding in attention[:limit]:
        lines.append("  " + finding.describe())
    hidden = len(attention) - limit
    if hidden > 0:
        lines.append(f"  ... and {hidden} more")
    return lines
