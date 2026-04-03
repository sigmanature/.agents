#!/usr/bin/env bash
set -euo pipefail

HOOK_DIR=".git/hooks"
mkdir -p "$HOOK_DIR"

cat > "$HOOK_DIR/pre-commit" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

python3 tools/agentctl.py close
EOF

chmod +x "$HOOK_DIR/pre-commit"
echo "Installed pre-commit close gate: .git/hooks/pre-commit"
