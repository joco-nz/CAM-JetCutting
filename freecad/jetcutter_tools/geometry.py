"""Pure geometry helpers for concave-indentation detection.

Split out of `commands.py` (CR-03) so this logic can be tested without a
FreeCAD, Qt or GUI import. Nothing here imports FreeCAD at module scope; the
`App` reference inside `find_concave_chains` is confined to its exception
handler and is resolved lazily.

`commands.py` re-exports every name below, so existing call sites and the
addon's public surface are unaffected.

Two functions here are load-bearing and should not be "simplified":

- `is_contiguous_run` *replaces* its frontier rather than unioning it. That is
  what makes a T-junction return False instead of reporting three unrelated
  edges as one run. It looks like a bug and is not.
- `group_concave_chains` walks indices modulo the edge count and starts at a
  genuine run start, so a notch straddling the wire seam is neither split nor
  merged with its neighbour.
"""
import traceback
from itertools import pairwise

# Injected by commands.py so debug output stays in one place. None disables it.
_debug_log = None

# Mirrors commands.DEBUG. Kept separate so this module stays importable without
# the GUI layer; commands.py sets it from the same source of truth.
DEBUG = False


def configure(debug=None, logger=None):
    """Align this module's debug state with the addon's.

    ``debug`` sets the verbosity flag; ``logger`` sets the sink. commands.py
    calls this once so there is a single switch for the whole addon.
    """
    global DEBUG, _debug_log
    if debug is not None:
        DEBUG = debug
    if logger is not None:
        _debug_log = logger


def _dbg(msg):
    if DEBUG and _debug_log is not None:
        _debug_log(msg)


def _report_error(prefix, exc):
    """Report an unexpected error.

    Prefers FreeCAD's report view when it is importable, and falls back to
    stderr so this module stays usable with no FreeCAD present. The fallback
    exists for tests; the FreeCAD path is what runs in the addon.
    """
    detail = f"{prefix} ERROR: {exc}\n{traceback.format_exc()}"
    try:
        import FreeCAD as App
    except ImportError:
        import sys
        print(detail, file=sys.stderr)
    else:
        App.Console.PrintError(detail)

def is_concave_indentation(edge, bb_min_x, bb_max_x, bb_min_y, bb_max_y, depth_tol):
    """Check if an edge is a concave indentation."""
    try:
        verts = edge.Vertexes
        if not verts:
            return False

        any_deep = False
        for v in verts:
            dist = min(
                v.X - bb_min_x,
                bb_max_x - v.X,
                v.Y - bb_min_y,
                bb_max_y - v.Y,
            )
            if dist > depth_tol:
                any_deep = True
                break

        return any_deep
    except (AttributeError, IndexError) as e:
        if DEBUG:
            _dbg(
                f"  [CONCAVE] Edge vertex access error: {e}\n",
            )
        return False


# Minimum number of edges for a run of concave edges to count as an indentation.
_MIN_CONCAVE_CHAIN = 2


def is_contiguous_run(edges):
    """Return True if ``edges`` form one connected end-to-end run.

    Accepts edges in any order. Two edges are connected when they share a vertex
    position, compared with a tolerance so that numerically-close but distinct
    vertices still count as joined.

    This distinguishes a genuine indentation (one connected chain) from several
    separate indentations that have been concatenated into a single list. The
    latter would produce a toolpath that jumps across the part between features,
    which on a jet cutting machine means cutting a line the user never asked for.
    """
    if len(edges) < 2:
        return False

    pos = [[(v.X, v.Y, v.Z) for v in e.Vertexes] for e in edges]
    if any(not p for p in pos):
        return False

    def same(a, b):
        return all(abs(x - y) <= 1e-6 for x, y in zip(a, b, strict=True))

    # Walk the run: from the first edge, repeatedly step to an edge that shares an
    # endpoint with the one we are standing on, until nothing else is reachable.
    used = [0]
    frontier = list(pos[0])
    while True:
        extended = False
        for i, p in enumerate(pos):
            if i in used:
                continue
            if any(same(pt, ep) for pt in p for ep in frontier):
                used.append(i)
                frontier = list(p)
                extended = True
        if not extended:
            break

    return len(used) == len(edges)


def group_concave_chains(edges, concave_indices):
    """Group concave edge indices into chains of circularly-consecutive edges.

    A wire is a closed loop, so its edge indices form a circle: index ``n - 1`` is
    adjacent to index ``0``. A notch that straddles that boundary would otherwise be
    split into two runs, and the two boundary runs would each be too short to survive
    the minimum-length check.

    Two things are needed to get this right, and neither is sufficient alone:

    1. The scan starts at an index that *begins* a run, so no run straddles the seam.
    2. Adjacency is compared modulo the edge count, so a run that legitimately wraps
       from the last index back to the first stays joined.

    Chains shorter than ``_MIN_CONCAVE_CHAIN`` are dropped: a single concave edge is
    not a meaningful indentation.

    Args:
        edges: The wire's edges, indexed as they appear on the wire.
        concave_indices: Ascending indices into ``edges`` of the concave edges.

    Returns:
        List of chains, each a list of edges forming a consecutive run around the
        wire. Every concave edge appears in exactly one chain.

    """
    n = len(edges)
    if n < 2 or len(concave_indices) < _MIN_CONCAVE_CHAIN:
        return []

    concave_set = set(concave_indices)

    # Start at an index that begins a run, so the seam never cuts a run in half.
    start = next(
        (
            i
            for i in range(n)
            if i in concave_set and (i - 1) % n not in concave_set
        ),
        concave_indices[0],
    )
    # Walk n indices from `start`, wrapping, and keep the wrapped (0..n-1) value.
    ordered = [
        i % n for i in range(start, start + n) if i % n in concave_set
    ]

    if DEBUG:
        _dbg(
            f"  [CONCAVE_CHAINS] scan start={start}, circular order={ordered}\n",
        )

    chains = []
    current_chain = [ordered[0]]
    for a, b in pairwise(ordered):
        if (b - a) % n == 1:
            current_chain.append(b)
        elif len(current_chain) >= _MIN_CONCAVE_CHAIN:
            chains.append([edges[idx] for idx in current_chain])
            if DEBUG:
                _dbg(
                    f"  [CONCAVE_CHAINS] chain closed at gap {a}->{b}, size {len(current_chain)}\n",
                )
            current_chain = [b]
        else:
            if DEBUG:
                _dbg(
                    f"  [CONCAVE_CHAINS] dropping short run {current_chain} (< {_MIN_CONCAVE_CHAIN})\n",
                )
            current_chain = [b]
    if len(current_chain) >= _MIN_CONCAVE_CHAIN:
        chains.append([edges[idx] for idx in current_chain])

    return chains


