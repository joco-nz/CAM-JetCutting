"""Bootstrap shim for FreeCAD addon discovery.

This file exists because FreeCAD's addon loader (DirModGui) looks for
InitGui.py at the addon root directory. It does not have any mechanism
for discovering workbench manipulators registered from within a namespace
package (freecad.jetcutter_tools.init_gui).

The <content> tags in package.xml support <workbench>, <preferencepack>,
and other types, but there is no <manipulator> element. So FreeCAD will
never find our init_gui.py on its own — it only runs InitGui.py at the
addon root.

This shim simply imports the real initialization code from the namespace
package, which registers the commands and the workbench manipulator.

See:
- FreeCADGuiInit.py DirModGui.run_init_gui() - looks for InitGui.py
- FreeCADGuiInit.py DirModGui.process_metadata() - handles <workbench> tags
- FreeCADInit.py ExtModScanner - scans freecad.* namespace via pkgutil
"""

import freecad.jetcutter_tools.init_gui  # noqa: F401
