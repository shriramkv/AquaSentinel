#!/usr/bin/env bash
# Start AquaSentinel locally: backend on :8080, dashboard on :5173.
# First run sets everything up (Python 3.12 venv via uv, npm packages).
set -euo pipefail
cd "$(dirname "$0")"
PORT="${BACKEND_PORT:-8080}"
PY=backend/.venv/bin/python

[ -f .env ] || { cp .env.example .env; echo "Created .env - add your GEMINI_API_KEY to enable the agents."; }

if [ ! -x "$PY" ]; then
  command -v uv >/dev/null || { echo "Install uv first: https://docs.astral.sh/uv/"; exit 1; }
  uv venv --python 3.12 backend/.venv
  uv pip install --python "$PY" -r backend/requirements.txt
fi

# macOS: LightGBM's wheel expects Homebrew's libomp. If it is missing, point it at the copy
# that scikit-learn ships inside this venv.
if [ "$(uname)" = "Darwin" ] && ! "$PY" -c "import lightgbm" 2>/dev/null; then
  SP="$("$PY" -c 'import sysconfig; print(sysconfig.get_paths()["purelib"])')"
  install_name_tool -add_rpath "$SP/sklearn/.dylibs" "$SP/lightgbm/lib/lib_lightgbm.dylib"
  "$PY" -c "import lightgbm" || { echo "LightGBM still cannot load libomp. Install it with: brew install libomp"; exit 1; }
fi

[ -d frontend/node_modules ] || (cd frontend && npm install)

trap 'kill 0' EXIT INT TERM
# Default 127.0.0.1: the control endpoints have no login, so they are not exposed to the network.
# To open the dashboard on a phone or tablet on the same Wi-Fi, run:  HOST=0.0.0.0 ./run.sh
# Only do that on a network you trust. Voice input will not work there: browsers allow the
# microphone only on https or localhost.
HOST="${HOST:-127.0.0.1}"
(cd backend && exec .venv/bin/uvicorn aquasentinel.main:app --host 127.0.0.1 --port "$PORT") &
(cd frontend && BACKEND_PORT="$PORT" exec npm run dev -- --host "$HOST") &
echo "Dashboard: http://localhost:5173   API docs: http://localhost:$PORT/docs"
if [ "$HOST" != "127.0.0.1" ]; then
  IP="$(ipconfig getifaddr en0 2>/dev/null || hostname -I 2>/dev/null | awk '{print $1}')"
  echo "On your phone or tablet (same Wi-Fi): http://${IP:-THIS-COMPUTER-IP}:5173"
fi
wait
