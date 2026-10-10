"""The lead-in audit's decision rules, tested without FreeCAD.

`jetcutter_tools.lead_in_audit` imports neither FreeCAD nor Qt, and Shapely only
inside the functions that need it, so `tests/conftest.py` imports it with no stub
at all. That is the point of this tier: if a FreeCAD dependency ever creeps into
the audit, these tests stop running rather than quietly passing against a stub.

The toolpath stand-ins below are the Nesting workbench's pattern. A lead-in is
not identified by its shape -- an `Arc` one and a `Perpendicular` one have
nothing in common but being extra -- so these build the sequences the generator
actually emits and check what the audit concludes from them.

The geometry these encode is measured, not assumed; see
`tests/gui/test_lead_in_probe.py`, which establishes that the pierce lands at
`|path_r - L|` and that a lead-in is in the waste exactly when `L < 2R - t`.
"""
import math

import pytest
from jetcutter_tools import lead_in_audit as audit

KERF = 2.0
TOOL_R = KERF / 2.0
HOLE_R = 10.0


class _Cmd:
    """One gcode line."""

    def __init__(self, name, **params):
        self.Name = name
        self.Parameters = params


class _Path:
    """A toolpath: just a list of commands, which is all the audit reads."""

    def __init__(self, commands):
        self.Commands = list(commands)


class _Dressup:
    """Enough of a dressup for the marking helpers.

    `addProperty`/`removeProperty` are modelled because `mark_on_part` guards
    them and swallows failures. A stub without them would take the guarded path
    every time and the tests would pass without ever proving the property is
    written.
    """

    def __init__(self, label="DressupLeadInOut001"):
        self.Label = label
        self.PropertiesList = []

    def addProperty(self, _kind, name, _group, _doc):
        if name not in self.PropertiesList:
            self.PropertiesList.append(name)
        setattr(self, name, "")

    def removeProperty(self, name):
        if name in self.PropertiesList:
            self.PropertiesList.remove(name)
            delattr(self, name)


def lead_in_path(start, end, body=()):
    """Build a toolpath shaped like the one `leadinout.generate` emits.

    Travel to the lead-in's entry, plunge, the lead-in, then the contour. The
    G1 that plunges moves only Z, and is the reason the audit tests for XY
    movement rather than for the command name: read as a cut move it would
    report the plunge as the pierce, at Z=0 and on the contour.
    """
    commands = [
        _Cmd("G0", Z=6.0),
        _Cmd("G0", X=start[0], Y=start[1]),
        _Cmd("G0", Z=4.0),
        # Modal gcode: a plunge carries only Z, inheriting X/Y from the rapid
        # above. Spelling X/Y out here would make this fixture pass whether or
        # not the XY-movement guard exists.
        _Cmd("G1", Z=0.0, F=100.0),
        _Cmd("G1", X=end[0], Y=end[1], F=100.0),
    ]
    commands.extend(body)
    return _Path(commands)


def circle_body(cx, cy, radius, segments=12):
    """Build a circular cut as straight moves, which is all a body is."""
    body = []
    for index in range(1, segments + 1):
        angle = 2.0 * math.pi * index / segments
        body.append(_Cmd("G1",
                         X=cx + radius * math.cos(angle),
                         Y=cy + radius * math.sin(angle), F=100.0))
    return body


# -- reading the pierce -----------------------------------------------------

def test_the_pierce_is_the_start_of_the_first_xy_cut_move():
    path = lead_in_path((30.0, 30.0), (40.0, 40.0), circle_body(0.0, 0.0, 5.0))
    assert audit.pierce_point(path) == (30.0, 30.0)


def test_the_z_plunge_is_not_counted_as_a_cut_move():
    # The plunge inherits X/Y from the rapid above it, so it shares the lead-in's
    # start and dropping the guard would NOT change the pierce point -- it would
    # only add a zero-length move to the list. Asserted on the list, which is
    # what the guard actually governs: a move that goes nowhere is not a cut.
    path = lead_in_path((30.0, 30.0), (40.0, 40.0))
    moves = audit.cut_moves(path)
    assert len(moves) == 1
    assert moves[0].length == pytest.approx(math.hypot(10.0, 10.0))


def test_the_pierce_survives_the_lead_in_being_only_one_move():
    path = lead_in_path((30.0, 30.0), (40.0, 40.0), circle_body(0.0, 0.0, 5.0))
    assert audit.pierce_point(path) == (30.0, 30.0)


def test_a_path_with_no_cutting_move_has_no_pierce():
    assert audit.pierce_point(_Path([_Cmd("G0", X=1.0, Y=2.0)])) is None
    assert audit.pierce_point(None) is None


