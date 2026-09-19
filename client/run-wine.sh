#!/bin/sh
# Run the Windows client in the game's existing Wine environment.
set -eu

fail() {
    printf '%s\n' "$*" >&2
    exit 1
}

if [ "$#" -lt 1 ]; then
    fail 'Usage: WINEPREFIX=/absolute/game/prefix [WINE=/path/to/wine] sh run-wine.sh /path/to/asobby.exe'
fi
case "${WINEPREFIX:-}" in
    /*) ;;
    *) fail 'Set WINEPREFIX to the absolute path of the existing game prefix.' ;;
esac
[ -f "$WINEPREFIX/system.reg" ] || fail 'WINEPREFIX is not an existing Wine prefix (system.reg is missing).'
[ -f "$1" ] || fail "Client executable not found: $1"

asobby_wine_bin=$(command -v "${WINE:-wine}") || fail 'Wine executable not found. Set WINE to the same Wine runner used by the game.'
# Make the runner absolute before cd, preserving its entry point/symlink name.
case "$asobby_wine_bin" in
    /*) ;;
    *) asobby_wine_bin="$PWD/$asobby_wine_bin" ;;
esac
asobby_exe=$(realpath -- "$1")
shift
cd -- "$(dirname -- "$asobby_exe")"
export WINEPREFIX
export ASOBBY_WINE=1
exec "$asobby_wine_bin" "$asobby_exe" "$@"
