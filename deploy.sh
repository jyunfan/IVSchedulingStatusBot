#!/bin/bash
set -euo pipefail

REMOTE="iv"
REMOTE_DIR="~/IVSchedulingStatusBot"

echo "==> Syncing files to ${REMOTE}..."
rsync -avz --exclude '.venv' --exclude '__pycache__' --exclude '.git' --exclude 'bot.db' \
    ./ "${REMOTE}:${REMOTE_DIR}/"

echo "==> Building and starting on remote..."
ssh "${REMOTE}" bash -s <<'EOF'
cd ~/IVSchedulingStatusBot

# Install uv if not present
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Sync dependencies
uv sync --frozen --no-dev

# Stop existing bot if running
if [ -f bot.pid ]; then
    kill "$(cat bot.pid)" 2>/dev/null || true
    rm -f bot.pid
fi

# Source env file
if [ ! -f .env ]; then
    echo "ERROR: Create .env with TELEGRAM_BOT_TOKEN=<your-token>"
    exit 1
fi
set -a; source .env; set +a

export DB_PATH="${HOME}/IVSchedulingStatusBot/bot.db"

# Start bot in background
nohup uv run python bot.py > bot.log 2>&1 &
echo $! > bot.pid

echo "Bot started with PID $(cat bot.pid)"
EOF

echo "==> Deploy complete!"
