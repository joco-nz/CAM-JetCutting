"""Every command's Pixmap must resolve to a real file in Resources/Icons.

Why this needs a check at all
-----------------------------
A `Pixmap` name that has no matching file is the one rename failure FreeCAD
reports *nothing* about. The command still registers, the toolbar still appears,
the button still works -- it just shows a blank square. No exception, no log
line, no non-zero exit anywhere. That is exactly the class of regression this
suite exists to catch, and it is the failure mode this project's own icon rename
had to be manually checked for.

What is asserted
----------------
1. The icon directory `init_gui.py` registers actually exists. If that path is
   wrong every icon fails at once and none of the per-command checks below would
   notice, because they resolve against the same wrong path.
2. Each command's `Pixmap` names a file that exists in that directory.
3. Each named icon is a non-empty file. A zero-byte file passes step 2 and then
   renders as nothing.
4. Unreferenced files are reported but do NOT fail the run: `jetcutter_tools.svg`
   is the workbench icon, consumed through a different path than `Pixmap`.

Scope: this is pure Python -- path resolution and `os.stat` -- so it belongs to
the fast pytest tier, which stubs FreeCAD. `commands` imports `FreeCADGui` and
PySide at module scope; the conftest stubs cover that, and nothing here calls
into either, so a stub cannot make any of these checks pass falsely.
"""
import os

import pytest

from jetcutter_tools import commands

_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)
ICON_DIR = os.path.join(REPO_ROOT, "Resources", "Icons")

# The command classes that register a toolbar button, by the name init_gui.py
# registers them under. Keyed by that name so the test fails if a command is
# renamed without this list being updated, rather than silently testing less.
COMMANDS = {
    "JetCutter_SameEdges": commands.SameEdgesAsHighlighted,
    "JetCutter_FindProfiles": commands.FindProfiles,
}

# Referenced by a mechanism other than GetResources()["Pixmap"], so a leftover
# is expected rather than a defect.
KNOWN_UNREFERENCED = {"jetcutter_tools"}


def _pixmaps():
    for registered_name, cls in COMMANDS.items():
        pixmap = cls().GetResources().get("Pixmap")
        yield registered_name, pixmap


def test_icon_dir_registered_by_init_gui_exists():
    """init_gui.py's addIconPath must point at a directory that exists.

    Parsed out of the source rather than duplicated here, so the two cannot
    drift: a change to one without the other is exactly the bug being guarded.
    """
    init_gui = os.path.join(REPO_ROOT, "freecad", "jetcutter_tools", "init_gui.py")
    with open(init_gui) as handle:
        source = handle.read()

    assert '"Resources", "Icons"' in source, (
        "init_gui.py no longer registers Resources/Icons by that path; update "
        "ICON_DIR in this test to match, or the per-command checks below will "
        "be resolving against a path the addon does not actually use"
    )
    assert os.path.isdir(ICON_DIR), "registered icon dir does not exist: %s" % ICON_DIR


@pytest.mark.parametrize("registered_name,pixmap", list(_pixmaps()))
def test_pixmap_resolves_to_a_real_icon_file(registered_name, pixmap):
    assert pixmap, "%s declares no Pixmap" % registered_name
    path = os.path.join(ICON_DIR, pixmap + ".svg")
    assert os.path.isfile(path), (
        "%s names Pixmap %r but %s does not exist. FreeCAD renders a blank "
        "button in this case and reports nothing, so it fails silently."
        % (registered_name, pixmap, path)
    )
    assert os.path.getsize(path) > 0, (
        "%s is zero bytes and would render as a blank button" % path
    )


def test_no_unexpected_orphan_icons():
    """Report files no command references.

    Informational: it does not fail, because `jetcutter_tools.svg` is the
    workbench icon and is consumed through a path that is not `Pixmap`. A new
    orphan is worth a look, but it is not automatically a defect, so this
    prints rather than asserting.
    """
    referenced = {pixmap for _, pixmap in _pixmaps() if pixmap}
    on_disk = {
        os.path.splitext(f)[0]
        for f in os.listdir(ICON_DIR)
        if f.endswith(".svg")
    }
    orphans = on_disk - referenced - KNOWN_UNREFERENCED
    if orphans:
        print("icons present but not referenced by any Pixmap: %s"
              % ", ".join(sorted(orphans)))
    assert referenced <= on_disk, (
        "referenced icons missing from disk: %s" % ", ".join(sorted(referenced - on_disk))
    )