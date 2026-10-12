"""GuiCommand classes for JetCutter Tools."""

import contextlib
import os

import FreeCAD as App
import FreeCADGui as Gui

from . import geometry
from . import lead_in_audit as LeadInAudit
from . import slicing as Slicing

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


# Give geometry.py the same debug switch and sink, so there is one place that
# decides whether debug output happens at all. Done here rather than in geometry
# because _dbg is defined above and the flag is read at call time.
geometry.configure(debug=DEBUG, logger=_dbg)


# ============================================================================
# SameEdgesAsHighlighted
# ============================================================================

class SameEdgesAsHighlighted:
    """Finds and selects all edges matching a template profile (length + Z-level)."""

    def IsActive(self):
        # Named `IsActive`, not `IsEnabled`: Gui's PythonCommand::isActive() looks
        # up `IsActive` by name (Command.cpp) and ignores anything else, so a
        # method called `IsEnabled` is dead code and the button stays clickable.
        # IsActive must also return a real bool -- a non-bool is read as False.
        selection = Gui.Selection.getSelectionEx()
        if not selection:
            return False
        # No explicit "are there subelement names" test: a whole object has an
        # empty tuple, and `any()` over an empty tuple is already False, which
        # is the answer we want. Verified rather than assumed -- see
        # test_slice_model.py.
        return any(
            sub.startswith("Edge") for sub in selection[0].SubElementNames
        )

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
            "Pixmap": "JetCutter_SameEdges",
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

    def IsActive(self):
        # `IsActive`, not `IsEnabled` -- see SameEdgesAsHighlighted.IsActive.
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
            "Pixmap": "JetCutter_FindProfiles",
            "MenuText": "Find Profiles",
            "ToolTip": "Create Profile operations for internal edges on CAM Job top faces",
        }


# ============================================================================
# SliceModel
# ============================================================================

class SliceModel:
    """Extrudes a sketch's slice lines through a solid and slices it apart."""

    def IsActive(self):
        # `IsActive`, not `IsEnabled` -- see SameEdgesAsHighlighted.IsActive.
        # One attached sketch and nothing else: with nothing selected the button
        # greys out rather than waiting to complain in the report view.
        selection = Gui.Selection.getSelection()
        if len(selection) != 1:
            return False
        sketch = selection[0]
        if not sketch.isDerivedFrom("Sketcher::SketchObject"):
            return False
        return bool(list(getattr(sketch, "AttachmentSupport", None) or ()))

    def Activated(self):
        slice_selected_model()

    def GetResources(self):
        return {
            "Pixmap": "JetCutter_SliceModel",
            "MenuText": "Slice Model",
            "ToolTip": "Extrude a sketch's lines through the model and slice it into chunks",
        }


def slice_selected_model():
    """Slice the selected sketch's model apart, as one undoable step.

    The whole point is that this is one transaction. Done by hand it is two
    commands, each with its own undo, and an abort partway through leaves the
    model consumed by an extrusion the user then has to find and delete.
    """
    selection = Gui.Selection.getSelection()
    if len(selection) != 1 or not selection[0].isDerivedFrom("Sketcher::SketchObject"):
        App.Console.PrintError("Please select the sketch holding the slice lines.\n")
        return

    sketch = selection[0]
    doc = sketch.Document

    try:
        model = Slicing.model_for_sketch(sketch)
    except Slicing.SlicingError as exc:
        App.Console.PrintError(f"{exc}\n")
        return

    if not _has_solids(model):
        App.Console.PrintError(f"{model.Label} has no solid to slice.\n")
        return

    # Pre-created so the `finally` can close it even if opening the transaction
    # or building it raised. See create_progress_dialog for why autoReset is off.
    progress_dialog = None
    opened = False

    def note(message):
        if progress_dialog is not None:
            progress_dialog.setLabelText(message)
            QApplication.processEvents()

    try:
        doc.openTransaction("Slice Model")
        opened = True

        progress_dialog = QProgressDialog(
            "Preparing...",
            "Cancel",
            0,
            3,
            Gui.getMainWindow(),
        )
        progress_dialog.setWindowModality(QtCore.Qt.WindowModal)
        progress_dialog.setMinimumDuration(0)
        progress_dialog.setAutoReset(False)
        progress_dialog.show()
        QApplication.processEvents()

        extrusion, feature, _group, chunks = Slicing.slice_model(
            doc, sketch, model, progress=note,
        )
        progress_dialog.setValue(3)

        # Hidden, not deleted: the extrusion was scaffolding and the model is the
        # original, but all three are recoverable and all are in the undo.
        #
        # The Slice feature has to go too, and that is not tidiness. Each chunk is
        # a compound filter reading `Base=Slice`, so a visible Slice re-renders the
        # whole unsplit panel on top of the pieces -- and because it is the parent of
        # them in the tree, hiding the group above would not take it away. Part >
        # Split > Slice Apart hides it for the same reason (SplitFeatures.py).
        for consumed in (model, extrusion, feature):
            view = getattr(consumed, "ViewObject", None)
            if view is not None:
                view.hide()

        doc.commitTransaction()
        opened = False

        App.Console.PrintMessage(
            f"Sliced {model.Label} into {len(chunks)} chunk(s).\n"
        )
    except Slicing.SlicingError as exc:
        if opened:
            doc.abortTransaction()
        App.Console.PrintError(f"{exc}\n")
    except Exception as exc:  # noqa: BLE001
        if opened:
            doc.abortTransaction()
        App.Console.PrintError(f"Slicing failed: {type(exc).__name__}: {exc}\n")
    finally:
        if progress_dialog is not None:
            progress_dialog.close()


