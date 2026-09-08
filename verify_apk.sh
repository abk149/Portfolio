#!/usr/bin/env bash
# Prove the APK contains the Python you just wrote.
#
# "Is my fix actually on the device?" has cost several round-trips. This answers
# it from the built artifact: it extracts the packaged bytecode and greps it for
# markers, and prints the build stamp the running app will report.
#
#   ./verify_apk.sh                      # stamp + freshness of every module
#   ./verify_apk.sh unpriced_holdings    # ...and check for specific markers
set -uo pipefail
cd "$(dirname "$0")"
APK="android/app/build/outputs/apk/debug/app-debug.apk"
[ -f "$APK" ] || { echo "No APK at $APK — run ./gradlew assembleDebug first."; exit 1; }

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
unzip -p "$APK" assets/chaquopy/app.imy > "$WORK/app.imy" 2>/dev/null || {
  echo "Couldn't read the Python bundle from the APK."; exit 1; }
unzip -qo "$WORK/app.imy" -d "$WORK/py"

echo "APK built:  $(date -r "$APK" '+%Y-%m-%d %H:%M:%S')"
python3 - "$WORK/py" <<'PY'
import marshal, pathlib, sys
root = pathlib.Path(sys.argv[1])
bi = root / "src" / "_build_info.pyc"
if bi.exists():
    co = marshal.loads(bi.read_bytes()[16:])
    vals = [c for c in co.co_consts if isinstance(c, str) and c and not c.startswith("Generated")]
    print("Build stamp:", " · ".join(vals[:2]))
else:
    print("Build stamp: MISSING (old build?)")
print("Modules packaged:", len(list(root.rglob('*.pyc'))))
PY

# Every .py in src/ should have a counterpart in the bundle.
missing=0
while IFS= read -r f; do
  rel="${f#src/}"
  [ -f "$WORK/py/src/${rel%.py}.pyc" ] || { echo "  MISSING from APK: $f"; missing=1; }
done < <(find src -name "*.py" -not -path "*/__pycache__/*")
[ $missing -eq 0 ] && echo "All src/*.py are present in the bundle."

# Markers are searched in BOTH layers: the Python bundle and the compiled
# Kotlin (dex). Searching only Python reported UI strings as "missing", which
# is worse than not checking at all.
if [ $# -gt 0 ]; then
  unzip -qo "$APK" "classes*.dex" -d "$WORK/dex" 2>/dev/null || true
fi
for marker in "$@"; do
  where=""
  grep -rql "$marker" "$WORK/py" 2>/dev/null && where="python"
  if grep -rql "$marker" "$WORK/dex" 2>/dev/null; then
    where="${where:+$where + }kotlin"
  fi
  if [ -n "$where" ]; then
    echo "  ✅ '$marker' found in $where"
  else
    echo "  ❌ '$marker' NOT in the APK — the device would run older code"
    missing=1
  fi
done
exit $missing
