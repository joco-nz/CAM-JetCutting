"""GuiCommand classes for JetCutter Tools."""

import FreeCAD as App
import FreeCADGui as Gui

DEBUG = False


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
        has_edge = any(sub.startswith("Edge") for sub in first_sel.SubElementNames)
        return has_edge

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
                'length': edge.Length,
            })
            target_z_positions.append(edge.CenterOfMass.z)

        if not target_edges_data:
            App.Console.PrintWarning("None of the selected elements are edges.\n")
            return

        avg_target_z = sum(target_z_positions) / len(target_z_positions)
        if DEBUG:
            App.Console.PrintMessage(
                f"Template profile: {len(target_edges_data)} edges at Global Z-level: {avg_target_z:.4f} mm\n"
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
                        if abs(edge.Length - target['length']) <= TOLERANCE:
                            is_match = True
                            break

                    if is_match:
                        matched_edge_names.append(f"Edge{i+1}")
                        matched_on_this_obj += 1
                        total_matched_count += 1

            if matched_on_this_obj > 0:
                Gui.Selection.addSelection(obj, tuple(matched_edge_names))
                if DEBUG:
                    App.Console.PrintMessage(
                        f"Found {matched_on_this_obj} matching edges in visible object: '{obj.Label}'\n"
                    )

        if DEBUG:
            App.Console.PrintMessage(
                f"Done! Successfully selected {total_matched_count} matching edges across visible solids.\n"
            )

    def GetResources(self):
        return {
            'Pixmap': 'same-edges-as-highlighted',
            'MenuText': 'Same Edges As Highlighted',
            'ToolTip': 'Select all edges matching the length and Z-level of highlighted edges',
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
            'Pixmap': 'FindProfiles',
            'MenuText': 'Find Profiles',
            'ToolTip': 'Create Profile operations for internal edges on CAM Job top faces',
        }


# ============================================================================
# Dialog and helpers for FindProfiles
# ============================================================================

def show_profile_settings_dialog(job):
    """Show a dialog to select Tool, Offset Side, Cut Direction, and LeadInOut settings."""
    try:
        from PySide import QtGui, QtCore
    except ImportError:
        from PySide6 import QtGui, QtCore

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
            App.Console.PrintMessage(
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
                    selected["concaveDepthTol"]
                )
            )
        return selected

    if DEBUG:
        App.Console.PrintMessage("  [DIALOG] User cancelled\n")
    return None


