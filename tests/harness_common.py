"""Shared helpers for the `freecadcmd` and GUI tiers.

Run under `freecadcmd`, which ships its own interpreter without pytest, so
these scripts are plain scripts rather than test modules. Each writes a status
file that `tests/run.sh` reads back — `freecadcmd` does not reliably propagate
a script's exit code, so the status file is the only way a check can gate.

Status contract
---------------
Write "0" for pass, "1" for fail. **Write the file on every path**, including
the error path: `run.sh` treats a missing status file as "the check did not
run", which is a failure, because a check that silently did not run is the same
as having no check at all.
"""
import os
import sys
import traceback

# This file lives in tests/, so the repo root is one level up. Shared by both
# FreeCAD tiers — deliberately NOT named like a script, so no tier's
# `if __name__ in (...)` guard can match it and re-execute it on import.
_HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(_HERE)

# One status directory for every tier, so run.sh has a single place to look and
# cannot miss a tier's results.
STATUS_DIR = os.path.join(_HERE, "status")


def status_path(name):
    """Return the status-file path for a named check."""
    return os.path.join(STATUS_DIR, name + ".status")


def write_status(name, value):
    """Record a check's result. Never raises — a failure here must not mask
    the result being reported.
    """
    try:
        os.makedirs(STATUS_DIR, exist_ok=True)
        with open(status_path(name), "w") as handle:
            handle.write(str(int(value)))
    except OSError:
        pass


class Report:
    """Accumulates pass/fail detail for one check.

    Deliberately not an assert-based harness: under `freecadcmd` an assertion
    failure raises, and the traceback is less informative than the list of
    everything that failed. `main()` returns 1 if anything failed.
    """

    def __init__(self, name):
        self.name = name
        self._failures = []
        print("=" * 72)
        print(name)
        print("=" * 72)

    def emit(self, message=""):
        print(message)

    def check(self, condition, detail):
        """Record one check. `detail` should say what was wanted, not just fail."""
        if condition:
            print("  PASS  %s" % detail)
        else:
            print("  FAIL  %s" % detail)
            self._failures.append(detail)
        return bool(condition)

    def check_eq(self, got, want, detail):
        return self.check(got == want, "%s: got %r, want %r" % (detail, got, want))

    @property
    def failed(self):
        return bool(self._failures)

    def finish(self):
        if self._failures:
            print("\nFAILED: %d check(s)" % len(self._failures))
            for failure in self._failures:
                print("  - %s" % failure)
            return 1
        print("\nAll checks passed.")
        return 0


def run(name, body):
    """Run `body(report)` and write the status file on every path."""
    report = Report(name)
    try:
        body(report)
        code = report.finish()
    except Exception as exc:  # noqa: BLE001
        print("harness raised: %s: %s" % (type(exc).__name__, exc))
        print(traceback.format_exc())
        code = 1
    finally:
        write_status(name, code)
    return code


def add_freecad_to_path():
    """Make this directory and the addon importable under the FreeCAD interpreters.

    Two separate things, and `freecadcmd` needs both done explicitly:

    1. This directory, so a script can `import harness_common`. The interpreter
       does not put the script's own directory on `sys.path`.
    2. `<repo>/freecad`, because `jetcutter_tools` lives under it. FreeCAD's
       namespace-package loader finds that automatically for an *installed*
       addon; for a checkout the directory has to be on the path.
    """
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    freecad_dir = os.path.join(REPO_ROOT, "freecad")
    if freecad_dir not in sys.path:
        sys.path.insert(0, freecad_dir)


def stub_freecadgui():
    """Install a minimal `FreeCADGui` so `commands` imports headlessly.

    `commands` imports FreeCADGui at module scope but the geometry path never
    touches it. The stub is inert: any actual use raises AttributeError on None
    rather than returning a plausible wrong answer. The GUI tier does NOT need
    this — there the real module is present.
    """
    import types

    if "FreeCADGui" in sys.modules:
        return

    gui = types.ModuleType("FreeCADGui")
    gui.Selection = None
    gui.ActiveDocument = None
    gui.Control = types.SimpleNamespace(closeDialog=lambda: None)
    gui.getMainWindow = lambda: None
    gui.addCommand = lambda *a, **k: None
    gui.addIconPath = lambda *a: None
    gui.addWorkbenchManipulator = lambda *a: None
    sys.modules["FreeCADGui"] = gui


def require_real_freecad():
    """Fail loudly if a real FreeCAD is not present.

    A geometry check that ran against a stub would produce confident nonsense.
    This is the guard against that, and the reason it exists: the fast pytest
    tier stubs FreeCAD, so a script accidentally run under it must not be able
    to pass.
    """
    import FreeCAD

    if getattr(FreeCAD, "GuiUp", 0) == 0:
        return "freecadcmd"
    return "gui"
