"""GuiCommand classes for JetCutter Tools."""

import os
import FreeCAD as App
import FreeCADGui as Gui

try:
    from PySide import QtGui, QtCore
    from PySide.QtGui import QApplication, QProgressDialog
except ImportError:
    from PySide6 import QtWidgets, QtCore
    from PySide6.QtWidgets import QApplication, QProgressDialog

DEBUG = False
_DEBUG_LOG = os.path.join(os.environ.get("TMPDIR", "/tmp"), "find_profiles_debug.log")

def _dbg(msg):
    with open(_DEBUG_LOG, "a") as f:
        f.write(msg)


# ============================================================================
# SameEdgesAsHighlighted
# ============================================================================

class SameEdgesAsHighlighted:
    """Finds and selects all edges matching a template profile (length + Z-level)."""

    def IsEnabled(self):
        selection = Gui.Selection.getSelectionEx()
        if not selection:
            return False
        first_sel = selection[0]
        if not first_sel.SubElementNames:
            return False
        return any(sub.startswith("Edge") for sub in first_sel.SubElementNames)

    def Activated(self):
        TOLERANCE = 1e-3

        selection = Gui.Selection.getSelectionEx()
        if not selection:
            App.Console.PrintWarning("Please select your source edges first.\n")
            return

        first_sel = selection[0]
        sub_elements = first_sel.SubElementNames
        source_obj = first_sel.Object

        if not sub_elements:
            App.Console.PrintWarning("No edges selected.\n")
            return

        target_edges_data = []
        target_z_positions = []

        for sub_name in sub_elements:
            if not sub_name.startswith("Edge"):
                continue
            edge_index = int(sub_name.replace("Edge", "")) - 1
            edge = source_obj.Shape.Edges[edge_index]

            target_edges_data.append({
                "length": edge.Length,
            })
            target_z_positions.append(edge.CenterOfMass.z)

        if not target_edges_data:
            App.Console.PrintWarning("None of the selected elements are edges.\n")
            return

        avg_target_z = sum(target_z_positions) / len(target_z_positions)
        if DEBUG:
            _dbg(
                f"Template profile: {len(target_edges_data)} edges at Global Z-level: {avg_target_z:.4f} mm\n",
            )

        Gui.Selection.clearSelection()

        total_matched_count = 0
        active_doc = App.ActiveDocument
        gui_doc = Gui.ActiveDocument

        for obj in active_doc.Objects:
            if not hasattr(obj, "Shape") or obj.Shape.isNull():
                continue

            if obj.isDerivedFrom("App::DocumentObjectGroup"):
                continue
            if obj.isDerivedFrom("Part::Group"):
                continue
            if obj.isDerivedFrom("App::Part"):
                continue

            if not is_effectively_visible(obj, gui_doc):
                continue

            matched_edge_names = []
            matched_on_this_obj = 0

            for i, edge in enumerate(obj.Shape.Edges):
                edge_z = edge.CenterOfMass.z

                if abs(edge_z - avg_target_z) <= TOLERANCE:
                    is_match = False
                    for target in target_edges_data:
                        if abs(edge.Length - target["length"]) <= TOLERANCE:
                            is_match = True
                            break

                    if is_match:
                        matched_edge_names.append(f"Edge{i+1}")
                        matched_on_this_obj += 1
                        total_matched_count += 1

            if matched_on_this_obj > 0:
                Gui.Selection.addSelection(obj, tuple(matched_edge_names))
                if DEBUG:
                    _dbg(
                        f"Found {matched_on_this_obj} matching edges in visible object: '{obj.Label}'\n",
                    )

        if DEBUG:
            _dbg(
                f"Done! Successfully selected {total_matched_count} matching edges across visible solids.\n",
            )

    def GetResources(self):
        return {
            "Pixmap": "same-edges-as-highlighted",
            "MenuText": "Same Edges As Highlighted",
            "ToolTip": "Select all edges matching the length and Z-level of highlighted edges",
        }


# ============================================================================
# Helpers for SameEdgesAsHighlighted
# ============================================================================

def is_effectively_visible(obj, gui_doc, visited=None):
    if visited is None:
        visited = set()

    if obj in visited:
        return True
    visited.add(obj)

    gui_obj = gui_doc.getObject(obj.Name)
    if gui_obj and hasattr(gui_obj, "Visibility") and not gui_obj.Visibility:
        return False

    for ancestor in obj.InList:
        if not is_effectively_visible(ancestor, gui_doc, visited):
            return False

    return True


# ============================================================================
# FindProfiles
# ============================================================================