def find_concave_chains(wire, face_normal, concave_depth_tol=5.0):
    """Find edge chains forming concave indentations in a wire."""
    try:
        if DEBUG:
            _dbg("  [CONCAVE_CHAINS] Starting concave chain detection\n")
        edges = wire.Edges
        if DEBUG:
            _dbg(
                f"  [CONCAVE_CHAINS] Wire has {len(edges)} edges, normal_z={face_normal.z:.6f}\n",
            )
        if len(edges) < 2:
            if DEBUG:
                _dbg("  [CONCAVE_CHAINS] Not enough edges (< 2), returning []\n")
            return []

        bb_min_x = bb_min_y = float("inf")
        bb_max_x = bb_max_y = float("-inf")
        for edge in edges:
            for v in edge.Vertexes:
                bb_min_x = min(bb_min_x, v.X)
                bb_max_x = max(bb_max_x, v.X)
                bb_min_y = min(bb_min_y, v.Y)
                bb_max_y = max(bb_max_y, v.Y)

        if DEBUG:
            _dbg(
                f"  [CONCAVE_CHAINS] Bounding box: x=[{bb_min_x:.2f}, {bb_max_x:.2f}], y=[{bb_min_y:.2f}, {bb_max_y:.2f}], concaveDepthTol={concave_depth_tol:.1f}\n",
            )

        concave_indices = []
        for i, edge in enumerate(edges):
            is_concave = is_concave_indentation(edge, bb_min_x, bb_max_x, bb_min_y, bb_max_y, concave_depth_tol)
            if is_concave:
                concave_indices.append(i)
            if DEBUG:
                _dbg(
                    f"  [CONCAVE] Edge{i + 1}: index={i}, concave={is_concave}\n",
                )

        if DEBUG:
            _dbg(
                f"  [CONCAVE_CHAINS] Found {len(concave_indices)} concave edge(s) at indices: {concave_indices}\n",
            )
        if DEBUG:
            _dbg(
                f"  [CONCAVE_CHAINS] Boundary edges: {len(edges) - len(concave_indices)}, Concave edges: {len(concave_indices)}\n",
            )

        if len(concave_indices) < 2:
            if DEBUG:
                _dbg("  [CONCAVE_CHAINS] Need at least 2 concave edges, returning []\n")
            return []

        return group_concave_chains(edges, concave_indices)
    except Exception as e:  # noqa: BLE001
        _report_error("  [CONCAVE_CHAINS]", e)
        return []


def validate_chain_edges(matched_edges, sort_fn=None, wire_fn=None, is_closed_fn=None):
    """Decide whether a chain of resolved edges is one profileable feature.

    Returns ``(ok, ordered_edges)``. ``ordered_edges`` is sorted and ready to
    build a wire from when ``ok`` is True.

    Two guards, in this order, and the order matters:

    1. **Contiguity, checked on the edges as resolved.** A chain must be one
       connected run; if the edges do not all join end-to-end then several
       features have been merged, and profiling them together drives the tool
       across the part rather than along an indentation.
    2. **Closure.** A chain that closes is a loop, not a side cut, and a Profile
       operation treats it differently — so it is rejected.

    ``sort_fn`` is ``Part.__sortEdges__``. It is called only *after* the
    contiguity check, never before it: sorting repairs disconnected edges into
    one connected sequence, so a check applied to sorted edges always passes
    and the guard cannot fire. That was CR-11, and it is why the sort is a
    parameter rather than an import here — this module must stay FreeCAD-free.

    ``is_closed_fn`` and ``wire_fn`` are injected for the same reason.

    Args:
        matched_edges: The chain's edges as resolved from the model, unsorted.
        sort_fn: Callable turning an edge list into sorted order.
        is_closed_fn: Callable taking a built wire, returning its closed state.
        wire_fn: Callable building a wire from an edge list.

    Returns:
        ``(True, ordered_edges, None)`` if the chain is one profileable feature.
        Otherwise ``(False, None, reason)`` where reason is ``"too_short"``,
        ``"not_contiguous"`` or ``"closed"`` — so the caller can report which
        guard fired without re-deriving it.

    """
    if len(matched_edges) < 2:
        return False, None, "too_short"

    # Before sorting. See the docstring.
    if not is_contiguous_run(matched_edges):
        return False, None, "not_contiguous"

    ordered = sort_fn(matched_edges) if sort_fn is not None else list(matched_edges)
    if wire_fn is not None and is_closed_fn is not None and is_closed_fn(wire_fn(ordered)):
        return False, None, "closed"
    return True, ordered, None
