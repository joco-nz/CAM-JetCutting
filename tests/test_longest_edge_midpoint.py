"""
Unit tests for longest_edge_midpoint and seed behavior in start_point_candidates.
Pure pytest, no FreeCAD required.
"""
import sys, math
sys.path.insert(0, "/home/james/dev/CAM-JetCutting/freecad")
from jetcutter_tools.lead_in_audit import (
    longest_edge_midpoint,
    start_point_candidates,
    COINCIDENT_MM,
)


class FakeEdge:
    """Minimal edge mock with Length and discretize(3)."""
    def __init__(self, length, pts):
        self.Length = length
        self._pts = pts

    def discretize(self, n):
        assert n == 3
        return self._pts


class FakePt:
    def __init__(self, x, y):
        self.x = x
        self.y = y


def test_longest_edge_midpoint_picks_longest():
    e1 = FakeEdge(10.0, [FakePt(0, 0), FakePt(5, 0), FakePt(10, 0)])   # midpoint (5,0)
    e2 = FakeEdge(20.0, [FakePt(0, 0), FakePt(10, 0), FakePt(20, 0)])  # midpoint (10,0)
    e3 = FakeEdge(15.0, [FakePt(0, 0), FakePt(7.5, 0), FakePt(15, 0)]) # midpoint (7.5,0)
    assert longest_edge_midpoint([e1, e2, e3]) == (10.0, 0.0)


def test_longest_edge_midpoint_tie_resolves_to_first():
    e1 = FakeEdge(20.0, [FakePt(0, 0), FakePt(10, 0), FakePt(20, 0)])
    e2 = FakeEdge(20.0, [FakePt(0, 0), FakePt(10, 1), FakePt(20, 2)])
    assert longest_edge_midpoint([e1, e2]) == (10.0, 0.0)


def test_longest_edge_midpoint_empty_returns_none():
    assert longest_edge_midpoint([]) is None


def test_longest_edge_midpoint_degenerate_edge_returns_none():
    # discretize raises
    class BadEdge:
        Length = 10.0
        def discretize(self, n):
            raise ValueError("bad")
    assert longest_edge_midpoint([BadEdge()]) is None


def test_start_point_candidates_seed_first():
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    origin = (0.0, 0.0)
    # With seed, first yield should be the seed
    cand = list(start_point_candidates(points, origin, step=2.0, limit=10, seed=(5.0, 5.0)))
    assert cand[0] == (5.0, 5.0), f"first should be seed, got {cand[0]}"
    # Remaining should be spiral order starting from origin
    assert len(cand) > 1


def test_start_point_candidates_seed_counts_toward_limit():
    points = [(0.0, 0.0), (10.0, 0.0)]
    origin = (0.0, 0.0)
    cand = list(start_point_candidates(points, origin, step=2.0, limit=1, seed=(5.0, 5.0)))
    assert cand == [(5.0, 5.0)], f"limit=1 with seed should yield only seed, got {cand}"


def test_start_point_candidates_seed_none_falls_back_to_spiral():
    points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0), (0.0, 0.0)]
    origin = (0.0, 0.0)
    # No seed -> starts with spiral
    cand = list(start_point_candidates(points, origin, step=2.0, limit=2))
    # First spiral candidate should be 2mm along the contour in +direction
    assert cand[0] != (5.0, 5.0), "without seed, first should not be the midpoint"


def test_start_point_candidates_limit_zero_yields_nothing():
    points = [(0.0, 0.0), (10.0, 0.0)]
    origin = (0.0, 0.0)
    cand = list(start_point_candidates(points, origin, step=2.0, limit=0, seed=(5.0, 5.0)))
    assert cand == []


def test_start_point_candidates_short_contour_returns_none():
    # contour length < COINCIDENT_MM
    points = [(0.0, 0.0), (1e-7, 0.0)]
    origin = (0.0, 0.0)
    cand = list(start_point_candidates(points, origin, step=2.0, limit=10, seed=(1.0, 0.0)))
    assert cand == []


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
