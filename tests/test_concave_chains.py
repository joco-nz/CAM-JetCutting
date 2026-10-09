"""Tests for the concave-indentation detection.

These cover `group_concave_chains`, `is_contiguous_run` and
`is_concave_indentation` — the pure geometry behind Find Profiles' concave
detection. They live in `jetcutter_tools.geometry`, which imports neither
FreeCAD nor Qt, so they run under a plain `pytest` with no stubs and no
FreeCAD:

    python3 -m pytest tests/

That is the point of the split (CR-03). The tests that genuinely need a real
FreeCAD document live in `tests/freecadcmd/` and `tests/gui/`, driven by
`tests/run.sh`.

What is being tested is a safety-relevant detector: a wrongly grouped chain
produces a toolpath that crosses the part rather than following an indentation.
So the cases that matter most are the ones where the code must say **no** — a
single-grouping regression must fail here, not pass quietly.

The grouping tests pass plain integers as edges, because grouping only indexes
the sequence and never touches the objects.
"""
from itertools import pairwise

import pytest

from jetcutter_tools import geometry as G

MIN = G._MIN_CONCAVE_CHAIN


def _identity_sort(edges):
    """Stand-in for sorting that changes nothing."""
    return list(edges)


def _sort_like_part(edges):
    """Stand-in for `Part.__sortEdges__`.

    That function repairs disconnected edges into a single connected sequence —
    measured, it takes 6 merged edges and returns 3, discarding whatever it
    cannot connect. Every group it returns is therefore connected, and
    is_contiguous_run always accepts it, so a guard applied to sorted input can
    never fire. This models the same effect: walk from the first edge, attach
    any unused edge that shares a vertex with something already placed, and
    drop the rest.
    """
    remaining = list(edges)
    if not remaining:
        return []
    placed = [remaining.pop(0)]

    def touches(candidate, others):
        for a in candidate.Vertexes:
            for other in others:
                for b in other.Vertexes:
                    if abs(a.X - b.X) <= 1e-6 and abs(a.Y - b.Y) <= 1e-6:
                        return True
        return False

    # Attach anything reachable from what is already placed.
    progress = True
    while progress:
        progress = False
        for edge in list(remaining):
            if touches(edge, placed):
                placed.append(edge)
                remaining.remove(edge)
                progress = True
    # Unreachable edges are DROPPED, which is what the real function does.
    # Measured on Part geometry: __sortEdges__ takes 6 merged edges and returns
    # 3. Every group it returns is connected, so is_contiguous_run always
    # accepts the result — the guard can never fire on sorted input.
    return placed


class _Wire:
    """Minimal stand-in for a Part wire: only `isClosed` is ever asked."""

    def __init__(self, edges):
        self.Edges = list(edges)

    def isClosed(self):
        return True


# ---------------------------------------------------------------------------
# group_concave_chains
# ---------------------------------------------------------------------------
# Index sets that used to either lose a wrap-around notch (the chain was split
# at the seam and both fragments fell below the minimum) or merge unrelated
# notches into one chain.
GROUPING_CASES = [
    ("two notches, seam between them", 11, [0, 1, 3, 4, 5, 10], [[3, 4, 5], [10, 0, 1]]),
    ("three notches, seam between", 15, [0, 1, 3, 4, 5, 7, 8, 9, 14],
     [[3, 4, 5], [7, 8, 9], [14, 0, 1]]),
    ("no seam involved", 15, [3, 4, 5, 7, 8, 9, 14], [[3, 4, 5], [7, 8, 9]]),
    ("wrap-around pair only", 6, [0, 5], [[5, 0]]),
    ("single run", 8, [2, 3, 4, 5], [[2, 3, 4, 5]]),
    ("runs of one are dropped", 8, [1, 3, 4, 6], [[3, 4]]),
    ("all edges concave", 4, [0, 1, 2, 3], [[0, 1, 2, 3]]),
    ("too few concave edges", 8, [2], []),
    ("empty", 8, [], []),
]


