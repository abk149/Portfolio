#!/usr/bin/env bash
# Run the suite against BOTH library stacks.
#
# The dev machine and the phone ship different pandas/fastapi versions, and
# several bugs only ever appeared on the phone's — a cache pickle that needs
# pyarrow, a resample alias that arrived in pandas 2.2, a TestClient that will
# not construct on old starlette. Testing only the dev stack sent those to the
# device to be found by hand.
#
#   ./run_tests.sh            both stacks
#   ./run_tests.sh dev        dev only (fast)
set -uo pipefail
cd "$(dirname "$0")"

PHONE_VENV="${PHONE_VENV:-/private/tmp/claude-501/-Users-abheekbhowmik-Downloads-Code-Portfolio/cd9dff43-6381-40b6-8db4-eed3b888fa3f/scratchpad/pd21}"
WHICH="${1:-both}"
rc=0

echo "── dev stack ($(.venv/bin/python -c 'import pandas;print("pandas",pandas.__version__)')) ──"
.venv/bin/python -m pytest tests/ -q -p no:cacheprovider || rc=1

if [ "$WHICH" = "both" ]; then
  if [ -x "$PHONE_VENV/bin/python" ]; then
    echo
    echo "── phone stack ($("$PHONE_VENV/bin/python" -c 'import pandas,fastapi;print("pandas",pandas.__version__,"fastapi",fastapi.__version__)')) ──"
    "$PHONE_VENV/bin/python" -m pytest tests/ -q -p no:cacheprovider || rc=1
  else
    echo
    echo "!! phone-stack venv missing — build it with:"
    echo "   python3 -m venv \$PHONE_VENV && \$PHONE_VENV/bin/pip install \\"
    echo "     'pandas==2.1.3' 'numpy==1.26.2' 'fastapi==0.99.1' 'pydantic==1.10.13' \\"
    echo "     'httpx<0.28' uvicorn requests python-dotenv rich jinja2 openpyxl \\"
    echo "     xlsxwriter tabulate beautifulsoup4 lxml python-multipart pytest"
    echo "   (matches what Chaquopy ships in the APK — no scipy, no pyarrow)"
    rc=1
  fi
fi

[ $rc -eq 0 ] && echo && echo "✅ all green on every stack tested"
exit $rc
