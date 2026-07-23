#!/usr/bin/env bash
# install_skill.sh — install the cv_automatizacion skill into the local
# Claude Code and Opencode skill directories so it's available from any
# project, not just this one.
#
# Usage:
#   ./scripts/install_skill.sh           # install (or update) the skill
#   ./scripts/install_skill.sh --uninstall   # remove the skill
#
# Safe to re-run; it overwrites existing copies with the repo version.

set -euo pipefail

REPO_SKILL=".claude/skills/cv_automatizacion.md"
OPENCODE_CMD=".opencode/command/cv_automatizacion.md"

if [ ! -f "$REPO_SKILL" ]; then
  echo "ERROR: $REPO_SKILL not found. Run this script from the repo root." >&2
  exit 1
fi

ACTION="install"
if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-u" ]; then
  ACTION="uninstall"
fi

# --- Claude Code (global, available in every project) -----------------------
CLAUDE_GLOBAL_DIR="$HOME/.claude/skills"
CLAUDE_GLOBAL_FILE="$CLAUDE_GLOBAL_DIR/cv_automatizacion.md"

# --- Opencode (global, available in every project) --------------------------
OPENCODE_GLOBAL_DIR="$HOME/.config/opencode/skills/cv_automatizacion"
OPENCODE_GLOBAL_FILE="$OPENCODE_GLOBAL_DIR/SKILL.md"

# --- Opencode command (project-local, used as /cv_automatizacion) -----------
# (Already in the repo at .opencode/command/; no install needed.)

if [ "$ACTION" = "uninstall" ]; then
  rm -f "$CLAUDE_GLOBAL_FILE" "$OPENCODE_GLOBAL_FILE"
  rmdir "$OPENCODE_GLOBAL_DIR" 2>/dev/null || true
  echo "Uninstalled cv_automatizacion skill from:"
  echo "  - $CLAUDE_GLOBAL_FILE"
  echo "  - $OPENCODE_GLOBAL_FILE"
  exit 0
fi

mkdir -p "$CLAUDE_GLOBAL_DIR" "$OPENCODE_GLOBAL_DIR"
cp "$REPO_SKILL" "$CLAUDE_GLOBAL_FILE"
cp "$REPO_SKILL" "$OPENCODE_GLOBAL_FILE"
chmod 644 "$CLAUDE_GLOBAL_FILE" "$OPENCODE_GLOBAL_FILE"

echo "Installed cv_automatizacion skill:"
echo "  Claude Code  -> $CLAUDE_GLOBAL_FILE"
echo "  Opencode     -> $OPENCODE_GLOBAL_FILE"
echo
echo "The skill is now available from any directory. In Claude Code or"
echo "Opencode, just say: \"generá el CV para la oferta <url>\" and the"
echo "assistant will run the pipeline for you."
echo
echo "The project-local copy (.opencode/command/cv_automatizacion.md) is"
echo "also available as the /cv_automatizacion command when you open"
echo "Opencode in this repo."