class FindProfiles:
    """Creates Profile operations for internal edges on CAM Job top faces."""

    def IsEnabled(self):
        selection = Gui.Selection.getSelectionEx()
        if not selection:
            return False
        obj = selection[0].Object
        if not hasattr(obj, "Proxy"):
            return False
        return "Job" in obj.Proxy.__class__.__name__

    def Activated(self):
        create_profile_ops_for_top_loops()

    def GetResources(self):
        return {
            "Pixmap": "FindProfiles",
            "MenuText": "Find Profiles",
            "ToolTip": "Create Profile operations for internal edges on CAM Job top faces",
        }


# ============================================================================
# Dialog and helpers for FindProfiles
# ============================================================================

def show_profile_settings_dialog(job):
    """Show a dialog to select Tool, Offset Side, Cut Direction, and LeadInOut settings."""
    try:
        from PySide import QtCore, QtGui
    except ImportError:
        from PySide6 import QtCore, QtGui

    tools_folder = None
    for obj in job.OutList:
        if obj.Name.startswith("Tools") or obj.Label.startswith("Tools"):
            tools_folder = obj
            break

    tool_names = []
    tool_objects = []
    if tools_folder and hasattr(tools_folder, "Group"):
        for tc in tools_folder.Group:
            if "ToolController" in tc.Proxy.__class__.__name__:
                tool_names.append(tc.Label)
                tool_objects.append(tc)

    if not tool_names:
        App.Console.PrintError("No ToolControllers found in the Job.\n")
        return None

    dialog = QtGui.QDialog(Gui.getMainWindow())
    dialog.setWindowTitle("Profile Operation Settings")
    dialog.setModal(True)

    layout = QtGui.QVBoxLayout(dialog)

    # Tool selection
    tool_layout = QtGui.QHBoxLayout()
    tool_layout.addWidget(QtGui.QLabel("Tool:"))
    tool_combo = QtGui.QComboBox()
    tool_combo.addItems(tool_names)
    tool_combo.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Fixed)
    tool_layout.addWidget(tool_combo)
    layout.addLayout(tool_layout)

    # Offset Side selection
    side_layout = QtGui.QHBoxLayout()
    side_layout.addWidget(QtGui.QLabel("Offset Side:"))
    side_combo = QtGui.QComboBox()
    side_combo.addItems(["Inside", "Outside", "None"])
    side_combo.setCurrentIndex(0)
    side_combo.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Fixed)
    side_layout.addWidget(side_combo)
    layout.addLayout(side_layout)

    # Cut Direction selection
    dir_layout = QtGui.QHBoxLayout()
    dir_layout.addWidget(QtGui.QLabel("Cut Direction:"))
    dir_combo = QtGui.QComboBox()
    dir_combo.addItems(["CW", "CCW"])
    dir_combo.setCurrentIndex(1)
    dir_combo.setSizePolicy(QtGui.QSizePolicy.Expanding, QtGui.QSizePolicy.Fixed)
    dir_layout.addWidget(dir_combo)
    layout.addLayout(dir_layout)

    # LeadInOut Dressup section
    leadin_group = QtGui.QGroupBox("LeadInOut Dressup")
    leadin_layout = QtGui.QVBoxLayout()

    # Lead In row
    leadin_row = QtGui.QHBoxLayout()
    leadin_check = QtGui.QCheckBox()
    leadin_check.setChecked(True)
    leadin_style_combo = QtGui.QComboBox()
    try:
        import Path.Dressup.Gui.LeadInOut as LeadInOutDressup
        leadin_style_combo.addItems(LeadInOutDressup.lead_styles)
        leadin_style_combo.setCurrentIndex(LeadInOutDressup.lead_styles.index("Perpendicular"))
    except ImportError:
        leadin_style_combo.addItems(["Perpendicular", "Tangent", "Direct"])
        leadin_style_combo.setCurrentIndex(0)
    leadin_row.addWidget(QtGui.QLabel("Lead In:"))
    leadin_row.addWidget(leadin_check)
    leadin_row.addSpacing(15)
    leadin_row.addWidget(QtGui.QLabel("Style:"))
    leadin_row.addWidget(leadin_style_combo)
    leadin_row.addStretch()
    leadin_layout.addLayout(leadin_row)

    # Lead Out row
    leadout_row = QtGui.QHBoxLayout()
    leadout_check = QtGui.QCheckBox()
    leadout_check.setChecked(False)
    leadout_style_combo = QtGui.QComboBox()
    try:
        import Path.Dressup.Gui.LeadInOut as LeadInOutDressup
        leadout_style_combo.addItems(LeadInOutDressup.lead_styles)
        leadout_style_combo.setCurrentIndex(LeadInOutDressup.lead_styles.index("Perpendicular"))
    except ImportError:
        leadout_style_combo.addItems(["Perpendicular", "Tangent", "Direct"])
        leadout_style_combo.setCurrentIndex(0)
    leadout_row.addWidget(QtGui.QLabel("Lead Out:"))
    leadout_row.addWidget(leadout_check)
    leadout_row.addSpacing(15)
    leadout_row.addWidget(QtGui.QLabel("Style:"))
    leadout_row.addWidget(leadout_style_combo)
    leadout_row.addStretch()
    leadin_layout.addLayout(leadout_row)

    # Length row
    length_row = QtGui.QHBoxLayout()
    length_spin = QtGui.QDoubleSpinBox()
    length_spin.setRange(0.01, 100.0)
    length_spin.setDecimals(2)
    length_spin.setValue(3.0)
    length_spin.setSingleStep(0.1)
    length_row.addWidget(QtGui.QLabel("Length:"))
    length_row.addWidget(length_spin)
    length_row.addWidget(QtGui.QLabel("× Tool Diameter"))
    length_row.addStretch()
    leadin_layout.addLayout(length_row)

    leadin_group.setLayout(leadin_layout)
    layout.addWidget(leadin_group)

    # Interactivity: disable style dropdowns when checkbox is unchecked
    def on_leadin_state_changed(state):
        leadin_style_combo.setEnabled(state == QtCore.Qt.Checked)

    def on_leadout_state_changed(state):
        leadout_style_combo.setEnabled(state == QtCore.Qt.Checked)

    leadin_check.stateChanged.connect(on_leadin_state_changed)
    leadout_check.stateChanged.connect(on_leadout_state_changed)

    # Initial state
    leadin_style_combo.setEnabled(True)
    leadout_style_combo.setEnabled(False)

    # Concave detection checkbox
    concave_layout = QtGui.QHBoxLayout()
    concave_check = QtGui.QCheckBox()
    concave_check.setChecked(True)
    concave_layout.addStretch()
    concave_layout.addWidget(concave_check)
    concave_layout.addWidget(QtGui.QLabel("Detect concave indentations in outer wire"))
    layout.addLayout(concave_layout)

    # Concave depth threshold
    depth_layout = QtGui.QHBoxLayout()
    depth_spin = QtGui.QDoubleSpinBox()
    depth_spin.setRange(0.1, 100.0)
    depth_spin.setValue(5.0)
    depth_spin.setSuffix(" mm")
    depth_spin.setDecimals(1)
    depth_layout.addStretch()
    depth_layout.addWidget(depth_spin)
    depth_layout.addWidget(QtGui.QLabel("Min depth for concave detection"))
    layout.addLayout(depth_layout)

    # Button row
    button_layout = QtGui.QHBoxLayout()
    ok_button = QtGui.QPushButton("OK")
    cancel_button = QtGui.QPushButton("Cancel")
    button_layout.addStretch()
    button_layout.addWidget(ok_button)
    button_layout.addWidget(cancel_button)
    layout.addLayout(button_layout)

    # Default values
    selected = {
        "tool": tool_objects[0],
        "side": "Inside",
        "direction": "CCW",
        "accepted": False,
        "leadIn": True,
        "leadOut": False,
        "styleIn": "Perpendicular",
        "styleOut": "Perpendicular",
        "lengthMultiplier": 3.0,
        "concaveDetection": True,
        "concaveDepthTol": 5.0,
    }

    def on_ok():
        selected["accepted"] = True
        selected["tool"] = tool_objects[tool_combo.currentIndex()]
        selected["side"] = side_combo.currentText()
        selected["direction"] = dir_combo.currentText()
        selected["leadIn"] = leadin_check.isChecked()
        selected["leadOut"] = leadout_check.isChecked()
        selected["styleIn"] = leadin_style_combo.currentText()
        selected["styleOut"] = leadout_style_combo.currentText()
        selected["lengthMultiplier"] = length_spin.value()
        selected["concaveDetection"] = concave_check.isChecked()
        selected["concaveDepthTol"] = depth_spin.value()
        dialog.accept()

    def on_cancel():
        dialog.reject()

    ok_button.clicked.connect(on_ok)
    cancel_button.clicked.connect(on_cancel)

    if dialog.exec() == QtGui.QDialog.Accepted:
        if DEBUG:
            _dbg(
                "  [DIALOG] User accepted: tool='{}', side='{}', dir='{}', "
                "leadIn={}, leadOut={}, styleIn={}, styleOut={}, lengthMult={}, "
                "concave={}, concaveDepthTol={}\n".format(
                    selected["tool"].Name if selected["tool"] else "None",
                    selected["side"],
                    selected["direction"],
                    selected["leadIn"],
                    selected["leadOut"],
                    selected["styleIn"],
                    selected["styleOut"],
                    selected["lengthMultiplier"],
                    selected["concaveDetection"],
                    selected["concaveDepthTol"],
                ),
            )
        return selected

    if DEBUG:
        _dbg("  [DIALOG] User cancelled\n")
    return None