@pytest.mark.parametrize("label,n,concave,want", GROUPING_CASES,
                         ids=[c[0] for c in GROUPING_CASES])
def test_grouping(label, n, concave, want):
    assert G.group_concave_chains(list(range(n)), concave) == want


@pytest.mark.parametrize("label,n,concave,_want", GROUPING_CASES,
                         ids=[c[0] for c in GROUPING_CASES])
def test_chains_partition_the_concave_edges(label, n, concave, _want):
    """No edge lands in two chains, and nothing non-concave is included.

    Chains partition the concave edges except for an isolated one, which is
    dropped by design. So every concave edge is either in exactly one chain or
    is isolated.
    """
    chains = G.group_concave_chains(list(range(n)), concave)
    flat = [e for ch in chains for e in ch]
    concave_set = set(concave)
    isolated = {i for i in concave
                if not ((i - 1) % n in concave_set or (i + 1) % n in concave_set)}
    assert len(flat) == len(set(flat)), "an edge appears in two chains"
    assert set(flat) <= concave_set, "a non-concave edge was grouped"
    assert set(flat) == concave_set - isolated


def test_two_separate_notches_are_never_merged():
    """The regression this whole function exists for.

    Pre-fix, this produced ONE chain of 6 edges spanning both notches across a
    100 mm part. `test_concave_regressions.py` pins the end-to-end case; this is
    the unit-level statement of the same thing.
    """
    chains = G.group_concave_chains(list(range(11)), [0, 1, 3, 4, 5, 10])
    assert [list(c) for c in chains] == [[3, 4, 5], [10, 0, 1]]
    assert len(chains) == 2, "separate notches were merged into one chain"


def test_grouping_is_rotation_invariant():
    """The same concave pattern, rotated, must group the same way.

    The seam used to decide the outcome, so this is the property that was
    broken rather than any single index set.
    """
    n = 15
    for start in range(n):
        concave = sorted(((start + i) % n) for i in (3, 4, 5, 7, 8, 9))
        chains = G.group_concave_chains(list(range(n)), concave)
        assert [len(c) for c in chains] == [3, 3], f"start={start} gave {chains}"
        for chain in chains:
            # each chain must be consecutive modulo n, so it wraps rather than
            # runs off either end
            for a, b in pairwise(chain):
                assert (b - a) % n == 1, f"chain {chain} is not contiguous"


# ---------------------------------------------------------------------------
# is_contiguous_run
# ---------------------------------------------------------------------------

class _V:
    """Minimal stand-in for a Part vertex. Only .X/.Y/.Z are ever read."""

    def __init__(self, x, y, z=0.0):
        self.X, self.Y, self.Z = x, y, z


class _E:
    """Minimal stand-in for a Part edge, defined by its endpoints."""

    def __init__(self, *points):
        self.Vertexes = [_V(*p) for p in points]


def _rect_notch_chain(x0, x1, top, depth):
    """The four edges of a rectangular notch cut into a top edge."""
    return [
        _E((x0, top), (x0, depth)),
        _E((x0, depth), (x1, depth)),
        _E((x1, depth), (x1, top)),
    ]


def test_single_notch_is_contiguous():
    assert G.is_contiguous_run(_rect_notch_chain(80, 90, 40, 20)) is True


def test_two_separate_notches_are_not_contiguous():
    """The guard that stops a merged chain becoming a cross-part cut.

    These two notches are 60 mm apart. Profiling them as one operation would
    drive the tool across the intervening material.
    """
    merged = _rect_notch_chain(10, 20, 40, 20) + _rect_notch_chain(80, 90, 40, 20)
    assert G.is_contiguous_run(merged) is False


def test_lone_edge_is_not_a_run():
    assert G.is_contiguous_run([_E((0, 0), (1, 1))]) is False