def _has_solids(obj):
    shape = getattr(obj, "Shape", None)
    return bool(shape) and bool(shape.Solids)


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
    #
    # The floor is 2.0, not a decorative lower bound: the multiplier is applied
    # to the tool diameter, which for these processes *is* the kerf, and a
    # plasma or laser arc needs roughly 2x the kerf to travel before it
    # stabilises. The previous range started at 0.01, which offered a 0.06 mm
    # lead-in on a 6 mm kerf -- a pierce with no arc stabilisation behind it.
    # The upper bound is unchanged and generous.
    length_row = QtGui.QHBoxLayout()
    length_spin = QtGui.QDoubleSpinBox()
    length_spin.setRange(LeadInAudit.MINIMUM_MULTIPLIER, 100.0)
    length_spin.setDecimals(2)
    length_spin.setValue(3.0)
    length_spin.setSingleStep(0.1)
    length_row.addWidget(QtGui.QLabel("Length:"))
    length_row.addWidget(length_spin)
    length_row.addWidget(QtGui.QLabel("× Tool Diameter"))
    length_row.addStretch()
    leadin_layout.addLayout(length_row)

    # Start-point relocation, opt-in and off by default.
    #
    # When a lead-in pierces the finished part, the audit can either shorten the
    # lead-in or move where the cut begins. Shortening spends arc-stabilisation
    # margin -- the length *is* the distance the arc travels before it cuts -- so
    # moving is the better outcome where it is available. But it relocates the
    # cut's start, which is an operational choice: a tab position, a seam, an
    # ordering preference may all depend on where a cut begins. So it is a
    # setting the operator sets, not a behaviour the addon assumes.
    startmove_check = QtGui.QCheckBox()
    startmove_check.setChecked(False)
    startmove_check.setToolTip(
        "When a lead-in would pierce the finished part, allow the check to move "
        "the cut's start point around the contour to find somewhere the pierce "
        "lands in waste. Off by default: this changes where the cut begins."
    )
    startmove_row = QtGui.QHBoxLayout()
    startmove_row.addWidget(QtGui.QLabel("Start point:"))
    startmove_row.addWidget(startmove_check)
    startmove_row.addWidget(QtGui.QLabel("allow the pierce check to move it"))
    startmove_row.addStretch()
    leadin_layout.addLayout(startmove_row)

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
        # Off by default: moving where a cut begins is the operator's call.
        "allowStartMove": False,
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
        selected["allowStartMove"] = startmove_check.isChecked()
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


# ============================================================================
# Lead-in pierce audit
#
# The decision rules live in lead_in_audit.py, which imports neither FreeCAD nor
# Qt and is tested by the fast tier. What is here is the adapter: the parts that
# need a live document to read a toolpath, to re-point a multiplier, or to mark
# an object. Kept apart so the rules stay testable without a GUI.
# ============================================================================

def find_dressup_on(doc, operation):
    """Return the LeadInOut dressup sitting directly on `operation`, or None.

    There is no back-link from an operation to the dressups above it, so this
    scans the document. Narrowed to `Base is operation` so a chain of dressups
    returns the one actually on this operation rather than any descendant.
    """
    for obj in doc.Objects:
        if getattr(obj, "Base", None) is operation and hasattr(obj, "LeadIn"):
            return obj
    return None


def set_leadin_multiplier(dressup, multiplier):
    """Re-point a dressup's lead-in length at `multiplier` x the tool diameter.

    `RadiusIn` is held by an expression rather than a plain value, so assigning
    to it is silently discarded. The expression has to be rewritten instead --
    which is also why this is not a one-line assignment and why the addon's own
    creation path bothers with the expression in the first place.
    """
    from Path.Dressup import Utils as PathDressup

    base_op = PathDressup.baseOp(dressup.Base)
    if base_op and getattr(base_op, "ToolController", None):
        expr = f"{base_op.Name}.ToolController.Tool.Diameter.Value*{multiplier}"
        dressup.setExpression("RadiusIn", expr)
        dressup.setExpression("RadiusOut", expr)
    else:
        dressup.RadiusIn = multiplier
        dressup.RadiusOut = multiplier


def contour_points(wire, deflection=0.02):
    """Return a closed contour's ordered `(x, y)` points, or None.

    Discretised rather than read from the ordered vertexes because a wire's
    vertexes say nothing about the curves between them, and a straight chord
    between two points of a hole cuts across the waste the pierce has to land in.
    """
    if wire is None:
        return None
    try:
        if wire.isNull():
            return None
    except Exception:  # noqa: BLE001
        return None
    try:
        points = [(p.x, p.y) for p in wire.discretize(Deflection=deflection)]
    except Exception:  # noqa: BLE001
        points = None
    if not points or len(points) < 3:
        try:
            points = [(v.Point.x, v.Point.y) for v in wire.OrderedVertexes]
        except Exception:  # noqa: BLE001
            return None
    return points


def waste_region_from_wire(wire, deflection=0.02):
    """Return the region a closed contour encloses, as a polygon, or None.

    None means "no region to test against", which the audit records as unchecked
    rather than as clean.
    """
    return LeadInAudit.waste_polygon(contour_points(wire, deflection))


def tool_kerf(operation):
    """Return the tool diameter behind an operation, in mm, or None.

    For plasma, laser and waterjet the tool diameter *is* the kerf, which is what
    makes the multiplier and the arc-stabilisation floor the same unit. None when
    it cannot be read, because a floor derived from an unknown kerf is not a
    floor.
    """
    controller = getattr(operation, "ToolController", None)
    tool = getattr(controller, "Tool", None)
    diameter = getattr(tool, "Diameter", None)
    if diameter is None:
        return None
    try:
        return float(diameter.Value)
    except Exception:  # noqa: BLE001
        try:
            return float(diameter)
        except Exception:  # noqa: BLE001
            return None