def add_leadinout_dressup(profile_op, leadIn=True, leadOut=False, styleIn="Perpendicular",
                          styleOut="Perpendicular", lengthMultiplier=3.0):
    """Add and configure a DressupLeadInOut dressup for the given profile op."""
    try:
        if DEBUG:
            _dbg(
                f"  [DRESSUP] Creating dressup for op='{profile_op.Name}', leadIn={leadIn}, leadOut={leadOut}, "
                f"styleIn={styleIn}, styleOut={styleOut}, lengthMult={lengthMultiplier}\n",
            )
        import Path.Dressup.Gui.LeadInOut as LeadInOutDressup
        dressup = LeadInOutDressup.Create(profile_op, mode=2)
        if dressup is None:
            App.Console.PrintError("  [DRESSUP] Create() returned None!\n")
            return None
        if DEBUG:
            _dbg(
                f"  [DRESSUP] Created dressup object: {dressup.Name}\n",
            )
        dressup.LeadIn = leadIn
        dressup.LeadOut = leadOut
        dressup.StyleIn = styleIn
        dressup.StyleOut = styleOut
        if DEBUG:
            _dbg(
                f"  [DRESSUP] Set LeadIn={dressup.LeadIn}, LeadOut={dressup.LeadOut}, StyleIn={dressup.StyleIn}, StyleOut={dressup.StyleOut}\n",
            )

        from Path.Dressup import Utils as PathDressup
        baseOp = PathDressup.baseOp(dressup.Base)
        if baseOp and getattr(baseOp, "ToolController", None):
            expr = f"{baseOp.Name}.ToolController.Tool.Diameter.Value*{lengthMultiplier}"
            dressup.setExpression("RadiusIn", expr)
            dressup.setExpression("RadiusOut", expr)
            if DEBUG:
                _dbg(
                    f"  [DRESSUP] Set expressions: RadiusIn={dressup.RadiusIn}, RadiusOut={dressup.RadiusOut}\n",
                )
        else:
            dressup.RadiusIn = lengthMultiplier
            dressup.RadiusOut = lengthMultiplier
            if DEBUG:
                _dbg(
                    f"  [DRESSUP] No tool controller found, set RadiusIn={lengthMultiplier}, RadiusOut={lengthMultiplier} (raw)\n",
                )

        if DEBUG:
            _dbg(
                f"  [DRESSUP] Final: RadiusIn={dressup.RadiusIn}, RadiusOut={dressup.RadiusOut}\n",
            )
        return dressup
    except Exception as e:  # noqa: BLE001
        App.Console.PrintError(f"  [DRESSUP] ERROR: {e}\n")
        import traceback
        App.Console.PrintError(traceback.format_exc())
        return None


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

        chains = []
        current_chain = [concave_indices[0]]
        for j in range(1, len(concave_indices)):
            if concave_indices[j] == concave_indices[j - 1] + 1:
                current_chain.append(concave_indices[j])
            else:
                if len(current_chain) >= 2:
                    chains.append([edges[idx] for idx in current_chain])
                current_chain = [concave_indices[j]]
        if len(current_chain) >= 2:
            chains.append([edges[idx] for idx in current_chain])

        # Check if concave edges wrap around the wire boundary
        if len(concave_indices) >= 2:
            first_idx = concave_indices[0]
            last_idx = concave_indices[-1]

            # Case 1: chain starts at last edge and continues at first edge
            # e.g., [8, 0, 1, 2]
            if first_idx == len(edges) - 1 and concave_indices[1] == 0:
                wrap_chain = [edges[idx] for idx in concave_indices]
                if DEBUG:
                    _dbg(
                        f"  [CONCAVE_CHAINS] Full-wire wrap: {len(wrap_chain)} edges\n",
                    )
                chains = [wrap_chain]

            # Case 2: chain starts at first edge and wraps to last edge
            # e.g., [0, 5] or [0, 1, 5]
            elif first_idx == 0 and last_idx == len(edges) - 1:
                wrap_chain = [edges[idx] for idx in concave_indices]
                if DEBUG:
                    _dbg(
                        f"  [CONCAVE_CHAINS] Full-wire wrap: {len(wrap_chain)} edges\n",
                    )
                chains = [wrap_chain]

        if DEBUG:
            _dbg(
                f"  [CONCAVE_CHAINS] Returning {len(chains)} valid chain(s)\n",
            )
        return chains
    except Exception as e:  # noqa: BLE001
        App.Console.PrintError(f"  [CONCAVE_CHAINS] ERROR: {e}\n")
        import traceback
        App.Console.PrintError(traceback.format_exc())
        return []