def test_empty_is_not_a_run():
    assert G.is_contiguous_run([]) is False


def test_closed_loop_is_contiguous():
    loop = [_E((0, 0), (10, 0)), _E((10, 0), (10, 10)),
            _E((10, 10), (0, 10)), _E((0, 10), (0, 0))]
    assert G.is_contiguous_run(loop) is True


def test_edges_in_arbitrary_order_are_contiguous():
    """The docstring promises order-independence; assert it.

    The frontier is *replaced* rather than unioned as the walk proceeds, which
    is what keeps a T-junction from reporting three unrelated edges as one run.
    """
    chain = _rect_notch_chain(80, 90, 40, 20)
    assert G.is_contiguous_run(list(reversed(chain))) is True
    assert G.is_contiguous_run([chain[2], chain[0], chain[1]]) is True


def test_star_of_edges_at_one_vertex_is_contiguous():
    """Three edges meeting at a single point ARE one connected run.

    Asserted because it is the easy thing to get wrong. A star is genuinely
    contiguous — every edge shares the same vertex — so returning False here
    would silently drop a real feature.
    """
    star = [
        _E((0, 0), (10, 0)),
        _E((10, 0), (20, 10)),
        _E((10, 0), (20, -10)),
    ]
    assert G.is_contiguous_run(star) is True


def test_a_detached_edge_breaks_the_run():
    """The case the contiguity guard exists for.

    Two edges join end to end; the third is somewhere else entirely. The walk
    reaches the second and stops, so this is not one run — and profiling it as
    one would cut across the part between the features.
    """
    edges = [_E((0, 0), (10, 0)), _E((10, 0), (20, 0)), _E((50, 50), (60, 50))]
    assert G.is_contiguous_run(edges) is False


def test_frontier_is_replaced_not_unioned():
    """Pin the load-bearing detail in `is_contiguous_run`.

    The walk advances one edge at a time and *discards* the previous frontier
    rather than accumulating it. An edge is therefore only accepted when it
    touches the edge the walk is currently standing on — not merely because it
    touched some earlier edge in the run.

    The input below is the discriminating case:

        edge1  (0,0)---(10,0)
        edge2              (10,0)---(20,0)
        edge3  (0,0)---(-10,5)      touches edge1, but the walk has moved past it

    A unioning implementation would accept edge3 (it is in the accumulated
    frontier) and answer True. Replacing the frontier rejects it and answers
    False, which is what stops two features that merely both touch edge1 being
    profiled as one cut.
    """
    edges = [
        _E((0, 0), (10, 0)),     # edge1
        _E((10, 0), (20, 0)),    # edge2 — the walk steps onto this
        _E((0, 0), (-10, 5)),    # edge3 — reaches back to edge1 only
    ]
    assert G.is_contiguous_run(edges) is False


def test_reachability_is_ordered_not_transitive():
    """A longer chain makes the same point without relying on edge order.

    Every edge here is reachable from edge1, but only in walk order. The last
    edge can only be entered via edge3, and by then the frontier no longer
    contains edge1's vertices.
    """
    edges = [
        _E((0, 0), (10, 0)),
        _E((10, 0), (20, 0)),
        _E((20, 0), (30, 5)),
        _E((0, 0), (-10, 5)),
    ]
    assert G.is_contiguous_run(edges) is False


# ---------------------------------------------------------------------------
# is_concave_indentation
# ---------------------------------------------------------------------------

def test_notch_vertex_is_deep_inside_the_box():
    # A 100x40 plate; the notch bottom at y=20 is > 5 mm from every wall.
    assert G.is_concave_indentation(_E((50, 20), (60, 20)), 0, 100, 0, 40, 5.0) is True


def test_corner_vertex_is_not_deep():
    assert G.is_concave_indentation(_E((0, 0), (10, 0)), 0, 100, 0, 40, 5.0) is False


