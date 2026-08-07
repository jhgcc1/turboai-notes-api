#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp -n .env.example .env || true
LAUNCHER="$HOME/.cursor-mcp-launchers/turbo-notes-aws.sh"
mkdir -p "$HOME/.cursor-mcp-launchers"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
set -a
[ -f "$ROOT/.env" ] && . "$ROOT/.env"
set +a
exec "$ROOT/.venv/bin/python" "$ROOT/server.py"
EOF
chmod +x "$LAUNCHER"
echo "Installed. Add to ~/.cursor/mcp.json:"
echo "  \"turbo-notes-aws\": { \"command\": \"$LAUNCHER\" }"
echo "Then set AWS_SSO_START_URL and restart Cursor."
