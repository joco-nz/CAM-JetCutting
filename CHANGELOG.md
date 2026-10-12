# Changelog

All notable changes to CAM-JetCutting are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [1.1.0] - 2026-10-12

### Added
- **Find Profiles: Concave indentation detection** — Detects chains of edges forming concave indentations in the outer wire and creates profile operations for them. Each chain runs boundary-to-boundary with a 1 µm tolerance.
- **Find Profiles: Cut-side inversion for concave chains** — Inside ↔ Outside, None unchanged. Concave chains cut from the outside boundary inward, so the waste is on the opposite side of an internal hole.
- **Find Profiles: Concave lead-in angle sweep** — For fixed-start concave chains, sweeps lead-in angle alternating from 90° (Perpendicular) with both invert settings (89 candidates, 2° steps). First hit applies `StyleIn='Line'` with a single recompute. Falls back to shortening then flagging.
- **Find Profiles: Longest-edge midpoint seed for relocation** — On closed contours, the midpoint of the longest edge is tried first before the spiral search. Costs ~1 recompute vs up to 48.
- **Find Profiles: Creation-time longest-edge start** — Closed loops now start at the midpoint of the contour's longest edge via FreeCAD's `Profile.UseLongestEdge`. Reads the property during the recompute that was going to run anyway (~6.7% overhead, 50.7 → 54.1 ms/op). Opt-in via `start_on_longest_edge`; concave path explicitly opts out.
- **Find Profiles: Lead-in audit progress reporting** — `begin_leadin_audit_phase` / `report_leadin_audit_progress` called twice per target; shows progress even when relocation searches stall the bar.
- **Slice Model command** — Extrudes a sketch's slice lines through a model and splits it into chunks. Depth derived from the model's bounding box; works for any thickness without hard-coded numbers. Produces an Exploded group of chunks, hides the model/extrusion/Slice feature.
- **Same Edges As Highlighted: Clone-first Job selection ordering** — Gate checks selection order (Clone then Job) because `create_profile_ops_for_top_loops` acts on the first object.
- **Toolbar icons redrawn** — New SVG icons for all three commands.
- **Concave chain detection hardening** — `extend_chains_to_boundary` walks each concave run until endpoints reach the part boundary (1 µm tolerance, matching `is_contiguous_run`). Guards against double-inclusion, whole-wire wrap, and iteration caps.

### Changed
- **Find Profiles: Lead-in pierce clearance rule tightened** — Pierce centre must clear the waste edge by ≥ kerf/2 (was just containment). The pierce hole itself cannot reach the finished edge.
- **Find Profiles: Audit lever order** — Direction flip → Relocation → Shortening → Flag. Relocation skipped on convex regions (guard removed 2026-10-12 after discovering it suppressed valid moves on convex rounded triangles).
- **Find Profiles: Skip relocation on concave (fixed-start) chains** — `UseStartPoint` on an open chain silently displaces the cut by ~1 mm and makes `StartPoint` inert. Relocation burns 48 recomputes and cannot succeed. `fixed_start=True` skips the lever entirely.
- **Find Profiles: Concave chains use angle sweep instead of relocation** — The only free parameter is the lead-in angle. Sweep subsumes the invert lever (first candidate = 90° with original invert).
- **Find Profiles: Convex guard removed from relocation** — `Profile_Loop_31` (convex rounded triangle) natural start missed clearance by 0.066 mm; longest-edge start clears by 4.187 mm. The guard's premise ("inward normal cannot leave waste on convex regions") was false — convexity says nothing about local width.
- **Find Profiles: `IsEnabled` → `IsActive`** — FreeCAD's `PythonCommand::isActive()` looks up `IsActive` by name only; non-bool returns read as False.
- **Slice Model: Slice feature hidden** — The Slice feature itself is hidden; chunks read `Base=Slice`, so leaving it visible re-renders the whole unsplit panel.
- **Slice Model: Extrusion and model hidden** — One extrusion, hidden; model hidden; all in one document, undoable together.