def measure_pierce(dressup, region, kerf=None, current=False):
    """Return `(pierce, clear)` for a dressup, or `(None, None)` if unreadable.

    `clear` means the pierce is inside the waste **and** at least `kerf/2` from
    its edge, so the pierce hole itself cannot reach the finished edge. `kerf` of
    None falls back to bare containment, which is only right when the jet width
    is genuinely unknown.

    Recomputes the dressup first: its path is what is read, and a path left
    stale by an earlier edit would report the pre-edit geometry as if it were
    the result.

    `current=True` says the caller has already recomputed this dressup and
    nothing has touched it since, so the recompute is skipped. That is the
    normal case for the audit's *first* measurement of each operation:
    FindProfiles finishes creating them with a `doc.recompute()`, and the audit
    then re-derived every one of those paths from scratch. On the shop's floral
    panel the audit's own geometry costs 0.05 s for all 43 contours, so a
    recompute the caller has already paid for was the entire cost of the check.

    A path that is null or empty is recomputed anyway, so `current` can only ever
    skip work, never turn a readable path into an unreadable one.
    """
    if not current or not getattr(dressup, "Path", None):
        try:
            dressup.recompute()
        except Exception:  # noqa: BLE001
            return None, None
    pierce = LeadInAudit.pierce_point(dressup.Path)
    if pierce is None:
        return None, None
    margin = None if kerf is None else kerf / 2.0
    return pierce, LeadInAudit.is_in_waste(pierce, region, margin=margin or 0.0)


def apply_start_point(operation, dressup, xy):
    """Point an operation's cut at `xy` and bring its dressup up to date.

    **`UseStartPoint` is set as well as the point, and that is required.** With
    it false, `StartPoint` is not read at all: the Nesting workbench measured
    that setting the point to opposite corners of a part produced bit-identical
    toolpaths. Verified on this addon's own operations in
    `tests/gui/test_lead_in_probe.py`, which records the pierce's (x, y) as the
    point goes round a hole and finds four distinct places.

    The Z is taken from `ClearanceHeight`, never carried over: a `StartPoint`'s Z
    is a derived value written by `CommandSetStartPoint`, not an authored
    coordinate, and riding a transformed Z along reaches the toolpath as a
    spurious level.

    Returns True when the operation and its dressups recomputed. A failure means
    the path is in an unknown state, so the caller treats it as "this candidate
    does not work" rather than as a result.
    """
    import FreeCAD as App

    try:
        if hasattr(operation, "UseStartPoint"):
            operation.UseStartPoint = True
        z = 0.0
        clearance = getattr(operation, "ClearanceHeight", None)
        if clearance is not None:
            try:
                z = float(clearance.Value)
            except Exception:  # noqa: BLE001
                z = 0.0
        operation.StartPoint = App.Vector(xy[0], xy[1], z)
        operation.recompute()
    except Exception:  # noqa: BLE001
        return False
    dressup.recompute()
    return True


def search_for_clear_pierce(operation, dressup, points, region, kerf=None,
                            cancel_check=None, stats=None, edges=None):
    """Try candidate start points until the pierce lands in the waste.

    Returns the point that worked, or None. Every candidate is verified against
    the dressup's own regenerated toolpath rather than predicted from the
    geometry: the whole point of this audit is to measure, and a prediction that
    disagreed with the toolpath would put the pierce somewhere nobody checked.

    Leaves the start point wherever the last attempt put it, so the caller
    restores it if nothing worked -- a relocation that achieved nothing is a
    silent change to where the cut begins.

    `edges` (optional) is the list of wire edges for the contour. When provided,
    the midpoint of the longest edge is tried first as a seed candidate before
    the spiral search begins. This mirrors FreeCAD's `UseLongestEdge` mechanism
    and costs only one recompute when it works.

    `stats`, if given, is a dict collecting how much work this did -- candidates
    tried and recomputes spent. The search is the only part of the audit that
    runs many times per operation, so its cost is worth counting rather than
    estimating.
    """
    origin = LeadInAudit.pierce_point(dressup.Path)
    if origin is None:
        return None

    # Seed: midpoint of the longest edge, if edges are available.
    seed = None
    if edges:
        seed = LeadInAudit.longest_edge_midpoint(edges)

    tried = 0
    for candidate in LeadInAudit.start_point_candidates(points, origin, seed=seed):
        if cancel_check is not None and cancel_check():
            return None
        tried += 1
        if stats is not None:
            stats["candidates_tried"] = stats.get("candidates_tried", 0) + 1
        if not apply_start_point(operation, dressup, candidate):
            continue
        # `current=True`: apply_start_point has just recomputed this dressup and
        # nothing has touched it since. Without it the search recomputes the
        # same path twice per candidate -- and the search is the one part of the
        # audit that runs up to 48 times per operation, so that duplicate was the
        # single largest avoidable cost in the whole command.
        _pierce, clear = measure_pierce(dressup, region, kerf, current=True)
        if clear:
            return candidate
    return None


