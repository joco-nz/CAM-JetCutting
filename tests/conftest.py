"""Shared pytest configuration for the JetCutter Tools test suite.

`freecad/jetcutter_tools/commands.py` does `import FreeCAD as App` and
`import FreeCADGui as Gui` at module scope, so it cannot be imported by a plain
`pytest` interpreter. This conftest installs minimal stand-ins for those
modules *only when they are genuinely absent*, which lets `python3 -m pytest`
collect and run the fast suite.

Scope of this tier
------------------
It covers whatever needs no real FreeCAD or Qt: after CR-03 the concave-detection
geometry lives in `jetcutter_tools.geometry`, which imports neither. That module
is imported here *without* any stub, deliberately — if it ever grows a FreeCAD
dependency this conftest will not hide it.

It does NOT cover document or geometry extraction. Those need a real FreeCAD:
`freecadcmd` for the view-provider-free paths, and the GUI binary for anything
reaching `ViewObject` or `FreeCADGui.UiLoader`. See `tests/run.sh`.

The stubs are deliberately inert: attribute access returns `None`. That is enough
to satisfy module-level `import FreeCAD`; any actual use of a FreeCAD API then
fails loudly with an `AttributeError` on `None` rather than silently returning a
plausible-looking wrong answer. A stub that pretends to work is worse than no
stub at all.
"""
import sys
import types

_REAL_MODULES = ("FreeCAD", "Part", "FreeCADGui")
_STUBBED = []


def _install_stub(name):
    """Register an inert placeholder for a module that cannot be imported.

    The package `__getattr__` returns `None` for *every* attribute, including
    submodule names, so `import FreeCAD.Gui`-style lookups resolve without
    exploding at import time.
    """
    module = types.ModuleType(name)

    def __getattr__(attr, _name=name):
        # A real FreeCAD submodule or attribute. Returning None keeps callers
        # from exploding on import-time lookups while making any actual use fail
        # loudly with a clear AttributeError on None.
        return None

    module.__path__ = []  # mark it as a package so submodule imports resolve
    module.__getattr__ = __getattr__
    sys.modules[name] = module
    _STUBBED.append(name)
    return module


def ensure_freecad_importable():
    """Ensure FreeCAD/Part/FreeCADGui are importable, stubbing only if needed.

    Returns the list of module names that had to be stubbed — empty when a real
    FreeCAD is present, which is what happens under `freecadcmd`. Tests that need
    real geometry assert on this so a silent stub cannot make them pass.
    """
    for name in _REAL_MODULES:
        if name in sys.modules:
            continue
        try:
            __import__(name)
        except ImportError:
            _install_stub(name)
    return list(_STUBBED)


STUBBED_MODULES = ensure_freecad_importable()


def using_real_freecad():
    """True when a real FreeCAD is importable and no stub is standing in.

    The `freecadcmd` and GUI tiers rely on this being True. If a test that needs
    real geometry ever runs under plain pytest and gets a stub, this is how it
    refuses to produce a false pass.
    """
    return "FreeCAD" not in STUBBED_MODULES


# Make the addon importable without installing it. `freecad/` is the directory
# holding the `jetcutter_tools` package, matching how FreeCAD's namespace-package
# loader finds it (see InitGui.py).
_REPO_ROOT = __file__.rsplit("/tests/", 1)[0]
_FREECAD_DIR = _REPO_ROOT + "/freecad"
if _FREECAD_DIR not in sys.path:
    sys.path.insert(0, _FREECAD_DIR)

# The FreeCAD tiers are scripts, not pytest modules. `tests/gui/` and
# `tests/freecadcmd/` hold files named test_*.py, which pytest would otherwise
# collect: importing one runs a real FreeCAD check under plain pytest, where it
# fails on a missing `Part` and takes the whole run down with a collection
# error. They are driven by tests/run.sh instead.
collect_ignore = ["gui", "freecadcmd"]
