#!/bin/sh
# Local gate for CAM-JetCutting. Runs every tier and reports one verdict.
#
#   tests/run.sh                 # gate everything
#   tests/run.sh --fast          # pytest only (no FreeCAD needed)
#   tests/run.sh --tier=N        # run one tier: pytest | freecadcmd | gui
#
# Tiers
# -----
#   pytest      pure Python, no FreeCAD. The concave-detection geometry lives in
#               jetcutter_tools.geometry, which imports neither FreeCAD nor Qt, so
#               this needs no stubs at all for the code under test. Fast.
#   freecadcmd  real FreeCAD document and Part geometry. No view providers.
#   gui         the GUI binary. Needed for anything touching ViewObject or
#               FreeCADGui.UiLoader — notably LeadInOutDressup.Create.
#
# Why a status file instead of an exit code
# -----------------------------------------
# freecadcmd does not reliably propagate a script's exit code. Each script
# therefore writes tests/status/<name>.status ("0" pass, "1" fail) and this
# wrapper reads them back, so the usual shell contract holds and a failure
# actually gates.
#
# A missing status file is a FAILURE, not a skip: a check that silently did not
# run is the same failure as having no check at all. That has bitten this
# codebase before — a bug sat behind a green gate because no gated test
# exercised its path.
#
# Override the interpreters if yours live elsewhere:
#   FREECAD_PREFIX=/path/to/appimage/usr tests/run.sh
set -eu

TESTS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$TESTS_DIR/.." && pwd)
STATUS_DIR="$TESTS_DIR/status"
PYTHON=${PYTHON:-python3}

# The FreeCAD interpreters default to an extracted AppImage, which is how this
# was developed. Point FREECAD_PREFIX elsewhere for a system install.
FREECAD_PREFIX=${FREECAD_PREFIX:-$HOME/freecad_env/usr}
FREECADCMD=${FREECADCMD:-$FREECAD_PREFIX/bin/freecadcmd}
GUI_FREECAD=${GUI_FREECAD:-$FREECAD_PREFIX/bin/freecad}

TIER=all
for arg in "$@"; do
    case "$arg" in
        --fast) TIER=pytest ;;
        --tier=*) TIER=${arg#--tier=} ;;
        -h|--help)
            sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

# Interpreter environment shared by both FreeCAD tiers. No DISPLAY is set or
# needed: the GUI binary starts its event loop offscreen on this Qt build.
freecad_env() {
    PYTHONHOME="$FREECAD_PREFIX"
    PATH_TO_FREECAD_LIBDIR="$FREECAD_PREFIX/lib"
    SSL_CERT_FILE="$FREECAD_PREFIX/ssl/cacert.pem"
    QT_QPA_PLATFORM=offscreen
    export PYTHONHOME PATH_TO_FREECAD_LIBDIR SSL_CERT_FILE QT_QPA_PLATFORM
}

missing_interpreters() {
    for exe in "$@"; do
        [ -x "$exe" ] || echo "  $exe"
    done
}

run_pytest() {
    echo "--- tier 1/3: pytest (pure Python) ---"
    if ! "$PYTHON" -c 'import pytest' 2>/dev/null; then
        echo "  pytest is not installed for $PYTHON — FAILING rather than skipping."
        echo "  install it, or run: $PYTHON -m pip install pytest"
        return 1
    fi
    if (cd "$REPO_ROOT" && "$PYTHON" -m pytest tests/ -q); then
        echo "  pytest: PASS"
        return 0
    fi
    echo "  pytest: FAIL"
    return 1
}

# Each entry is "<script> <tier>". The script name is also the status name.
run_script() {
    script=$1
    # The status name must match the STATUS constant inside the script. Scripts
    # are named test_<name>.py to be collected, and write "<name>.status" —
    # strip the prefix so the two agree.
    label=$(basename "$script" .py)
    label=${label#test_}
    dir=$(dirname "$script")
    case "$dir" in
        *freecadcmd) interpreter=$FREECADCMD ;;
        *gui) interpreter=$GUI_FREECAD ;;
        *) echo "  $script: unknown tier directory" >&2; return 1 ;;
    esac
    if [ ! -x "$interpreter" ]; then
        echo "  $label: SKIPPED — $interpreter not found" >&2
        return 0   # reported separately by the preflight check
    fi
    rm -f "$STATUS_DIR/$label.status"
    # The interpreter's own exit code is unreliable, so its output is discarded
    # and the status file is the verdict. `|| true` keeps set -e from aborting
    # the whole gate because one script exited non-zero.
    (cd "$REPO_ROOT" && freecad_env && "$interpreter" "$script") || true
    if [ ! -f "$STATUS_DIR/$label.status" ]; then
        echo "  $label: DID NOT RUN — no status file written" >&2
        return 1
    fi
    if [ "$(cat "$STATUS_DIR/$label.status")" = "0" ]; then
        echo "  $label: PASS"
        return 0
    fi
    echo "  $label: FAIL (status $(cat "$STATUS_DIR/$label.status"))" >&2
    return 1
}

run_tier() {
    dir=$1
    rc=0
    for script in "$dir"/test_*.py; do
        [ -e "$script" ] || continue
        run_script "$script" || rc=1
    done
    return $rc
}

rm -rf "$STATUS_DIR"
mkdir -p "$STATUS_DIR"

STATUS=0

# ---- preflight ------------------------------------------------------------
if [ "$TIER" = "all" ]; then
    echo "--- preflight ---"
    absent=$(missing_interpreters "$FREECADCMD" "$GUI_FREECAD")
    if [ -n "$absent" ]; then
        echo "  missing FreeCAD interpreters:" >&2
        echo "$absent" >&2
        echo "  set FREECAD_PREFIX, or run --fast for the pytest tier only." >&2
        STATUS=1
    else
        echo "  freecadcmd: $FREECADCMD"
        echo "  gui       : $GUI_FREECAD"
    fi
    echo
fi

# ---- tiers ----------------------------------------------------------------
if [ "$TIER" = "all" ] || [ "$TIER" = "pytest" ]; then
    run_pytest || STATUS=1
    echo
fi

if [ "$TIER" = "all" ] || [ "$TIER" = "freecadcmd" ]; then
    echo "--- tier 2/3: freecadcmd (real document, no view providers) ---"
    run_tier "$TESTS_DIR/freecadcmd" || STATUS=1
    echo
fi

if [ "$TIER" = "all" ] || [ "$TIER" = "gui" ]; then
    echo "--- tier 3/3: gui binary (ViewObject, UiLoader) ---"
    run_tier "$TESTS_DIR/gui" || STATUS=1
    echo
fi

# ---- verdict -------------------------------------------------------------
# Printed last, once, after every status has folded into $STATUS. Printing it
# first would put "PASS" above the line explaining a failure — which is the
# exact moment a gate is worth having.
if [ "$STATUS" -eq 0 ]; then
    echo "gate: PASS"
else
    echo "gate: FAIL" >&2
fi
exit "$STATUS"