def search_for_angle_pierce(dressup, region, kerf=None, cancel_check=None, stats=None):
    """Try candidate angles (and invert) until the pierce lands in the waste.

    For fixed-start chains (concave), the start point cannot move, so the only
    free parameter is the lead-in angle. We sweep angles alternating from 90°
    (Perpendicular) and try both invert settings (which together sweep the full
    inward half-plane). The first candidate that puts the pierce in the waste
    with kerf/2 margin is applied: StyleIn='Line', AngleIn=angle, InvertIn=flip.
    A single recompute verifies the toolpath matches the prediction.

    Returns the (angle_deg, flip) that worked, or None. The dressup is left in
    the final attempted state; the caller restores original settings on failure.

    `stats`, if given, collects candidates tried.
    """
    # Recover the geometric parameters from the current dressup state.
    geom = LeadInAudit.recover_lead_in_geometry(dressup)
    if geom is None:
        return None
    start, tangent_unit, normal_unit, length = geom

    # Original settings to restore on failure.
    orig_style = getattr(dressup, "StyleIn", "Perpendicular")
    orig_angle = getattr(dressup, "AngleIn", 90.0)
    orig_invert = getattr(dressup, "InvertIn", False)

    tried = 0
    for flip in (False, True):
        if cancel_check is not None and cancel_check():
            return None
        # InvertIn flips the normal direction (getLeadDir negates it).
        normal = normal_unit if not flip else (-normal_unit[0], -normal_unit[1])
        for angle in LeadInAudit.angle_candidates():
            if cancel_check is not None and cancel_check():
                return None
            tried += 1
            if stats is not None:
                stats["candidates_tried"] = stats.get("candidates_tried", 0) + 1
            # Predict the pierce analytically (matches leadinout.py exactly).
            pred = LeadInAudit.lead_in_pierce(start, tangent_unit, normal,
                                              length, angle)
            if pred is None:
                continue
            # Check clearance against the waste region.
            if not LeadInAudit.is_in_waste(pred, region, margin=(kerf / 2.0) if kerf else 0.0):
                continue
            # Prediction passes: apply and verify with one recompute.
            dressup.StyleIn = "Line"
            dressup.AngleIn = float(angle)
            dressup.InvertIn = flip
            dressup.recompute()
            pierce, clear = measure_pierce(dressup, region, kerf, current=True)
            if clear and pierce is not None:
                return (angle, flip)
            # Verification failed (should not happen if geometry is sound),
            # continue searching.

    # Nothing worked: restore original settings.
    dressup.StyleIn = orig_style
    dressup.AngleIn = orig_angle
    dressup.InvertIn = orig_invert
    dressup.recompute()
    return None


def audit_leadin_pierces(targets, side, configured=LeadInAudit.DEFAULT_MULTIPLIER,
                         allow_start_move=False, cancel_check=None, progress=None,
                         paths_current=False):
    """Move every target's lead-in pierce into the waste, or say why not.

    `targets` is a list of `(operation, dressup, wire)` or
    `(operation, dressup, wire, fixed_start)`. Each is measured from
    its own generated toolpath rather than from the closed-form budget, because
    the formula in `waste_budget` assumes a convex contour of a known diameter
    and would misjudge a slot or a concave chain.

    `fixed_start` (optional 4th element) means the cut's start point is fixed
    -- e.g. a concave chain whose start is pinned at the part boundary.
    When True, the relocation lever is skipped entirely because setting
    UseStartPoint on an open chain silently displaces the cut and renders
    StartPoint inert.

    `configured` is the multiplier the operations were created with, so a
    shortening can be reported as a change from what the operator asked for.
    `allow_start_move` permits relocating a cut's start, which preserves the
    operator's full lead-in but changes where the cut begins -- so it is off
    unless they asked for it.

    `paths_current` says every target's toolpath was just recomputed by the
    caller and has not been touched since, so the audit's first measurement of
    each skips its own recompute. It is only ever set by a caller that has just
    run a document recompute; every measurement after the audit has changed
    something recomputes regardless.

    `progress` is called as `progress(done, total, label, finding)`, twice per
    target: once naming the operation about to be worked on, and once with its
    result. Naming the operation *before* it is examined matters because the work
    per target is wildly uneven -- an operation already in the waste costs one
    recompute, while one needing relocation can walk dozens of candidate start
    points, each with its own recompute. A bar driven only by the completed count
    stalls on those and looks hung even while it is working.

    The order of the three levers is the design; see `lead_in_audit.next_action`
    for why relocation is tried before shortening. Returns a list of
    `lead_in_audit.Finding`, one per target.
    """
    findings = []
    total = len(targets)
    stats = {"total": total, "clean": 0, "on_part": 0, "unreadable": 0, "shortened": 0, "relocated": 0, "flagged": 0, "searches": 0, "candidates_tried": 0}
    
    App.Console.PrintMessage(f"[LeadInAudit] Starting audit of {total} operations (relocation={'on' if allow_start_move else 'off'}, paths_current={'yes' if paths_current else 'no'})\n")
    
    for index, target in enumerate(targets):
        if cancel_check is not None and cancel_check():
            App.Console.PrintWarning(f"[LeadInAudit] Cancelled at {index+1}/{total}\n")
            break
        # Targets are (operation, dressup, wire) or (operation, dressup, wire, fixed_start)
        if len(target) == 4:
            operation, dressup, wire, fixed_start = target
        else:
            operation, dressup, wire = target
            fixed_start = False
        name = getattr(dressup, "Name", None) or getattr(operation, "Name", "?")
        _report_progress(progress, index, total, f"Checking {name}...", None)
        finding = _audit_one(operation, dressup, wire, side, configured,
                             allow_start_move, cancel_check,
                             path_current=paths_current, stats=stats,
                             fixed_start=fixed_start)
        if finding is not None:
            findings.append(finding)
            # Update stats
            stats[finding.status] = stats.get(finding.status, 0) + 1
            if finding.action == LeadInAudit.ACTION_SHORTEN:
                stats["shortened"] += 1
            elif finding.action == LeadInAudit.ACTION_SEARCH:
                stats["relocated"] += 1
                stats["searches"] += 1
            elif finding.action == LeadInAudit.ACTION_FLAG:
                stats["flagged"] += 1
        _report_progress(progress, index + 1, total,
                         _audit_label(name, finding), finding)
    
    App.Console.PrintMessage(f"[LeadInAudit] Complete: {stats['clean']} clean, {stats['shortened']} shortened, {stats['relocated']} relocated, {stats['flagged']} flagged, {stats['unreadable']} unreadable\n")
    if stats["searches"] > 0:
        App.Console.PrintMessage(f"[LeadInAudit] Relocation stats: {stats['searches']} searches, {stats['candidates_tried']} candidates tried, avg {stats['candidates_tried']/max(1,stats['searches']):.1f} candidates/search\n")
    return findings


