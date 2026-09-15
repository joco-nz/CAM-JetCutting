"""A workbench manipulator that creates a new toolbar in the CAM Workbench."""

import FreeCADGui

_TARGET_WORKBENCH = "CAMWorkbench"
_TOOLBAR = "JetCutter Tools"

_COMMANDS = [
    "JetCutter_SameEdges",
    "JetCutter_FindProfiles",
]


def active_workbench_name():
    """Return the internal name of the workbench currently being set up.

    FreeCADGui.activeWorkbench().name() cannot be used during a core
    workbench's first activation (raises AttributeError). The handler
    object itself is correct, so the name is recovered by identity.
    """
    active = FreeCADGui.activeWorkbench()
    for name, handler in FreeCADGui.listWorkbenches().items():
        if handler is active:
            return name
    return ""


class Manipulator:
    def modifyToolBars(self):
        if _TARGET_WORKBENCH and active_workbench_name() != _TARGET_WORKBENCH:
            return []

        # First entry creates the toolbar (appending to unnamed root).
        # Subsequent entries append commands into the new toolbar.
        changes = [{"append": _TOOLBAR, "toolBar": ""}]
        changes += [{"append": cmd, "toolBar": _TOOLBAR} for cmd in _COMMANDS]
        return changes
