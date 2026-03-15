#!/bin/bash
# Launch the Gladiator system
set -e

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$PROJECT_DIR/base-product/.venv/bin"

echo "========================================="
echo "  GLADIATOR — Starting all services"
echo "========================================="

# 1. Check Paperclip
echo ""
echo "[1/3] Checking Paperclip..."
if curl -s http://localhost:3100/api/health | grep -q '"ok"'; then
    echo "  Paperclip already running on :3100"
else
    echo "  Starting Paperclip..."
    cd ~/paperclip
    DATABASE_URL="postgres://paperclip:paperclip@localhost:5432/paperclip" pnpm dev &
    sleep 10
    if curl -s http://localhost:3100/api/health | grep -q '"ok"'; then
        echo "  Paperclip started on :3100"
    else
        echo "  ERROR: Paperclip failed to start"
        exit 1
    fi
fi

# 2. Init evidence DB
echo ""
echo "[2/3] Initializing evidence database..."
cd "$PROJECT_DIR"
"$VENV/python" -c "
import sys; sys.path.insert(0, '.')
from traces.db import init_db
init_db()
print('  evidence.db ready')
"

# 3. Start dashboard
echo ""
echo "[3/3] Starting dashboard..."
pkill -f "uvicorn dashboard.server:app" 2>/dev/null || true
sleep 1
"$VENV/python" -m uvicorn dashboard.server:app --host 0.0.0.0 --port 4000 &
sleep 2

echo ""
echo "========================================="
echo "  GLADIATOR — All systems go!"
echo "========================================="
echo "  Paperclip:  http://localhost:3100"
echo "  Dashboard:  http://localhost:4000"
echo ""
echo "  To start the competition, Paperclip heartbeats"
echo "  will trigger agents automatically."
echo "========================================="