def begin_leadin_audit_phase(dialog, total):
    """Reshape `dialog` for the lead-in audit, which is a phase of its own.

    The dialog's range was sized for the wire loop above and is already sitting
    at that maximum, so without this the audit begins looking finished before it
    has started -- which is the report this was written in response to: the bar
    sat at 100% with a static label for the whole audit.

    Module-level and taking the dialog as an argument, rather than a closure
    over `progress_dialog`, so that it can be driven directly in a test. The
    closure form could only be reached by parsing its source out of this module,
    and a test that checks a source string passes whether or not the dialog is
    ever updated -- which is precisely how the original defect hid from review.
    """
    dialog.setRange(0, total)
    dialog.setValue(0)
    dialog.setLabelText(f"Checking lead-in pierce points (0 of {total})...")
    QApplication.processEvents()


def report_leadin_audit_progress(dialog, done, total, label, _finding):
    """Move `dialog` on to the next audited operation. See above for the shape."""
    dialog.setValue(done)
    dialog.setLabelText(f"Lead-in check: {done} of {total} done - {label}")
    QApplication.processEvents()


def _report_progress(progress, done, total, label, finding):
    if progress is not None:
        progress(done, total, label, finding)


def _audit_label(name, finding):
    """One line describing what happened to a single audited operation."""
    if finding is None:
        return f"{name} - not checked"
    if finding.status == LeadInAudit.ON_PART:
        # Worth standing out in the dialog: this is the one that needs a human.
        return f"{name} - NEEDS REVIEW ({finding.detail or 'pierce on the part'})"
    if finding.status == LeadInAudit.UNREADABLE:
        return f"{name} - not checked ({finding.detail or 'unreadable'})"
    return f"{name} - {finding.detail or 'pierce is in the waste'}"


def _audit_one(  # noqa: PLR0911
        operation, dressup, wire, side, configured, allow_start_move,
        cancel_check, path_current=False, stats=None, fixed_start=False):
    """Return the `Finding` for one operation, having tried to fix it.

    Many returns because the levers are a straight-line sequence and each has its
    own way of ending: in waste, unreadable, or flagged. Splitting it into one
    function per lever would hide the ordering, which is the part that is easy to
    get wrong and hard to see.

    `fixed_start` means the cut's start point is fixed (e.g. concave chain).
    When True, the relocation lever is skipped and the angle-sweep lever is
    used instead (changing the lead-in angle while keeping the start fixed).
    """
    if dressup is None:
        return LeadInAudit.Finding(
            getattr(operation, "Name", "?"), LeadInAudit.UNREADABLE,
            "no lead-in dressup was created")
    # No lead-in means no pierce to place. Reporting these would raise a finding
    # for an operation that cannot have one.
    if not getattr(dressup, "LeadIn", False):
        return LeadInAudit.Finding(dressup.Name, LeadInAudit.CLEAN,
                                   "lead-in is disabled", multiplier=None)

    name = dressup.Name
    points = contour_points(wire)
    region = LeadInAudit.waste_polygon(points)
    if region is None:
        return LeadInAudit.Finding(
            name, LeadInAudit.UNREADABLE,
            "its contour could not be turned into a region to test against")

    kerf = tool_kerf(operation)
    # Seeded with the configured value, not None. `next_action` only offers a
    # shortening when it has a multiplier above the floor to compare, so seeding
    # None made every pierce read as unfixable on the first pass -- which is what
    # a None multiplier means there: "I do not know the length, so I cannot
    # shorten it".
    multiplier = configured
    inverted = False
    searched = False
    angle_swept = False

    def settle(action, detail):
        LeadInAudit.clear_on_part(dressup)
        return LeadInAudit.Finding(name, LeadInAudit.CLEAN, detail,
                                   multiplier=multiplier, action=action)

    # -- lever 1: direction ------------------------------------------------
    # Free, and it does not move the cut's start, so it goes first.
    pierce, clear = measure_pierce(dressup, region, kerf, current=path_current)
    if pierce is None:
        return LeadInAudit.Finding(
            name, LeadInAudit.UNREADABLE,
            "its toolpath has no cutting move to read a pierce from")
    if LeadInAudit.next_action(clear, side=side) == LeadInAudit.ACTION_INVERT:
        dressup.InvertIn = not getattr(dressup, "InvertIn", False)
        inverted = True
        multiplier = configured
        pierce, clear = measure_pierce(dressup, region, kerf, current=False)
        if pierce is None:
            return LeadInAudit.Finding(
                name, LeadInAudit.UNREADABLE,
                "its toolpath has no cutting move after the direction flip")
    if clear:
        return settle(LeadInAudit.ACTION_INVERT if inverted else None,
                      "lead-in direction flipped inward" if inverted else "")

    # -- lever 2a: angle sweep for fixed-start chains (concave) ------------
    # The start point is pinned; the only free parameter is the lead-in angle.
    # Sweep angles alternating from 90° (Perpendicular) with both invert
    # settings, which together cover the full inward half-plane.
    if fixed_start:
        angle_swept = True
        result = search_for_angle_pierce(dressup, region, kerf, cancel_check, stats)
        if result is not None:
            angle, flip = result
            return settle(LeadInAudit.ACTION_SEARCH,
                          LeadInAudit.describe_angle(angle, flip))
        # Angle sweep exhausted at full length -- fall through to shortening.

    # -- lever 2b: relocate the start, keeping the full length -------------
    # Skipped when fixed_start=True because setting UseStartPoint on an open
    # chain silently displaces the cut by ~1 mm and makes StartPoint inert.
    if (not fixed_start and allow_start_move
            and LeadInAudit.next_action(False, side=side, inverted=inverted,
                                        multiplier=multiplier,
                                        allow_start_move=True)
            == LeadInAudit.ACTION_SEARCH):
        searched = True
        moved = search_for_clear_pierce(operation, dressup, points, region,
                                        kerf, cancel_check, stats,
                                        edges=wire.Edges if wire is not None else None)
        if moved is not None:
            return settle(LeadInAudit.ACTION_SEARCH,
                          LeadInAudit.describe_relocated(moved))
        # Nothing worked at the operator's length. Put their start back before
        # falling through, so the shortening below is tried where they set it
        # rather than at the last place the search happened to try.
        with contextlib.suppress(Exception):
            apply_start_point(operation, dressup, pierce)
        pierce, clear = measure_pierce(dressup, region, kerf)
        if clear:
            return settle(LeadInAudit.ACTION_SEARCH,
                          LeadInAudit.describe_relocated(pierce))
        multiplier = configured

    # -- lever 3: shorten to the arc-stabilisation floor -------------------
    if LeadInAudit.next_action(False, side=side, inverted=inverted,
                               multiplier=multiplier,
                               allow_start_move=allow_start_move,
                               search_tried=searched) \
            == LeadInAudit.ACTION_SHORTEN:
        set_leadin_multiplier(dressup, LeadInAudit.MINIMUM_MULTIPLIER)
        multiplier = LeadInAudit.MINIMUM_MULTIPLIER
        pierce, clear = measure_pierce(dressup, region, kerf)
        if clear:
            return settle(LeadInAudit.ACTION_SHORTEN,
                          LeadInAudit.describe_shorten(
                              configured, LeadInAudit.MINIMUM_MULTIPLIER))

    # -- flag --------------------------------------------------------------
    # Nothing both stabilises the arc and stays in the waste here. Undo the
    # trial changes: a shortening that demonstrably did not help is a silent
    # downgrade on top of a flag, and "change nothing and say why" is the
    # promise.
    if multiplier != configured:
        set_leadin_multiplier(dressup, configured)
        multiplier = configured
        with contextlib.suppress(Exception):
            dressup.recompute()
    # If we tried a search (relocation or angle sweep) but the user disallowed
    # start-move, don't report it as "search exhausted" -- the search was never
    # enabled by the checkbox.
    if (searched or angle_swept) and not allow_start_move:
        searched = False
        angle_swept = False
    detail = LeadInAudit.describe_flag(
        multiplier,
        None if kerf is None else
        LeadInAudit.waste_budget(_contour_diameter(wire), kerf),
        kerf or 0.0)
    if searched:
        detail = LeadInAudit.describe_search_exhausted() + "; " + detail
    elif angle_swept:
        detail = LeadInAudit.describe_search_exhausted() + " (angle sweep); " + detail
    LeadInAudit.mark_on_part(dressup, detail)
    return LeadInAudit.Finding(name, LeadInAudit.ON_PART, detail, pierce,
                               multiplier, LeadInAudit.ACTION_FLAG)


