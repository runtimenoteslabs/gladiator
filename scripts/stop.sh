#!/bin/bash
# Stop all Gladiator services
echo "Stopping Gladiator services..."

pkill -f "uvicorn dashboard.server:app" 2>/dev/null && echo "  Dashboard stopped" || echo "  Dashboard not running"
pkill -f "tsx.*src/index.ts" 2>/dev/null && echo "  Paperclip stopped" || echo "  Paperclip not running"
pkill -f "dev-runner" 2>/dev/null || true

echo "Done."