def test_shallow_feature_below_threshold_is_not_concave():
    """A 4 mm step is inside the 5 mm threshold, so not an indentation."""
    assert G.is_concave_indentation(_E((50, 4), (60, 4)), 0, 100, 0, 40, 5.0) is False


def test_edge_without_vertices_is_not_concave():
    assert G.is_concave_indentation(_E(), 0, 100, 0, 40, 5.0) is False


def test_access_failure_is_reported_as_not_concave():
    """A bad edge must not be silently promoted to an indentation.

    Returning True here would create a profile on geometry that was never
    inspected; returning False fails safe and is logged.
    """
    class Broken:
        @property
        def Vertexes(self):
            raise IndexError("no vertices")

    assert G.is_concave_indentation(Broken(), 0, 100, 0, 40, 5.0) is False


# ---------------------------------------------------------------------------
# validate_chain_edges — the guard ordering (CR-11)
# ---------------------------------------------------------------------------
# This is the part that was previously duplicated inside commands.py, where
# nothing could test it. Sorting happens INSIDE the helper, so a test can now
# pin that contiguity is judged before the sort rather than after.

def test_validate_rejects_a_merged_chain():
    merged = _rect_notch_chain(10, 20, 40, 20) + _rect_notch_chain(80, 90, 40, 20)
    ok, ordered, reason = G.validate_chain_edges(merged, sort_fn=_identity_sort)
    assert ok is False
    assert reason == "not_contiguous"
    assert ordered is None


def test_validate_rejects_too_short():
    ok, ordered, reason = G.validate_chain_edges([_E((0, 0), (1, 1))])
    assert (ok, reason) == (False, "too_short")


def test_validate_rejects_a_closed_wire():
    """A closed loop is not a side cut; the Profile op would mishandle it."""
    loop = [_E((0, 0), (10, 0)), _E((10, 0), (10, 10)),
            _E((10, 10), (0, 10)), _E((0, 10), (0, 0))]
    closed = []

    ok, ordered, reason = G.validate_chain_edges(
        loop,
        sort_fn=_identity_sort,
        wire_fn=lambda edges: _Wire(edges),
        is_closed_fn=lambda wire: wire.isClosed(),
    )
    assert (ok, reason) == (False, "closed")


def test_validate_accepts_a_single_notch_and_sorts_it():
    chain = _rect_notch_chain(80, 90, 40, 20)
    ok, ordered, reason = G.validate_chain_edges(chain, sort_fn=_identity_sort)
    assert (ok, reason) == (True, None)
    assert len(ordered) == len(chain)


def test_contiguity_is_judged_BEFORE_sorting():
    """The CR-11 regression, stated directly.

    `_sort_like_part` reproduces `Part.__sortEdges__`: it drops whatever it
    cannot connect, so its output is always contiguous. Checking the sorted
    value therefore accepts a merged chain — the defect that made the guard in
    commands.py inert.

    Three assertions, each pinning one part of the argument:

    1. the stand-in really does return a contiguous subset (so the test would
       notice if the stand-in stopped modelling the real function),
    2. checking that sorted value accepts the merged chain — the defect,
    3. `validate_chain_edges` rejects it whatever sort it is given, because it
       judges contiguity on the input.
    """
    merged = _rect_notch_chain(10, 20, 40, 20) + _rect_notch_chain(80, 90, 40, 20)

    # 1. the stand-in models __sortEdges__: fewer edges out, all contiguous
    sorted_edges = _sort_like_part(merged)
    assert len(sorted_edges) < len(merged), "stand-in should drop unreachable edges"
    assert G.is_contiguous_run(sorted_edges) is True, (
        "the real __sortEdges__ always returns connected groups, which is exactly "
        "why checking its output cannot detect a merged chain"
    )

    # 2. the defect: a guard applied to the sorted value accepts a merged chain
    assert G.is_contiguous_run(_sort_like_part(merged)) is True

    # 3. the fix: the helper rejects it regardless of the sort function
    for sort_fn, label in ((_identity_sort, "identity"), (_sort_like_part, "part-like")):
        ok, ordered, reason = G.validate_chain_edges(merged, sort_fn=sort_fn)
        assert ok is False, f"merged chain accepted with {label} sort"
        assert reason == "not_contiguous"