def _contour_diameter(wire):
    """Return the X extent of a contour, used only to phrase a flag's numbers.

    A stand-in for "how big is this feature" in a report. It is never the test --
    the test is the measured toolpath -- so a coarse stand-in cannot make the
    audit wrong, only its message approximate.
    """
    try:
        box = wire.BoundBox
        return max(box.XLength, box.YLength)
    except Exception:  # noqa: BLE001
        return 0.0


def report_leadin_findings(findings):
    """Print the audit's outcome to the Report view."""
    counts = LeadInAudit.summarise(findings)
    App.Console.PrintMessage(
        f"Lead-in check: {counts[LeadInAudit.CLEAN]} pierce in the waste, "
        f"{counts[LeadInAudit.ON_PART]} on the part, "
        f"{counts[LeadInAudit.UNREADABLE]} unchecked.\n")
    for line in LeadInAudit.describe_findings(findings):
        App.Console.PrintMessage(line + "\n")
    if counts[LeadInAudit.ON_PART]:
        App.Console.PrintWarning(
            f"Operations marked {LeadInAudit.ONPART_PREFIX} still cut; their "
            "pierce lands on the part because no lead-in both stabilises the "
            "arc and stays in the waste. They need a smaller kerf, or your "
            "judgement.\n")


# Concave-detection geometry lives in geometry.py so it can be tested without
# a FreeCAD, Qt or GUI import (CR-03). Re-exported here because these names are
# used throughout this module and by callers of it.
from .geometry import (  # noqa: E402,F401  (re-exported for existing call sites)
    _MIN_CONCAVE_CHAIN,
    find_concave_chains,
    group_concave_chains,
    is_concave_indentation,
    is_contiguous_run,
    validate_chain_edges,
)