def create_profile_operation(op_name, job, tool_controller, edge_names, model_clone,
                             offset_side, cut_direction, leadIn, leadOut, styleIn, styleOut,
                             lengthMultiplier):
    """Create a CAM Profile operation for the given edges and attach a LeadInOut dressup.

    This is the shared factory used by both the closed-loop and concave-chain
    code paths in create_profile_ops_for_top_loops().

    Args:
        op_name: Display name for the operation (e.g. "Profile_Loop_3").
        job: The CAM Job DocumentObject that will own this operation.
        tool_controller: A ToolController object defining the cutting tool.
        edge_names: List of edge sub-element names (e.g. ["Edge1", "Edge2", "Edge3"])
            referencing edges on model_clone.
        model_clone: The model clone DocumentObject whose Shape contains the edges.
        offset_side: "Inside", "Outside", or "None" — controls edge offset compensation.
        cut_direction: "CW" or "CCW" — tool travel direction around the profile.
        leadIn: Whether to enable LeadIn on the dressup.
        leadOut: Whether to enable LeadOut on the dressup.
        styleIn: LeadIn style string (e.g. "Perpendicular", "Tangent").
        styleOut: LeadOut style string.
        lengthMultiplier: LeadIn/Out length as a multiple of tool diameter.

    Returns:
        The created Profile DocumentObject, or None if creation failed.
    """
    import Path.Op.Profile as PathProfileOp
    import Path.Op.Gui.Profile as PathProfileGui
    import Path.Op.Gui.Base as PathOpGui

    profile_op = PathProfileOp.Create(op_name, parentJob=job)
    if profile_op is None:
        App.Console.PrintError(f"  [CREATE] Profile.Create() returned None for '{op_name}'\n")
        return None

    if DEBUG:
        _dbg(
            "  [CREATE] Created op: {}, type={}, hasProxy={}\n".format(
                profile_op.Name, type(profile_op).__name__,
                hasattr(profile_op, "Proxy"),
            ),
        )
        _dbg(
            "  After Create:\n"
            "    op.Base = {}\n"
            "    op.Proxy = {}\n"
            "    op.Proxy.job = {}\n".format(
                profile_op.Base, profile_op.Proxy,
                profile_op.Proxy.job if hasattr(profile_op.Proxy, "job") else "N/A",
            ),
        )

    profile_op.ToolController = tool_controller
    if DEBUG:
        _dbg(f"  ToolController set to {tool_controller.Name}\n")

    # PathProfileGui.Command.res provides the resource path required by the
    # ViewProvider constructor. This is a FreeCAD convention for linking GUI
    # view providers to their underlying command definitions.
    res = PathProfileGui.Command.res
    profile_op.ViewObject.Proxy = PathOpGui.ViewProvider(profile_op.ViewObject, res)
    # Prevent FreeCAD from deleting this operation if the user rejects it in the GUI.
    profile_op.ViewObject.Proxy.setDeleteObjectsOnReject(False)

    # Build the Base assignment: each entry is a (DocumentObject, [sub-element names]) tuple.
    # FreeCAD expects this format to associate geometric sub-elements with the operation.
    base_list = []
    for edge_name in edge_names:
        base_list.append((model_clone, [edge_name]))

    if DEBUG:
        _dbg(f"  Setting Base = {base_list}\n")
    profile_op.Base = base_list

    if DEBUG:
        _dbg(
            "  After assignment:\n"
            f"    profile_op.Base = {profile_op.Base}\n"
            f"    type(profile_op.Base) = {type(profile_op.Base)}\n"
            f"    len(profile_op.Base) = {len(profile_op.Base) if profile_op.Base else 0}\n",
        )

    if profile_op.Base:
        for base_obj, subs in profile_op.Base:
            for sub in subs:
                try:
                    elem = base_obj.Shape.getElement(sub)
                    if DEBUG:
                        _dbg(f"    VALID: {base_obj.Name} -> {sub} = {type(elem).__name__}\n")
                except Exception as e:  # noqa: BLE001
                    App.Console.PrintError(f"    INVALID: {base_obj.Name} -> {sub} ERROR: {e}\n")

    profile_op.Direction = cut_direction
    if offset_side == "None":
        # No edge offset compensation — cut exactly on the edge geometry.
        profile_op.UseComp = False
        profile_op.OffsetExtra.Value = 0.0
    else:
        # Enable edge offset compensation (kerf correction).
        profile_op.UseComp = True
        profile_op.Side = "Inside" if offset_side == "Inside" else "Outside"

    if DEBUG:
        _dbg(
            f"  Settings: Side='{profile_op.Side}', Direction='{profile_op.Direction}', UseComp={profile_op.UseComp}\n",
        )

    # Default cutting parameters (all values in mm).
    profile_op.ClearanceHeight = 5.0   # Z-height for rapid travel above workpiece
    profile_op.SafeHeight = 3.0       # Z-height for linear (non-rapid) travel
    profile_op.StartDepth = 0.0       # Z-depth where cutting begins
    profile_op.StepDown = 1.0         # Depth increment per pass (mm)
    profile_op.FinalDepth = -2.0      # Final cutting depth (mm, negative = below surface)

    dressup = add_leadinout_dressup(
        profile_op,
        leadIn=leadIn,
        leadOut=leadOut,
        styleIn=styleIn,
        styleOut=styleOut,
        lengthMultiplier=lengthMultiplier,
    )
    if dressup is not None:
        if DEBUG:
            _dbg(f"  [DRESSUP] Attached to '{op_name}': {dressup.Name}\n")
    else:
        App.Console.PrintError(f"  [DRESSUP] Failed to create dressup for '{op_name}'\n")

    return profile_op


