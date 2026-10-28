#!/usr/bin/env bash
# Scaffold a new project from dev-kit templates.
#
# Usage: ./init-project.sh <target-dir> <project-name> [python-fastapi|java-springboot|vue3-vite]
#
# Output is ASCII-only on purpose (avoids console encoding issues on Windows Git Bash).
set -euo pipefail

TARGET_DIR="${1:-}"
PROJECT_NAME="${2:-}"
STACK="${3:-python-fastapi}"
DEVKIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "$TARGET_DIR" || -z "$PROJECT_NAME" ]]; then
  echo "Usage: $0 <target-dir> <project-name> [stack]" >&2
  exit 1
fi

TEMPLATE_DIR="$DEVKIT_DIR/TEMPLATES/$STACK"
[[ -d "$TEMPLATE_DIR" ]] || { echo "[FAIL] Template not found: $TEMPLATE_DIR" >&2; exit 1; }

if [[ -d "$TARGET_DIR" ]] && [[ -n "$(ls -A "$TARGET_DIR" 2>/dev/null | grep -v '^\.git$')" ]]; then
  echo "[FAIL] Target directory is not empty: $TARGET_DIR" >&2
  exit 1
fi

echo "[ .. ] Creating $TARGET_DIR"
mkdir -p "$TARGET_DIR"

echo "[ .. ] Copying template: $STACK"
cp -R "$TEMPLATE_DIR"/. "$TARGET_DIR"/

if [[ "$STACK" == "python-fastapi" ]]; then
  echo "[ .. ] Vendoring devkit core (keeps the project self-contained)"
  cp -R "$DEVKIT_DIR/CORE/python/devkit" "$TARGET_DIR/devkit"
fi

echo "[ .. ] Dropping AGENTS.md / CLAUDE.md / REVIEW.md templates"
for f in AGENTS.md CLAUDE.md REVIEW.md; do
  src="$DEVKIT_DIR/SPEC/templates/$f.template"
  [[ -f "$src" && ! -f "$TARGET_DIR/$f" ]] && cp "$src" "$TARGET_DIR/$f"
done

CLEAN="${PROJECT_NAME//[^A-Za-z0-9]/_}"
PREFIX="$(echo "$CLEAN" | tr '[:lower:]' '[:upper:]')_"
SYS_CODE="$(echo "${CLEAN//_/}" | cut -c1-2 | tr '[:lower:]' '[:upper:]')"

echo "[ .. ] Substituting placeholders (prefix=$PREFIX)"
while IFS= read -r -d '' file; do
  case "$file" in
    */.venv/*|*/node_modules/*|*/.git/*|*/__pycache__/*) continue ;;
  esac
  sed -i.bak \
    -e "s|__APP_NAME__|$PROJECT_NAME|g" \
    -e "s|__APP_PREFIX__|$PREFIX|g" \
    -e "s|__APP_CODE_OK__|${SYS_CODE}0000|g" \
    -e "s|__APP_CODE_INTERNAL__|${SYS_CODE}5000|g" \
    -e "s|__PKG__|com.example.$(echo "$CLEAN" | tr '[:upper:]' '[:lower:]' | tr -d '_')|g" \
    "$file"
done < <(find "$TARGET_DIR" -type f -print0)

# 清理 sed -i.bak 产生的备份。用 find -delete 而不是 rm：
# GNU sed 在 macOS/BSD 下要求 -i 必须带后缀，而某些环境（含受限 shell）里 rm 会被包装拦截。
find "$TARGET_DIR" -name '*.bak' -type f -delete

if [[ -f "$TARGET_DIR/.env.example" ]]; then
  cp "$TARGET_DIR/.env.example" "$TARGET_DIR/.env"
  echo "[ OK ] Created .env from .env.example (never commit it)"
fi

echo ""
echo "[ OK ] Project created: $TARGET_DIR"
echo ""
echo "Next steps:"
case "$STACK" in
  python-fastapi)
    echo "  1. cd $TARGET_DIR"
    echo "  2. uv sync            (or: python -m venv .venv && pip install -e .)"
    echo "  3. pytest             (smoke test must pass)"
    echo "  4. uvicorn app.main:app --reload"
    echo "  5. Fill AGENTS.md / CLAUDE.md / REVIEW.md, then DELETE unfilled sections"
    ;;
  java-springboot)
    echo "  1. cd $TARGET_DIR"
    echo "  2. Confirm JDK baseline in README.md before coding"
    echo "  3. mvn -q compile"
    ;;
  vue3-vite)
    echo "  1. cd $TARGET_DIR"
    echo "  2. npm install && npm run build"
    ;;
esac