### Fixed
- **Find Profiles: Concave chain completion** — Previously stopped at first non-concave edge, leaving chains short of the boundary (e.g. `Profile_Concave_44` missing Edge221, 4.169 mm from boundary). Now walks to boundary vertices.
- **Find Profiles: Concave chain side inversion** — `Profile_Concave_44` created with Side='Inside'; now correctly created with Side='Outside'.
- **Slice Model: Toolbar button gating** — `IsActive` returns a real bool; button inactive when no sketch selected, active when one attached sketch selected.
- **Slice Model: Model hiding** — The original model, extrusion, and Slice feature all hidden rather than deleted (still undoable).
- **Lead-in audit: Convex-region relocation suppression** — The guard that skipped relocation on convex regions was suppressing valid fixes. Convexity ≠ local width. Removed the guard; relocation now runs on all regions when allowed.
- **Lead-in audit: `UseStartPoint` mutation on open chains** — Setting it displaced the cut by ~1 mm and made `StartPoint` inert. Now `fixed_start=True` skips relocation entirely for concave chains.
- **Profile creation: CR-11 guard** — `validate_chain_edges` runs *before* `Part.__sortEdges__` on unsorted edges, so the contiguous-run check can actually catch merged neighbouring indentations. Sorting first would mask the defect.

### Infrastructure
- **Mutation testing** — 10 mutants caught across concave detection, angle sweep, and creation-time longest-edge flag.
- **Tests: `test_profile_operation.py` pins creation-time flag** — Default off, flag sets it, closed-loop call site opts in, concave call site explicitly opts out (source-level assertion).
- **Tests: `test_angle_sweep.py`** — Pure pytest for angle sweep geometry: candidate ordering, radius invariant, flip negation, 0/180° degeneracy, narrow-mouth notch fixture (90° fails, 120° works).
- **Tests: `test_longest_edge_midpoint.py`** — Pure pytest for seed behaviour and edge picking.
- **Test: `test_concave_regressions.py`** — End-to-end regression: separate notches never merged; 5 shapes × all rotations; plain rectangle and circle yield no chains.
- **Test: `test_lead_in_audit.py`** — Lever order, convex/non-convex routing, direction flip precedence, relocation once-per-op, region_convex=None handling, `next_action` contract.
- **Test: `test_lead_in_probe.py`** — `StartPoint` moves pierce on closed loops; `Profile_Loop_31` is convex and was previously skipped by the convex guard.

---

## [1.0.0] - 2026-09-30

### Added
- **Three CAM Workbench toolbar commands** — "Same Edges As Highlighted", "Slice Model", "Find Profiles" under a "JetCutter Tools" toolbar.
- **Same Edges As Highlighted** — Selects all edges matching a template by length and Z-level (0.001 mm tolerance). Works across all visible solids.
- **Slice Model** — Extrudes a sketch's lines through a model to split it into machine-sized chunks. Depth auto-derived from model bounds.
- **Find Profiles** — Creates profile operations for all internal loops on top faces of a selected CAM Job. Configurable tool, offset side, cut direction, lead-in/out.
- **Lead-in pierce audit** — Moves pierces into the waste region, relocating start points or shortening lead-ins as needed. Reports CLEAN / ONPART / UNREADABLE.
- **Lead-in audit: kerf-aware clearance** — Pierce centre clears waste edge by ≥ kerf/2.
- **Lead-in audit: relocation before shortening** — Preserves operator's full lead-in length where possible.
- **Lead-in audit: progress dialog** — Shows real-time progress with per-operation labels.

### Fixed
- **Lead-in audit: Path recompute optimisation** — First measurement reuses caller's recompute (`paths_current=True`); search loop uses `current=True`.
- **Lead-in audit: Search double-recompute removed** — `measure_pierce(..., current=True)` in search loop.
- **Profile creation: O(n²) edge matching replaced with hash lookup** — `model_clone.Shape.getElement()` via `edge.hashCode()` map.
- **Dressup lead-in length** — Expression-bound to `ToolController.Tool.Diameter.Value × multiplier`.
- **FreeCAD 26.x compatibility** — `Path.Dressup.Utils.baseOp` for base operation resolution.

---

## Notes

- **Dependencies**: FreeCAD ≥ 1.1.0 (bundled CAM Workbench), `shapely ≥ 2.0` (lead-in audit geometry).
- **Test gate**: `tests/run.sh` runs three tiers — pytest (pure Python), freecadcmd (real document), gui (ViewObject/UiLoader). Status files in `tests/status/`.
- **Uncommitted docs**: `PRD.md`, `issues.md`, `review.md` — design records, not shipped.