def add_leadinout_dressup(profile_op, leadIn=True, leadOut=False, styleIn="Perpendicular",
                          styleOut="Perpendicular", lengthMultiplier=3.0):
    """Add and configure a DressupLeadInOut dressup for the given profile op."""
    try:
        if DEBUG:
            App.Console.PrintMessage(
                "  [DRESSUP] Creating dressup for op='{}', leadIn={}, leadOut={}, "
                "styleIn={}, styleOut={}, lengthMult={}\n".format(
                    profile_op.Name, leadIn, leadOut, styleIn, styleOut, lengthMultiplier
                )
            )
        import Path.Dressup.Gui.LeadInOut as LeadInOutDressup
        dressup = LeadInOutDressup.Create(profile_op, mode=2)
        if dressup is None:
            App.Console.PrintError("  [DRESSUP] Create() returned None!\n")
            return None
        if DEBUG:
            App.Console.PrintMessage(
                "  [DRESSUP] Created dressup object: {}\n".format(dressup.Name)
            )
        dressup.LeadIn = leadIn
        dressup.LeadOut = leadOut
        dressup.StyleIn = styleIn
        dressup.StyleOut = styleOut
        if DEBUG:
            App.Console.PrintMessage(
                "  [DRESSUP] Set LeadIn={}, LeadOut={}, StyleIn={}, StyleOut={}\n".format(
                    dressup.LeadIn, dressup.LeadOut, dressup.StyleIn, dressup.StyleOut
                )
            )

        import Path.Dressup as PathDressup
        baseOp = PathDressup.baseOp(dressup.Base)
        if baseOp and getattr(baseOp, "ToolController", None):
            toolDiameter = baseOp.ToolController.Tool.Diameter.Value
            expr = "{}.ToolController.Tool.Diameter.Value*{}".format(
                baseOp.Name, lengthMultiplier)
            dressup.setExpression("RadiusIn", expr)
            dressup.setExpression("RadiusOut", expr)
            if DEBUG:
                App.Console.PrintMessage(
                    "  [DRESSUP] Set expressions: RadiusIn={}, RadiusOut={}\n".format(
                        dressup.RadiusIn, dressup.RadiusOut
                    )
                )
        else:
            dressup.RadiusIn = lengthMultiplier
            dressup.RadiusOut = lengthMultiplier
            if DEBUG:
                App.Console.PrintMessage(
                    "  [DRESSUP] No tool controller found, set RadiusIn={}, RadiusOut={} (raw)\n".format(
                        lengthMultiplier, lengthMultiplier
                    )
                )

        if DEBUG:
            App.Console.PrintMessage(
                "  [DRESSUP] Final: RadiusIn={}, RadiusOut={}\n".format(
                    dressup.RadiusIn, dressup.RadiusOut
                )
            )
        return dressup
    except Exception as e:  # noqa: BLE001
        App.Console.PrintError("  [DRESSUP] ERROR: {}\n".format(e))
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
                bb_max_y - v.Y
            )
            if dist > depth_tol:
                any_deep = True
                break

        return any_deep
    except (AttributeError, IndexError) as e:
        if DEBUG:
            App.Console.PrintMessage(
                "  [CONCAVE] Edge vertex access error: {}\n".format(e)
            )
        return False


def find_concave_chains(wire, face_normal, concave_depth_tol=5.0):
    """Find edge chains forming concave indentations in a wire."""
    try:
        if DEBUG:
            App.Console.PrintMessage("  [CONCAVE_CHAINS] Starting concave chain detection\n")
        edges = wire.Edges
        if DEBUG:
            App.Console.PrintMessage(
                "  [CONCAVE_CHAINS] Wire has {} edges, normal_z={:.6f}\n".format(
                    len(edges), face_normal.z
                )
            )
        if len(edges) < 2:
            if DEBUG:
                App.Console.PrintMessage("  [CONCAVE_CHAINS] Not enough edges (< 2), returning []\n")
            return []

        bb_min_x = bb_min_y = float('inf')
        bb_max_x = bb_max_y = float('-inf')
        for edge in edges:
            for v in edge.Vertexes:
                bb_min_x = min(bb_min_x, v.X)
                bb_max_x = max(bb_max_x, v.X)
                bb_min_y = min(bb_min_y, v.Y)
                bb_max_y = max(bb_max_y, v.Y)

        if DEBUG:
            App.Console.PrintMessage(
                "  [CONCAVE_CHAINS] Bounding box: x=[{:.2f}, {:.2f}], y=[{:.2f}, {:.2f}], concaveDepthTol={:.1f}\n".format(
                    bb_min_x, bb_max_x, bb_min_y, bb_max_y, concave_depth_tol
                )
            )

        concave_indices = []
        for i, edge in enumerate(edges):
            is_concave = is_concave_indentation(edge, bb_min_x, bb_max_x, bb_min_y, bb_max_y, concave_depth_tol)
            if is_concave:
                concave_indices.append(i)
            if DEBUG:
                App.Console.PrintMessage(
                    "  [CONCAVE] Edge{}: index={}, concave={}\n".format(
                        i + 1, i, is_concave
                    )
                )

        if DEBUG:
            App.Console.PrintMessage(
                "  [CONCAVE_CHAINS] Found {} concave edge(s) at indices: {}\n".format(
                    len(concave_indices), concave_indices
                )
            )
        if DEBUG:
            App.Console.PrintMessage(
                "  [CONCAVE_CHAINS] Boundary edges: {}, Concave edges: {}\n".format(
                    len(edges) - len(concave_indices), len(concave_indices)
                )
            )

        if len(concave_indices) < 2:
            if DEBUG:
                App.Console.PrintMessage("  [CONCAVE_CHAINS] Need at least 2 concave edges, returning []\n")
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

        if concave_indices[0] == len(edges) - 1 and concave_indices[1] == 0:
            wrap_chain = [edges[idx] for idx in concave_indices]
            if DEBUG:
                App.Console.PrintMessage(
                    "  [CONCAVE_CHAINS] Full-wire wrap: {} edges\n".format(len(wrap_chain))
                )
            chains = [wrap_chain]

        if DEBUG:
            App.Console.PrintMessage(
                "  [CONCAVE_CHAINS] Returning {} valid chain(s)\n".format(len(chains))
            )
        return chains
    except Exception as e:  # noqa: BLE001
        App.Console.PrintError("  [CONCAVE_CHAINS] ERROR: {}\n".format(e))
        import traceback
        App.Console.PrintError(traceback.format_exc())
        return []