def create_profile_operation(op_name, job, tool_controller, edge_names, model_clone,
                             offset_side, cut_direction, leadIn, leadOut, styleIn, styleOut,
                             lengthMultiplier, start_on_longest_edge=False):
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
        start_on_longest_edge: Start the cut at the midpoint of the contour's
            longest edge. **Closed loops only.** Defaults False.

    `start_on_longest_edge` sets FreeCAD's own `Profile.UseLongestEdge`, which
    the path generation reads (`Area.py:278`) to place the start at the midpoint
    of the longest edge. It is read inside the path generation that was going to
    happen anyway, so it costs about 6.7% of a recompute on the floral panel --
    50.7 ms to 54.1 ms per loop, measured over 30 real operations. No extra
    recompute.

    Note it lands on a slightly *different* point than the audit's own seed,
    because FreeCAD computes the longest edge on the kerf-compensated shape from
    `area.makeSections()` (`Area.py:247`) rather than on the original wire. Both
    find a wide mid-region spot; on `Profile_Loop_31` they clear 4.180 mm and
    4.187 mm respectively.

    It is off by default, and the concave path must leave it off.
    `UseLongestEdge` is gated on `not obj.UseStartPoint` (`Area.py:272-278`) --
    the same precondition under which `StartPoint` silently displaces an *open*
    chain's cut by ~1 mm (see `apply_start_point`). Concave chains are the ones
    on the fixed-start angle sweep, so setting it there risks disturbing geometry
    that path already relies on.

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

    if start_on_longest_edge:
        # Set before the first recompute, which is when the path is generated and
        # the property is read. Harmless if the build lacks it: the audit's own
        # seed still runs later, so this is an optimisation, not a dependency.
        try:
            profile_op.UseLongestEdge = True
            if DEBUG:
                _dbg(f"  UseLongestEdge set on '{op_name}'\n")
        except Exception as e:  # noqa: BLE001
            App.Console.PrintWarning(
                f"  [CREATE] Could not set UseLongestEdge on '{op_name}': {e}. "
                "The cut will start at the contour's natural first vertex; the "
                "lead-in audit can still relocate it if needed.\n",
            )

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

    # Z properties are deliberately NOT set here.
    #
    # On jet cutting machines (plasma/laser/waterjet) the CNC controller owns the
    # Z axis, and a post-processor reads StartDepth/FinalDepth as the pierce and
    # cut heights, emitting them as machine metadata. Those are machine/torch
    # facts, not part geometry, and they will eventually live on the
    # ToolController. Deriving them from the model leaks the part's Z into the
    # machine header (a pierce height of 51mm on a part sitting at Z=50).
    #
    # FreeCAD binds stock-anchored expressions to these properties at creation
    # time, and a plain assignment would be silently discarded anyway.
    #
    # See DESIGN.md -> "Jet Cutting Premise".

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
    """Create and return a QProgressDialog for the Find Profiles operation.

    ``autoReset`` is disabled deliberately. With the default ``True``, reaching
    ``maximum`` makes the dialog reset itself, which both hides it — taking the
    Cancel button away from the user for the rest of the run — and clears
    ``wasCanceled()``. Cancellation is tracked from the ``canceled`` signal
    rather than by polling ``wasCanceled()``, because the signal is a latch and
    the flag is not. Keeping the dialog alive to the end is what lets the
    caller close it explicitly instead of leaving it to the garbage collector.
    """
    dialog = QProgressDialog(
        "Starting analysis...",
        "Cancel",
        0,
        max_value,
        Gui.getMainWindow(),
    )
    dialog.setWindowModality(QtCore.Qt.WindowModal)
    dialog.setMinimumDuration(0)
    dialog.setAutoReset(False)
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

    # Collected here, audited after the transaction commits. Reading a dressup's
    # toolpath needs a recompute per operation, and doing that inside the open
    # "Create Profile Loops" transaction is both slow and entangled with
    # ISS-010 -- the dressup's own nested commit already leaves later objects
    # outside any transaction. Auditing afterwards, in its own transaction,
    # keeps the fixes undoable as one unit.
    leadin_targets = []

    original_UserInput = PathUtils.UserInput

    class SilentToolControllerChooser:
        def selectedToolController(self):
            return None
        def chooseToolController(self, controllers):
            return tool_controller

    PathUtils.UserInput = SilentToolControllerChooser()

    # Pre-initialised so the `finally` block can close the dialog even if
    # creating it, or opening the transaction, raised.
    progress_dialog = None

    try:
        doc.openTransaction("Create Profile Loops")

        # Create progress dialog
        progress_dialog = create_progress_dialog(total_wires)
        wire_progress = 0
        cancelled_list = [False]

        # Cancellation is tracked from signals, not by polling wasCanceled().
        #
        # Every user action on a QProgressDialog — the Cancel button, the window
        # close button, and Escape — emits `canceled`. None of them emits
        # `finished`; that signal only fires for accept()/reject(), which this
        # dialog never triggers. So `finished` is kept only as a backstop for a
        # programmatic close, and `canceled` is what actually carries user intent.
        #
        # Signals rather than the flag because they latch: `wasCanceled()` is
        # cleared by reset(), and the dialog reaching its maximum used to reset
        # it, so a cancel arriving on the final wire was discarded before the
        # poll could observe it. A signal cannot lose that race.
        def on_dialog_cancelled(*_args):
            cancelled_list[0] = True

        progress_dialog.canceled.connect(on_dialog_cancelled)
        progress_dialog.finished.connect(on_dialog_cancelled)

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
                    # Closed loops only. A closed contour's start is genuinely
                    # free, and the midpoint of its longest edge is where the
                    # most room is -- so place it there from the start rather
                    # than leaving a pierce on the part for the audit to
                    # repair. Free: FreeCAD reads UseLongestEdge inside the path
                    # generation that was going to run anyway.
                    start_on_longest_edge=True,
                )
                if profile_op is None:
                    continue

                leadin_targets.append(
                    (profile_op, find_dressup_on(doc, profile_op), wire)
                )

                if DEBUG:
                    _dbg(
                        f"Created {op_name} linked to {source_obj.Name} ({len(edge_names)} edges)\n",
                    )

                progress_dialog.setValue(wire_progress)
                progress_dialog.setLabelText(
                    f"Processing face {face_idx + 1} of {len(top_faces)} — {op_name} created"
                )
                QApplication.processEvents()

                if cancelled_list[0]:
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
                                if not elem.isNull():
                                    matched_edges.append(elem)

                            # Both guards live in validate_chain_edges(), which
                            # decides contiguity BEFORE sorting. That ordering is
                            # the whole point: Part.__sortEdges__ repairs
                            # disconnected edges into one connected sequence, so
                            # a check applied to sorted edges always passes and
                            # the guard could never fire (CR-11). Keeping it in
                            # one testable function stops the two steps drifting
                            # apart again.
                            # The sorted edges are used twice: the caller
                            # re-resolves each edge name against the clone, and
                            # the lead-in audit needs a closed wire to know which
                            # side of the contour is waste. Ordering matters for
                            # that, so the returned order is not discarded.
                            ok, ordered, reason = validate_chain_edges(
                                matched_edges,
                                sort_fn=Part.__sortEdges__,
                                wire_fn=Part.Wire,
                                is_closed_fn=lambda wire: wire.isClosed(),
                            )
                            if not ok:
                                if reason == "not_contiguous":
                                    App.Console.PrintWarning(
                                        f"  [CONCAVE] Skipping a non-contiguous "
                                        f"chain of {len(matched_edges)} edges — it "
                                        f"spans more than one feature. Concave "
                                        f"detection may have merged neighbouring "
                                        f"indentations.\n",
                                    )
                                elif DEBUG:
                                    _dbg(f"  [CONCAVE] Skipping chain: {reason}\n")
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

                        # Concave chains cut from the outside boundary inward, so the
                        # cut side is the opposite of what the dialog specifies.
                        # Inside <-> Outside, None stays None.
                        if offset_side == "Inside":
                            concave_offset = "Outside"
                        elif offset_side == "Outside":
                            concave_offset = "Inside"
                        else:
                            concave_offset = offset_side  # None stays None

                        progress_dialog.setLabelText(
                            f"Creating {op_name}..."
                        )
                        QApplication.processEvents()

                        profile_op = create_profile_operation(
                            op_name, job, tool_controller, edge_names, model_clone,
                            concave_offset, cut_direction, leadIn, leadOut, styleIn, styleOut,
                            lengthMultiplier,
                            # Explicitly OFF, not merely left to the default.
                            # `UseLongestEdge` is gated on `not UseStartPoint`,
                            # the same precondition under which StartPoint
                            # displaces an open chain's cut by ~1 mm and then
                            # goes inert. A concave chain's start is pinned; its
                            # pierce is placed by the angle sweep, not by moving
                            # the start.
                            start_on_longest_edge=False,
                        )
                        if profile_op is None:
                            continue

                        # The chain's own closed wire is the waste region: the
                        # indentation it bounds is the material this cut removes,
                        # so a pierce inside it lands in waste. Built from the
                        # sorted edges, which is why their order was kept.
                        try:
                            chain_wire = Part.Wire(ordered)
                        except Exception:  # noqa: BLE001
                            chain_wire = None
                        # Concave chains have a fixed start point (one end of the
                        # chain). Setting UseStartPoint on an open chain silently
                        # displaces the cut by ~1 mm and makes StartPoint inert
                        # -- relocation is actively harmful. Mark it so the audit
                        # skips the relocation lever entirely.
                        fixed_start = chain_wire is not None and not chain_wire.isClosed()
                        leadin_targets.append(
                            (profile_op, find_dressup_on(doc, profile_op),
                             chain_wire, fixed_start)
                        )

                        if DEBUG:
                            _dbg(
                                f"Created {op_name} linked to {source_obj.Name} ({len(edge_names)} edges)\n",
                            )

                        progress_dialog.setValue(wire_progress)
                        progress_dialog.setLabelText(
                            f"Processing face {face_idx + 1} of {len(top_faces)} — {op_name} created"
                        )
                        QApplication.processEvents()

                        if cancelled_list[0]:
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

        # --- lead-in pierce audit ------------------------------------------
        # In its own transaction, after the creation one has committed, so the
        # fixes undo as a single unit and no recompute happens inside a
        # transaction the dressups have already committed out of (ISS-010).
        if leadin_targets and leadIn:
            # A second, separately-counted phase. The dialog's range is reset
            # because it was sized for the wire loop above and is sitting at its
            # own maximum; leaving it there made the audit look finished before
            # it began, with no way to tell how much was left.
            audit_total = len(leadin_targets)
            begin_leadin_audit_phase(progress_dialog, audit_total)

            def on_audit_progress(done, total, label, finding):
                report_leadin_audit_progress(
                    progress_dialog, done, total, label, finding,
                )

            doc.openTransaction("Fix Lead-in Placement")
            # `paths_current=True`: the doc.recompute() above already computed
            # every one of these dressups, and nothing has touched them since.
            try:
                findings = audit_leadin_pierces(
                    leadin_targets,
                    side=offset_side,
                    configured=lengthMultiplier,
                    allow_start_move=bool(settings.get("allowStartMove")),
                    cancel_check=lambda: cancelled_list[0],
                    progress=on_audit_progress,
                    paths_current=True,
                )
                doc.recompute()
            finally:
                doc.commitTransaction()

            counts = LeadInAudit.summarise(findings)
            progress_dialog.setLabelText(
                f"Lead-in check complete: {counts[LeadInAudit.CLEAN]} in the "
                f"waste, {counts[LeadInAudit.ON_PART]} need review, "
                f"{counts[LeadInAudit.UNREADABLE]} unchecked."
            )
            QApplication.processEvents()
            report_leadin_findings(findings)
        elif DEBUG and leadin_targets:
            _dbg("  [LEADIN] Audit skipped: lead-in is disabled\n")
    finally:
        PathUtils.UserInput = original_UserInput
        # The progress dialog is a bare QProgressDialog, never registered with
        # Gui.Control, so Gui.Control.closeDialog() cannot be the thing that
        # dismisses it. Close it directly instead of leaving it to the garbage
        # collector, which can otherwise leave a window on screen after the
        # command returns.
        if progress_dialog is not None:
            progress_dialog.close()
            progress_dialog.deleteLater()
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