def create_progress_dialog(max_value):
    """Create and return a QProgressDialog for the Find Profiles operation."""
    dialog = QProgressDialog(
        "Starting analysis...",
        "Cancel",
        0,
        max_value,
        Gui.getMainWindow(),
    )
    dialog.setWindowModality(QtCore.Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.show()
    QApplication.processEvents()
    return dialog


def create_profile_ops_for_top_loops():
    """Create Profile operations for CAM Job top faces."""
    if DEBUG:
        with open(_DEBUG_LOG, "w") as f:
            pass
        _dbg("=== MACRO START ===\n")

    selection_ex = Gui.Selection.getSelectionEx()
    if not selection_ex:
        App.Console.PrintError("Please select a CAM Job in the tree first.\n")
        return

    job = selection_ex[0].Object
    if DEBUG:
        _dbg(f"  Selected job: '{job.Name}' (type: {type(job).__name__})\n")

    if not hasattr(job, "Proxy") or "Job" not in job.Proxy.__class__.__name__:
        App.Console.PrintError("Selected object is not a CAM Job.\n")
        return

    model_folder = None
    for obj in job.OutList:
        if obj.Name.startswith("Model") or obj.Label.startswith("Model"):
            model_folder = obj
            break

    if not model_folder or not hasattr(model_folder, "Group"):
        App.Console.PrintError("Could not locate Model folder in the job.\n")
        return

    model_clones = []
    for child in model_folder.Group:
        if hasattr(child, "Shape") and child.Shape:
            source = child.LinkTo if hasattr(child, "LinkTo") and child.LinkTo else child
            model_clones.append((child, source))

    if not model_clones:
        App.Console.PrintError("No geometry found in the Model folder.\n")
        return

    model_clone, source_obj = model_clones[0]
    source_edges = source_obj.Shape.Edges

    if DEBUG:
        _dbg(
            "=== SETUP ===\n"
            f"Targeting geometry: '{model_clone.Label}' (Source: '{source_obj.Name}')\n"
            f"Model clone: '{model_clone.Name}'\n"
            f"Clone has {len(model_clone.Shape.Edges)} edges\n"
            f"Source has {len(source_edges)} edges\n"
        )
        for i, me in enumerate(source_edges):
            _dbg(
                f"  source Edge{i+1}: shapeType={me.ShapeType}, "
                f"p0=({me.Vertexes[0].Point.x:.4f},{me.Vertexes[0].Point.y:.4f},{me.Vertexes[0].Point.z:.4f}), "
                f"p1=({me.Vertexes[-1].Point.x:.4f},{me.Vertexes[-1].Point.y:.4f},{me.Vertexes[-1].Point.z:.4f})\n"
            )

    # Build fast hash-based lookup: wire edges come from source_obj.Shape,
    # so we match against source_edges by hashCode to get the index,
    # then use that index to reference the corresponding clone edge.
    src_hash_to_index = {e.hashCode(): i for i, e in enumerate(source_edges)}
    if DEBUG:
        _dbg(f"Built source edge hash map: {len(src_hash_to_index)} edges\n")

    top_faces = []
    for face in source_obj.Shape.Faces:
        u_min, u_max, v_min, v_max = face.ParameterRange
        u_mid = u_min + (u_max - u_min) / 2.0
        v_mid = v_min + (v_max - v_min) / 2.0
        normal = face.normalAt(u_mid, v_mid)
        if normal.z > 0.99:
            top_faces.append((face, normal))

    if DEBUG:
        _dbg(f"Found {len(top_faces)} top faces\n")

    if not top_faces:
        App.Console.PrintWarning("No top-facing flat planes found on the model.\n")
        return

    settings = show_profile_settings_dialog(job)
    if not settings:
        App.Console.PrintWarning("Operation cancelled by user.\n")
        return

    tool_controller = settings["tool"]
    offset_side = settings["side"]
    cut_direction = settings["direction"]
    leadIn = settings["leadIn"]
    leadOut = settings["leadOut"]
    styleIn = settings["styleIn"]
    styleOut = settings["styleOut"]
    lengthMultiplier = settings["lengthMultiplier"]

    if DEBUG:
        _dbg(
            f"Settings: Tool='{tool_controller.Name}', Side='{offset_side}', Direction='{cut_direction}', LeadIn={leadIn}, LeadOut={leadOut}, "
            f"StyleIn={styleIn}, StyleOut={styleOut}, LengthMult={lengthMultiplier}\n",
        )

    # Count total wires for progress bar granularity
    total_wires = 0
    for face, normal in top_faces:
        outer_hash = face.OuterWire.hashCode()
        for wire in face.Wires:
            if wire.hashCode() == outer_hash:
                continue
            if not wire.isClosed():
                continue
            total_wires += 1

    if DEBUG:
        _dbg(f"Total wires to process: {total_wires}\n")

    from PathScripts import PathUtils
    op_count = 0
    closed_loop_count = 0
    concave_total = 0
    doc = App.activeDocument()

    original_UserInput = PathUtils.UserInput

    class SilentToolControllerChooser:
        def selectedToolController(self):
            return None
        def chooseToolController(self, controllers):
            return tool_controller

    PathUtils.UserInput = SilentToolControllerChooser()

    try:
        doc.openTransaction("Create Profile Loops")

        # Create progress dialog
        progress_dialog = create_progress_dialog(total_wires)
        wire_progress = 0
        cancelled_list = [False]

        def on_dialog_closed(_result=0):
            cancelled_list[0] = True

        progress_dialog.finished.connect(on_dialog_closed)

        for face_idx, (face, normal) in enumerate(top_faces):
            if cancelled_list[0]:
                break

            if DEBUG:
                _dbg(
                    f"  Processing face with normal_z={normal.z:.6f}\n",
                )
            outer_hash = face.OuterWire.hashCode()
            for wire in face.Wires:
                if cancelled_list[0]:
                    break

                if wire.hashCode() == outer_hash:
                    continue
                if not wire.isClosed():
                    continue

                wire_progress += 1

                if DEBUG:
                    _dbg(
                        f"  Wire ({len(wire.Edges)} edges, closed={wire.isClosed()}):\n"
                    )
                    for i, e in enumerate(wire.Edges):
                        _dbg(
                            f"    wire edge {i+1}: shapeType={e.ShapeType}, "
                            f"p0=({e.Vertexes[0].Point.x:.4f},{e.Vertexes[0].Point.y:.4f},{e.Vertexes[0].Point.z:.4f}), "
                            f"p1=({e.Vertexes[-1].Point.x:.4f},{e.Vertexes[-1].Point.y:.4f},{e.Vertexes[-1].Point.z:.4f})\n"
                        )

                edge_names = []
                for edge in wire.Edges:
                    src_idx = src_hash_to_index.get(edge.hashCode())
                    if src_idx is not None:
                        edge_names.append(f"Edge{src_idx + 1}")
                        if DEBUG:
                            _dbg(f"    MATCH: wire edge -> master Edge{src_idx + 1}\n")
                    elif DEBUG:
                        _dbg(f"    NO MATCH: wire edge (hashCode={edge.hashCode()})\n")

                if DEBUG:
                    _dbg(
                        f"  Wire: {len(wire.Edges)} edges, matched: {len(edge_names)}\n"
                    )

                if len(edge_names) < 2:
                    # Update progress even for skipped wires
                    progress_dialog.setValue(wire_progress)
                    progress_dialog.setLabelText(
                        f"Processing face {face_idx + 1} of {len(top_faces)}"
                    )
                    QApplication.processEvents()
                    continue

                op_count += 1
                closed_loop_count += 1
                op_name = f"Profile_Loop_{op_count}"

                progress_dialog.setLabelText(
                    f"Creating {op_name}..."
                )
                QApplication.processEvents()

                profile_op = create_profile_operation(
                    op_name, job, tool_controller, edge_names, model_clone,
                    offset_side, cut_direction, leadIn, leadOut, styleIn, styleOut,
                    lengthMultiplier,
                )
                if profile_op is None:
                    continue

                if DEBUG:
                    _dbg(
                        f"Created {op_name} linked to {source_obj.Name} ({len(edge_names)} edges)\n",
                    )

                progress_dialog.setValue(wire_progress)
                progress_dialog.setLabelText(
                    f"Processing face {face_idx + 1} of {len(top_faces)} — {op_name} created"
                )
                QApplication.processEvents()

                if progress_dialog.wasCanceled():
                    cancelled_list[0] = True
                    break

            if cancelled_list[0]:
                break

            if settings["concaveDetection"]:
                if cancelled_list[0]:
                    break

                if DEBUG:
                    _dbg(
                        f"  [CONCAVE] Processing outer wire of face (normal_z={normal.z:.6f})\n",
                    )
                concave_chains = find_concave_chains(face.OuterWire, normal, settings["concaveDepthTol"])
                concave_count = len(concave_chains)
                if DEBUG:
                    _dbg(
                        f"  Outer wire: {concave_count} concave indentation chain(s) found\n",
                    )

                for chain in concave_chains:
                    if cancelled_list[0]:
                        break

                    try:
                        edge_names = []
                        for edge in chain:
                            src_idx = src_hash_to_index.get(edge.hashCode())
                            if src_idx is not None:
                                edge_names.append(f"Edge{src_idx + 1}")

                        if DEBUG:
                            _dbg(
                                f"  Concave chain: {len(chain)} edges, matched: {len(edge_names)}\n",
                            )

                        if len(edge_names) < 2:
                            if DEBUG:
                                _dbg(
                                    "  [CONCAVE] Skipping chain: < 2 matched edges\n",
                                )
                            continue

                        import Part
                        try:
                            matched_edges = []
                            for edge_name in edge_names:
                                elem = model_clone.Shape.getElement(edge_name)
                                if elem:
                                    matched_edges.append(elem)
                            if len(matched_edges) >= 2:
                                test_wire = Part.Wire(Part.__sortEdges__(matched_edges))
                                if test_wire.isClosed():
                                    if DEBUG:
                                        _dbg(
                                            "  [CONCAVE] Skipping chain: forms closed wire "
                                            "(will be mishandled as open profile)\n",
                                        )
                                    continue
                        except Exception as e:  # noqa: BLE001
                            if DEBUG:
                                _dbg(
                                    f"  [CONCAVE] Wire validation failed: {e}. Skipping chain.\n",
                                )
                            continue

                        concave_total += 1
                        op_count += 1
                        op_name = f"Profile_Concave_{op_count}"

                        progress_dialog.setLabelText(
                            f"Creating {op_name}..."
                        )
                        QApplication.processEvents()

                        profile_op = create_profile_operation(
                            op_name, job, tool_controller, edge_names, model_clone,
                            offset_side, cut_direction, leadIn, leadOut, styleIn, styleOut,
                            lengthMultiplier,
                        )
                        if profile_op is None:
                            continue

                        if DEBUG:
                            _dbg(
                                f"Created {op_name} linked to {source_obj.Name} ({len(edge_names)} edges)\n",
                            )

                        progress_dialog.setValue(wire_progress)
                        progress_dialog.setLabelText(
                            f"Processing face {face_idx + 1} of {len(top_faces)} — {op_name} created"
                        )
                        QApplication.processEvents()

                        if progress_dialog.wasCanceled():
                            cancelled_list[0] = True
                            break

                    except Exception as e:  # noqa: BLE001
                        App.Console.PrintError(
                            f"  [CONCAVE] ERROR processing chain: {e}\n",
                        )
                        import traceback
                        App.Console.PrintError(traceback.format_exc())
                        continue

        doc.commitTransaction()
        doc.recompute()
    finally:
        PathUtils.UserInput = original_UserInput
        Gui.Control.closeDialog()

    if cancelled_list[0]:
        App.Console.PrintWarning(
            f"Operation cancelled. Created {op_count} profile operations.\n"
        )
    else:
        App.Console.PrintMessage(
            f"Finished! Created {op_count} profile operations "
            f"({closed_loop_count} closed loops, {concave_total} concave indentations).\n"
        )

    if DEBUG:
        _dbg("\n=== POST-CREATION VERIFICATION ===\n")
    for obj in job.OutList:
        if obj.Name.startswith("Operations") or obj.Label.startswith("Operations"):
            for op in obj.Group:
                if hasattr(op, "Base"):
                    if "Dressup" in op.Name:
                        if DEBUG:
                            _dbg(
                                f"Dressup '{op.Name}': Base={op.Base}, LeadIn={op.LeadIn}, LeadOut={op.LeadOut}, StyleIn={op.StyleIn}, RadiusIn={op.RadiusIn}\n",
                            )
                    elif DEBUG:
                        _dbg(
                            f"Op '{op.Name}': Base = {op.Base}, Side={op.Side}, Direction={op.Direction}\n",
                        )

    if DEBUG:
        _dbg(
            f"Finished! Created {op_count} profile operations ({closed_loop_count} closed loops, {concave_total} concave indentations).\n",
        )
