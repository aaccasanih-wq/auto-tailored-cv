#!/usr/bin/env bash
# install_skill.sh — install the project skills into the local
# Claude Code and Opencode skill directories so they're available from any
# project, not just this one.
#
# Usage:
#   ./scripts/install_skill.sh           # install (or update) the skills
#   ./scripts/install_skill.sh --uninstall   # remove the skills
#
# Safe to re-run; it overwrites existing copies with the repo versions.

set -euo pipefail

# <repo skill path>:<global claude file>:<global opencode file> entries.
# The .opencode/command/ copies are project-local (/command) and need no install.
SKILLS=(
  ".claude/skills/cv_automatizacion.md:cv_automatizacion.md:cv_automatizacion/SKILL.md"
  ".claude/skills/editar_cv.md:editar_cv.md:editar_cv/SKILL.md"
)

for entry in "${SKILLS[@]}"; do
  if [ ! -f "${entry%%:*}" ]; then
    echo "ERROR: ${entry%%:*} not found. Run this script from the repo root." >&2
    exit 1
  fi
done

ACTION="install"
if [ "${1:-}" = "--uninstall" ] || [ "${1:-}" = "-u" ]; then
  ACTION="uninstall"
fi

# --- Claude Code (global, available in every project) -----------------------
CLAUDE_GLOBAL_DIR="$HOME/.claude/skills"

# --- Opencode (global, available in every project) --------------------------
OPENCODE_GLOBAL_DIR="$HOME/.config/opencode/skills"

# --- Opencode commands (project-local, used as /<name>) ---------------------
# (Already in the repo at .opencode/command/; no install needed.)

if [ "$ACTION" = "uninstall" ]; then
  for entry in "${SKILLS[@]}"; do
    rest="${entry#*:}"
    claude_file="$CLAUDE_GLOBAL_DIR/${rest%%:*}"
    opencode_file="$OPENCODE_GLOBAL_DIR/${rest#*:}"
    rm -f "$claude_file" "$opencode_file"
    rmdir "$(dirname "$opencode_file")" 2>/dev/null || true
    echo "Uninstalled:"
    echo "  - $claude_file"
    echo "  - $opencode_file"
  done
  exit 0
fi

mkdir -p "$CLAUDE_GLOBAL_DIR" "$OPENCODE_GLOBAL_DIR"
for entry in "${SKILLS[@]}"; do
  repo_file="${entry%%:*}"
  rest="${entry#*:}"
  claude_file="$CLAUDE_GLOBAL_DIR/${rest%%:*}"
  opencode_subdir="$OPENCODE_GLOBAL_DIR/$(dirname "${rest#*:}")"
  opencode_file="$OPENCODE_GLOBAL_DIR/${rest#*:}"
  mkdir -p "$opencode_subdir"
  cp "$repo_file" "$claude_file"
  cp "$repo_file" "$opencode_file"
  chmod 644 "$claude_file" "$opencode_file"
  echo "Installed:"
  echo "  Claude Code  -> $claude_file"
  echo "  Opencode     -> $opencode_file"
done

echo
echo "The skills are now available from any directory. In Claude Code or"
echo "Opencode, just say: \"generá el CV para la oferta <url>\" or \"editá mi CV base\" and the"
echo "assistant will help you."
echo
echo "The project-local copies (.opencode/command/*.md) are"
echo "also available as /commands when you open"
echo "Opencode in this repo."