def create_profile_ops_for_top_loops():
    """Main entry point for FindProfiles command."""
    if DEBUG:
        App.Console.PrintMessage("=== MACRO START ===\n")

    selection_ex = Gui.Selection.getSelectionEx()
    if not selection_ex:
        App.Console.PrintError("Please select a CAM Job in the tree first.\n")
        return

    job = selection_ex[0].Object
    if DEBUG:
        App.Console.PrintMessage("  Selected job: '{}' (type: {})\n".format(job.Name, type(job).__name__))

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
    master_edges = model_clone.Shape.Edges

    if DEBUG:
        App.Console.PrintMessage(
            "=== SETUP ===\n"
            "Targeting geometry: '{}' (Source: '{}')\n"
            "Model clone: '{}'\n"
            "Clone has {} edges\n"
            "Source has {} edges\n".format(
                model_clone.Label, source_obj.Name,
                model_clone.Name, len(master_edges),
                len(source_obj.Shape.Edges)
            )
        )

    top_faces = []
    for face in source_obj.Shape.Faces:
        u_min, u_max, v_min, v_max = face.ParameterRange
        u_mid = u_min + (u_max - u_min) / 2.0
        v_mid = v_min + (v_max - v_min) / 2.0
        normal = face.normalAt(u_mid, v_mid)
        if normal.z > 0.99:
            top_faces.append((face, normal))

    if DEBUG:
        App.Console.PrintMessage("Found {} top faces\n".format(len(top_faces)))

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
        App.Console.PrintMessage(
            "Settings: Tool='{}', Side='{}', Direction='{}', LeadIn={}, LeadOut={}, "
            "StyleIn={}, StyleOut={}, LengthMult={}\n".format(
                tool_controller.Name, offset_side, cut_direction,
                leadIn, leadOut, styleIn, styleOut, lengthMultiplier
            )
        )

    import Path.Op.Gui.Profile as PathProfileGui
    res = PathProfileGui.Command.res

    import PathScripts.PathUtils as PathUtils
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

        for face, normal in top_faces:
            if DEBUG:
                App.Console.PrintMessage(
                    "  Processing face with normal_z={:.6f}\n".format(normal.z)
                )
            outer_hash = face.OuterWire.hashCode()
            for wire in face.Wires:
                if wire.hashCode() == outer_hash:
                    continue
                if not wire.isClosed():
                    continue

                edge_names = []
                for edge in wire.Edges:
                    for idx, master_edge in enumerate(master_edges):
                        if master_edge.isEqual(edge):
                            edge_names.append("Edge{}".format(idx + 1))
                            break

                if DEBUG:
                    App.Console.PrintMessage(
                        "  Wire: {} edges, matched: {}\n".format(
                            len(wire.Edges), len(edge_names)
                        )
                    )

                if len(edge_names) < 2:
                    continue

                op_count += 1
                closed_loop_count += 1
                op_name = "Profile_Loop_{}".format(op_count)

                if DEBUG:
                    App.Console.PrintMessage(
                        "=== CREATING {} ===\n".format(op_name)
                    )

                import Path.Op.Profile as PathProfileOp
                profile_op = PathProfileOp.Create(op_name, parentJob=job)
                if profile_op is None:
                    App.Console.PrintError(
                        "  [CREATE] Profile.Create() returned None for '{}'\n".format(op_name)
                    )
                    continue
                if DEBUG:
                    App.Console.PrintMessage(
                        "  [CREATE] Created op: {}, type={}, hasProxy={}\n".format(
                            profile_op.Name, type(profile_op).__name__,
                            hasattr(profile_op, "Proxy")
                        )
                    )

                if DEBUG:
                    App.Console.PrintMessage(
                        "  After Create:\n"
                        "    op.Base = {}\n"
                        "    op.Proxy = {}\n"
                        "    op.Proxy.job = {}\n".format(
                            profile_op.Base, profile_op.Proxy,
                            profile_op.Proxy.job if hasattr(profile_op.Proxy, 'job') else "N/A"
                        )
                    )

                profile_op.ToolController = tool_controller
                if DEBUG:
                    App.Console.PrintMessage("  ToolController set to {}\n".format(tool_controller.Name))

                import Path.Op.Gui.Base as PathOpGui
                profile_op.ViewObject.Proxy = PathOpGui.ViewProvider(profile_op.ViewObject, res)
                profile_op.ViewObject.Proxy.setDeleteObjectsOnReject(False)

                base_list = []
                for edge_name in edge_names:
                    base_list.append((model_clone, [edge_name]))

                if DEBUG:
                    App.Console.PrintMessage(
                        "  Setting Base = {}\n".format(base_list)
                    )
                profile_op.Base = base_list

                if DEBUG:
                    App.Console.PrintMessage(
                        "  After assignment:\n"
                        "    profile_op.Base = {}\n"
                        "    type(profile_op.Base) = {}\n"
                        "    len(profile_op.Base) = {}\n".format(
                            profile_op.Base,
                            type(profile_op.Base),
                            len(profile_op.Base) if profile_op.Base else 0
                        )
                    )

                if profile_op.Base:
                    for base_obj, subs in profile_op.Base:
                        for sub in subs:
                            try:
                                elem = base_obj.Shape.getElement(sub)
                                if DEBUG:
                                    App.Console.PrintMessage(
                                        "    VALID: {} -> {} = {}\n".format(
                                            base_obj.Name, sub, type(elem).__name__
                                        )
                                    )
                            except Exception as e:  # noqa: BLE001
                                App.Console.PrintError(
                                    "    INVALID: {} -> {} ERROR: {}\n".format(
                                        base_obj.Name, sub, e
                                    )
                                )

                profile_op.Direction = cut_direction
                if offset_side == "None":
                    profile_op.UseComp = False
                    profile_op.OffsetExtra.Value = 0.0
                else:
                    profile_op.UseComp = True
                    profile_op.Side = "Inside" if offset_side == "Inside" else "Outside"

                if DEBUG:
                    App.Console.PrintMessage(
                        "  Settings: Side='{}', Direction='{}', UseComp={}\n".format(
                            profile_op.Side, profile_op.Direction, profile_op.UseComp
                        )
                    )

                profile_op.ClearanceHeight = 5.0
                profile_op.SafeHeight = 3.0
                profile_op.StartDepth = 0.0
                profile_op.StepDown = 1.0
                profile_op.FinalDepth = -2.0

                dressup = add_leadinout_dressup(
                    profile_op,
                    leadIn=leadIn,
                    leadOut=leadOut,
                    styleIn=styleIn,
                    styleOut=styleOut,
                    lengthMultiplier=lengthMultiplier
                )
                if dressup is not None:
                    if DEBUG:
                        App.Console.PrintMessage(
                            "  [DRESSUP] Attached to '{}': {}\n".format(op_name, dressup.Name)
                        )
                else:
                    App.Console.PrintError(
                        "  [DRESSUP] Failed to create dressup for '{}'\n".format(op_name)
                    )

                if DEBUG:
                    App.Console.PrintMessage(
                        "Created {} linked to {} ({} edges)\n".format(
                            op_name, source_obj.Name, len(edge_names)
                        )
                    )

            if settings["concaveDetection"]:
                if DEBUG:
                    App.Console.PrintMessage(
                        "  [CONCAVE] Processing outer wire of face (normal_z={:.6f})\n".format(
                            normal.z
                        )
                    )
                concave_chains = find_concave_chains(face.OuterWire, normal, settings["concaveDepthTol"])
                concave_count = len(concave_chains)
                if DEBUG:
                    App.Console.PrintMessage(
                        "  Outer wire: {} concave indentation chain(s) found\n".format(concave_count)
                    )

                for chain in concave_chains:
                    try:
                        edge_names = []
                        for edge in chain:
                            for idx, master_edge in enumerate(master_edges):
                                if master_edge.isEqual(edge):
                                    edge_names.append("Edge{}".format(idx + 1))
                                    break

                        if DEBUG:
                            App.Console.PrintMessage(
                                "  Concave chain: {} edges, matched: {}\n".format(
                                    len(chain), len(edge_names)
                                )
                            )

                        if len(edge_names) < 2:
                            if DEBUG:
                                App.Console.PrintMessage(
                                    "  [CONCAVE] Skipping chain: < 2 matched edges\n"
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
                                        App.Console.PrintMessage(
                                            "  [CONCAVE] Skipping chain: forms closed wire "
                                            "(will be mishandled as open profile)\n"
                                        )
                                    continue
                        except Exception as e:  # noqa: BLE001
                            if DEBUG:
                                App.Console.PrintMessage(
                                    "  [CONCAVE] Wire validation failed: {}. Skipping chain.\n".format(e)
                                )
                            continue

                        concave_total += 1
                        op_count += 1
                        op_name = "Profile_Concave_{}".format(op_count)

                        if DEBUG:
                            App.Console.PrintMessage(
                                "=== CREATING {} ===\n".format(op_name)
                            )

                        import Path.Op.Profile as PathProfileOp
                        profile_op = PathProfileOp.Create(op_name, parentJob=job)
                        if profile_op is None:
                            App.Console.PrintError(
                                "  [CREATE] Profile.Create() returned None for '{}'\n".format(op_name)
                            )
                            continue
                        if DEBUG:
                            App.Console.PrintMessage(
                                "  [CREATE] Created op: {}, type={}, hasProxy={}\n".format(
                                    profile_op.Name, type(profile_op).__name__,
                                    hasattr(profile_op, "Proxy")
                                )
                            )

                        if DEBUG:
                            App.Console.PrintMessage(
                                "  After Create:\n"
                                "    op.Base = {}\n"
                                "    op.Proxy = {}\n"
                                "    op.Proxy.job = {}\n".format(
                                    profile_op.Base, profile_op.Proxy,
                                    profile_op.Proxy.job if hasattr(profile_op.Proxy, 'job') else "N/A"
                                )
                            )

                        profile_op.ToolController = tool_controller
                        if DEBUG:
                            App.Console.PrintMessage("  ToolController set to {}\n".format(tool_controller.Name))

                        import Path.Op.Gui.Base as PathOpGui
                        profile_op.ViewObject.Proxy = PathOpGui.ViewProvider(profile_op.ViewObject, res)
                        profile_op.ViewObject.Proxy.setDeleteObjectsOnReject(False)

                        base_list = []
                        for edge_name in edge_names:
                            base_list.append((model_clone, [edge_name]))

                        if DEBUG:
                            App.Console.PrintMessage(
                                "  Setting Base = {}\n".format(base_list)
                            )
                        profile_op.Base = base_list

                        if DEBUG:
                            App.Console.PrintMessage(
                                "  After assignment:\n"
                                "    profile_op.Base = {}\n"
                                "    type(profile_op.Base) = {}\n"
                                "    len(profile_op.Base) = {}\n".format(
                                    profile_op.Base,
                                    type(profile_op.Base),
                                    len(profile_op.Base) if profile_op.Base else 0
                                )
                            )

                        if profile_op.Base:
                            for base_obj, subs in profile_op.Base:
                                for sub in subs:
                                    try:
                                        elem = base_obj.Shape.getElement(sub)
                                        if DEBUG:
                                            App.Console.PrintMessage(
                                                "    VALID: {} -> {} = {}\n".format(
                                                    base_obj.Name, sub, type(elem).__name__
                                                )
                                            )
                                    except Exception as e:  # noqa: BLE001
                                        App.Console.PrintError(
                                            "    INVALID: {} -> {} ERROR: {}\n".format(
                                                base_obj.Name, sub, e
                                            )
                                        )

                        profile_op.Direction = cut_direction
                        if offset_side == "None":
                            profile_op.UseComp = False
                            profile_op.OffsetExtra.Value = 0.0
                        else:
                            profile_op.UseComp = True
                            profile_op.Side = "Inside" if offset_side == "Inside" else "Outside"

                        if DEBUG:
                            App.Console.PrintMessage(
                                "  Settings: Side='{}', Direction='{}', UseComp={}\n".format(
                                    profile_op.Side, profile_op.Direction, profile_op.UseComp
                                )
                            )

                        profile_op.ClearanceHeight = 5.0
                        profile_op.SafeHeight = 3.0
                        profile_op.StartDepth = 0.0
                        profile_op.StepDown = 1.0
                        profile_op.FinalDepth = -2.0

                        dressup = add_leadinout_dressup(
                            profile_op,
                            leadIn=leadIn,
                            leadOut=leadOut,
                            styleIn=styleIn,
                            styleOut=styleOut,
                            lengthMultiplier=lengthMultiplier
                        )
                        if dressup is not None:
                            if DEBUG:
                                App.Console.PrintMessage(
                                    "  [DRESSUP] Attached to '{}': {}\n".format(op_name, dressup.Name)
                                )
                        else:
                            App.Console.PrintError(
                                "  [DRESSUP] Failed to create dressup for '{}'\n".format(op_name)
                            )

                        if DEBUG:
                            App.Console.PrintMessage(
                                "Created {} linked to {} ({} edges)\n".format(
                                    op_name, source_obj.Name, len(edge_names)
                                )
                            )

                    except Exception as e:  # noqa: BLE001
                        App.Console.PrintError(
                            "  [CONCAVE] ERROR processing chain: {}\n".format(e)
                        )
                        import traceback
                        App.Console.PrintError(traceback.format_exc())
                        continue

        doc.commitTransaction()
        doc.recompute()
    finally:
        PathUtils.UserInput = original_UserInput
        Gui.Control.closeDialog()

    if DEBUG:
        App.Console.PrintMessage("\n=== POST-CREATION VERIFICATION ===\n")
    for obj in job.OutList:
        if obj.Name.startswith("Operations") or obj.Label.startswith("Operations"):
            for op in obj.Group:
                if hasattr(op, "Base"):
                    if "Dressup" in op.Name:
                        if DEBUG:
                            App.Console.PrintMessage(
                                "Dressup '{}': Base={}, LeadIn={}, LeadOut={}, StyleIn={}, RadiusIn={}\n".format(
                                    op.Name, op.Base, op.LeadIn, op.LeadOut, op.StyleIn, op.RadiusIn
                                )
                            )
                    else:
                        if DEBUG:
                            App.Console.PrintMessage(
                                "Op '{}': Base = {}, Side={}, Direction={}\n".format(
                                    op.Name, op.Base, op.Side, op.Direction
                                )
                            )

    if DEBUG:
        App.Console.PrintMessage(
            "Finished! Created {} profile operations ({} closed loops, {} concave indentations).\n".format(
                op_count, closed_loop_count, concave_total
            )
        )