def test_validate_does_not_sort_when_it_rejects():
    """No work is done on a chain that will be skipped."""
    calls = []

    def counting_sort(edges):
        calls.append(list(edges))
        return list(edges)

    merged = _rect_notch_chain(10, 20, 40, 20) + _rect_notch_chain(80, 90, 40, 20)
    ok, _, reason = G.validate_chain_edges(merged, sort_fn=counting_sort)
    assert ok is False
    assert reason == "not_contiguous"
    assert calls == [], "sort ran even though the chain was rejected"


def test_call_site_does_not_sort_before_validating():
    """Guard the CR-11 regression at the point it was originally introduced.

    `validate_chain_edges` rejects a merged chain however it is called, so the
    behavioural tests above cannot see a caller that sorts the edges *before*
    handing them over — the helper would receive an already-repaired list and
    accept it. That is exactly the defect CR-11 was, and it lives at the call
    site in commands.py, not in the helper.

    Nothing runs the full operation loop, so this asserts on the source instead:
    the argument passed to `validate_chain_edges` must be the raw
    `matched_edges`, never a sorted or otherwise rewritten list. Cheap, and it
    fails loudly if someone reintroduces the ordering bug.
    """
    import ast
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    source = root / "freecad" / "jetcutter_tools" / "commands.py"
    assert source.exists(), "commands.py not found at %s" % source
    tree = ast.parse(source.read_text())

    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "validate_chain_edges"
    ]
    assert calls, "commands.py no longer calls validate_chain_edges"

    for call in calls:
        first_arg = call.args[0]
        assert isinstance(first_arg, ast.Name), (
            "validate_chain_edges must be passed the resolved edge list by name; "
            f"got {ast.unparse(first_arg)} — if it is a transformed list, the sort "
            "has been hoisted in front of the guard (CR-11)"
        )
        assert first_arg.id == "matched_edges", (
            "validate_chain_edges must be passed `matched_edges` as resolved, got "
            f"{first_arg.id!r} (CR-11)"
        )

    # The name itself must not have been rebound between being filled and being
    # passed. A pre-sort written as `matched_edges = Part.__sortEdges__(...)`
    # leaves the argument name untouched while defeating the guard entirely, so
    # checking the name alone would miss exactly the regression this guards.
    function = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(
            getattr(sub.func, "id", "") == "validate_chain_edges"
            for sub in ast.walk(node)
            if isinstance(sub, ast.Call)
        )
    )
    # `matched_edges = []` and `.append(...)` build the list; that is fine. What
    # must not happen is a *replacement* after it is filled and before it is
    # handed to the guard.
    call_line = min(call.lineno for call in calls)
    # The list is first bound at `matched_edges = []`. Anything after that and
    # up to the call is a rebind; the initial binding is not.
    first_bind = min(
        (
            node.lineno
            for node in ast.walk(function)
            if isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "matched_edges"
                for t in node.targets
            )
        ),
        default=0,
    )
    for node in ast.walk(function):
        targets = []
        if isinstance(node, ast.Assign):
            targets = [t for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            target = node.target
            if isinstance(target, ast.Name):
                targets = [target]
        for target in targets:
            if target.id != "matched_edges":
                continue
            assert not (first_bind < node.lineno <= call_line), (
                "`matched_edges` is rebound at line %d, between being filled at %d "
                "and being validated at %d. If that is a sort, the CR-11 guard is "
                "defeated again: the helper would receive already-repaired edges"
                % (node.lineno, first_bind, call_line)
            )
