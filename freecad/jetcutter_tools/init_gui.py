"""init_gui.py - FreeCAD addon initialization for JetCutter Tools."""

import os

import FreeCADGui
from . import commands
from .manipulator import Manipulator

_ADDON_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FreeCADGui.addIconPath(os.path.join(_ADDON_ROOT, "Resources", "Icons"))

FreeCADGui.addCommand('JetCutter_SameEdges', commands.SameEdgesAsHighlighted())
FreeCADGui.addCommand('JetCutter_FindProfiles', commands.FindProfiles())

FreeCADGui.addWorkbenchManipulator(Manipulator())
