#!/usr/bin/env bash
# Install livehunt.
#   1) pipx (isolated, best)      2) dedicated venv (always works)      3) --user if allowed
set -euo pipefail
DIR=$(cd "$(dirname "$0")" && pwd)
VENV="${LIVEHUNT_VENV:-$HOME/.local/share/livehunt/venv}"
BIN="${BIN:-$HOME/.local/bin}"

echo "== livehunt installer =="
python3 - <<'EOF'
import sys
if sys.version_info < (3, 9):
    sys.exit("Python 3.9+ required (found %s)" % sys.version.split()[0])
print("python", sys.version.split()[0], "ok")
EOF

if command -v pipx >/dev/null 2>&1; then
  echo "using pipx"
  pipx install "$DIR" --force
  echo
  livehunt version
  exit 0
fi

echo "pipx not found — creating a dedicated venv at $VENV"
python3 -m venv "$VENV"
"$VENV/bin/python" -m pip install -q --upgrade pip >/dev/null 2>&1 || true
"$VENV/bin/python" -m pip install -q "$DIR"

mkdir -p "$BIN"
ln -sf "$VENV/bin/livehunt" "$BIN/livehunt"
ln -sf "$VENV/bin/livekit" "$BIN/livekit"

echo
if command -v livehunt >/dev/null 2>&1; then
  livehunt version
else
  echo "installed, but $BIN is not on PATH."
  echo "add this to your shell profile:  export PATH=\"$BIN:\$PATH\""
  "$BIN/livehunt" version
fi

# agent skill (optional, harmless if the dir does not exist)
SKILLS="${AGENTS_SKILLS:-$HOME/.agents/skills}"
mkdir -p "$SKILLS"
cp -r "$DIR/skills/livehunt" "$SKILLS/" 2>/dev/null && echo "skill installed: $SKILLS/livehunt"

echo
echo "next:"
echo "  1) livehunt chrome --print"
echo "  2) livehunt init mytarget --hosts app.example.com"
echo "  3) livehunt up mytarget"
echo
echo "docs: $DIR/docs/QUICKSTART.md  ·  $DIR/docs/TROUBLESHOOTING.md"