def test_an_arc_is_sampled_rather_than_chorded():
    # A quarter arc of radius 10 about the origin, G2 from (10,0) to (0,10).
    # The rapid first: an arc's implicit start is wherever the tool is, so
    # without it the sweep would begin at the origin and describe nothing.
    path = _Path([_Cmd("G0", X=10.0, Y=0.0),
                  _Cmd("G2", I=-10.0, J=0.0, X=0.0, Y=10.0, F=100.0)])
    moves = audit.cut_moves(path)
    assert len(moves) == 1
    # The chord's midpoint is at radius 7.07; the arc's true midpoint is 10.
    mid = moves[0].points[len(moves[0].points) // 2]
    assert math.hypot(mid[0], mid[1]) > 9.9


def test_a_anticlockwise_arc_takes_the_anticlockwise_way_round():
    # G3 from (10,0) to (0,10) about the origin is the quarter turn through
    # (7.07,7.07). Taking the short way round the other side would still start
    # and end in the right place with the right radius, so only the sampled
    # geometry can tell the two apart.
    path = _Path([_Cmd("G0", X=10.0, Y=0.0),
                  _Cmd("G3", I=-10.0, J=0.0, X=0.0, Y=10.0, F=100.0)])
    moves = audit.cut_moves(path)
    mid = moves[0].points[len(moves[0].points) // 2]
    assert mid[0] > 0 and mid[1] > 0


def test_an_arc_without_a_centre_falls_back_to_its_chord():
    path = _Path([_Cmd("G0", X=10.0, Y=0.0),
                  _Cmd("G2", R=10.0, X=0.0, Y=10.0, F=100.0)])
    moves = audit.cut_moves(path)
    assert moves[0].points == [(10.0, 0.0), (0.0, 10.0)]


# -- the waste region -------------------------------------------------------

def square(size):
    return [(0.0, 0.0), (size, 0.0), (size, size), (0.0, size)]


def test_a_point_inside_the_contour_is_in_the_waste():
    polygon = audit.waste_polygon(square(20.0))
    assert audit.is_in_waste((10.0, 10.0), polygon) is True


def test_a_point_outside_the_contour_is_not_in_the_waste():
    polygon = audit.waste_polygon(square(20.0))
    assert audit.is_in_waste((25.0, 10.0), polygon) is False


def test_no_region_means_nothing_is_in_the_waste():
    # Deliberately not "clean": a caller that cannot test must be told so, and
    # False here is what makes the caller check the region first.
    assert audit.is_in_waste((1.0, 1.0), None) is False
    assert audit.is_in_waste((1.0, 1.0), None) is not None


def test_too_few_points_make_no_region():
    assert audit.waste_polygon([(0.0, 0.0), (1.0, 1.0)]) is None
    assert audit.waste_polygon([]) is None


def test_a_self_intersecting_contour_is_repaired_into_a_valid_region():
    # A contour whose points cross -- a bow tie -- is an invalid polygon, and
    # containment against an invalid one answers unpredictably rather than
    # refusing. buffer(0) is what repairs it. A merely repeated closing point is
    # a perfectly valid ring, so it would prove nothing about this guard.
    bow_tie = [(0.0, 0.0), (20.0, 20.0), (20.0, 0.0), (0.0, 20.0)]
    polygon = audit.waste_polygon(bow_tie)
    assert polygon is not None
    assert polygon.is_valid
    # Which lobe the repair keeps is an arbitrary tie-break, so it is not
    # asserted -- what matters is that the result is valid and answerable.
    assert isinstance(audit.is_in_waste((15.0, 10.0), polygon), bool)


# -- the decision -----------------------------------------------------------

@pytest.mark.parametrize("in_waste,side,inverted,multiplier,expected", [
    # Already good: nothing to do, on any setting.
    (True, "Inside", False, 3.0, audit.ACTION_NONE),
    (True, "Outside", True, 2.0, audit.ACTION_NONE),
    # Side=Outside points away from the contour, so it is on the part whatever
    # the length. InvertIn is tried first: flipping costs nothing, whereas
    # shortening a lead-in that points the right way helps nothing.
    (False, "Outside", False, 3.0, audit.ACTION_INVERT),
    # Already inverted and still wrong: now the length is the constraint.
    (False, "Outside", True, 3.0, audit.ACTION_SHORTEN),
    (False, "Inside", False, 3.0, audit.ACTION_SHORTEN),
    # At or below the floor there is nowhere shorter to go. Shrinking further
    # would trade a pierce scar for an arc that never stabilises.
    (False, "Inside", False, audit.MINIMUM_MULTIPLIER, audit.ACTION_FLAG),
    (False, "Inside", False, 2.0, audit.ACTION_FLAG),
    (False, "Outside", True, audit.MINIMUM_MULTIPLIER, audit.ACTION_FLAG),
])
def test_the_next_action(in_waste, side, inverted, multiplier, expected):
    assert audit.next_action(in_waste, side=side, inverted=inverted,
                             multiplier=multiplier) == expected


def test_the_floor_is_two_kerfs():
    # The whole design rests on this number: a lead-in shorter than 2x the kerf
    # does not give the arc room to stabilise, so it is not offered and not
    # applied. If it moves, the multiplier arithmetic moves with it.
    assert audit.MINIMUM_MULTIPLIER == 2.0


def test_shortening_happens_at_most_once():
    # Each action must move the tracked state, or a caller looping on this
    # would re-apply the same action forever.
    action = audit.next_action(False, side="Inside", multiplier=3.0)
    assert action == audit.ACTION_SHORTEN
    after = audit.next_action(False, side="Inside",
                              multiplier=audit.MINIMUM_MULTIPLIER)
    assert after == audit.ACTION_FLAG


def test_the_budget_is_hole_diameter_less_half_the_kerf():
    assert audit.waste_budget(20.0, 2.0) == pytest.approx(19.0)
    assert audit.waste_budget(5.0, 2.0) == pytest.approx(4.0)


def test_the_default_is_above_the_floor():
    assert audit.DEFAULT_MULTIPLIER > audit.MINIMUM_MULTIPLIER


def test_the_window_is_empty_for_a_hole_under_two_and_a_half_kerfs():
    # Measured on the GUI tier: a lead-in is in the waste exactly when
    # L < hole_dia - kerf/2, and it needs L >= 2 x kerf. Those conflict below
    # 2.5 x kerf, which is the only situation the audit has to hand back.
    for diameter in (3.0, 5.0):
        assert audit.waste_budget(diameter, KERF) <= 2.0 * KERF, diameter
    for diameter in (10.0, 20.0):
        assert audit.waste_budget(diameter, KERF) > 2.0 * KERF, diameter


# -- marking ----------------------------------------------------------------

def test_an_unfixed_dressup_is_marked_with_the_prefix():
    dressup = _Dressup("DressupLeadInOut001")
    audit.mark_on_part(dressup, "because reasons")
    assert dressup.Label == "ONPART_DressupLeadInOut001"
    assert getattr(dressup, audit.PROP_ONPART) == "because reasons"


def test_marking_twice_does_not_stack_the_prefix():
    # The audit may revisit an operation, and a doubled prefix is noise.
    dressup = _Dressup()
    audit.mark_on_part(dressup, "first")
    audit.mark_on_part(dressup, "second")
    assert dressup.Label == "ONPART_DressupLeadInOut001"
    assert getattr(dressup, audit.PROP_ONPART) == "second"


def test_clearing_restores_the_original_label():
    dressup = _Dressup()
    audit.mark_on_part(dressup, "because")
    assert audit.clear_on_part(dressup) is True
    assert dressup.Label == "DressupLeadInOut001"
    assert audit.PROP_ONPART not in dressup.PropertiesList


def test_clearing_an_untouched_dressup_is_not_a_change():
    dressup = _Dressup()
    assert audit.clear_on_part(dressup) is False
    assert dressup.Label == "DressupLeadInOut001"


def test_the_prefix_says_on_part_not_skip():
    # The dressup still cuts. A label reading SKIP_ invites the belief that it
    # has been excluded from the job, which is what turns one bad pierce into
    # scrapped parts.
    assert "PART" in audit.ONPART_PREFIX
    assert "SKIP" not in audit.ONPART_PREFIX


# -- reporting --------------------------------------------------------------

def test_an_unreadable_finding_is_not_reported_as_clean():
    finding = audit.Finding("DressupLeadInOut001", audit.UNREADABLE)
    assert finding.status != audit.CLEAN
    assert "could not read" in finding.describe()


def test_clean_ones_are_left_out_of_the_attention_list():
    findings = [audit.Finding("A", audit.CLEAN, multiplier=3.0),
                audit.Finding("B", audit.ON_PART, "too tight",
                              action=audit.ACTION_FLAG)]
    lines = audit.describe_findings(findings)
    assert len(lines) == 1
    assert "B" in lines[0] and "A" not in lines[0]


def test_the_summary_counts_each_status():
    findings = [audit.Finding("A", audit.CLEAN),
                audit.Finding("B", audit.ON_PART),
                audit.Finding("C", audit.UNREADABLE),
                audit.Finding("D", audit.ON_PART)]
    counts = audit.summarise(findings)
    assert counts[audit.CLEAN] == 1
    assert counts[audit.ON_PART] == 2
    assert counts[audit.UNREADABLE] == 1


def test_a_flag_says_why_rather_than_only_that():
    text = audit.describe_flag(3.0, 4.0, 2.0)
    assert "2.00 mm kerf" in text
    assert "arc needs to stabilise" in text


def test_the_attention_list_is_capped():
    findings = [audit.Finding("op%d" % i, audit.ON_PART) for i in range(30)]
    lines = audit.describe_findings(findings, limit=5)
    assert len(lines) == 6
    assert "and 25 more" in lines[-1]
