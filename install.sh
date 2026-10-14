#!/usr/bin/env bash
# Installs the CamusDB skills for Claude Code, Codex, and Cursor.
#
#   ./install.sh                      # user level, all agents, symlinks
#   ./install.sh --agent claude       # only ~/.claude/skills
#   ./install.sh --agent agents       # only ~/.agents/skills (Codex and Cursor)
#   ./install.sh --project ~/my-app   # into <project>/.claude/skills and <project>/.agents/skills
#   ./install.sh --copy               # copy the files instead of symlinks
#   ./install.sh --uninstall          # remove the CamusDB skills from the targets
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS_DIR="$REPO_DIR/skills"

agent="all"
project=""
mode="link"
uninstall=0

usage() {
  sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'
  exit "${1:-0}"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent) agent="${2:-}"; shift 2 ;;
    --project) project="${2:-}"; shift 2 ;;
    --copy) mode="copy"; shift ;;
    --link) mode="link"; shift ;;
    --uninstall) uninstall=1; shift ;;
    -h|--help) usage 0 ;;
    *) echo "Unknown option: $1" >&2; usage 1 ;;
  esac
done

case "$agent" in
  all|claude|agents|codex|cursor) ;;
  *) echo "--agent must be one of: all, claude, agents, codex, cursor" >&2; exit 1 ;;
esac

if [[ -n "$project" ]]; then
  base="$(cd "$project" && pwd)"
else
  base="$HOME"
fi

targets=()
if [[ "$agent" == "all" || "$agent" == "claude" ]]; then
  targets+=("$base/.claude/skills")
fi
# Codex and Cursor both read .agents/skills.
if [[ "$agent" == "all" || "$agent" == "agents" || "$agent" == "codex" || "$agent" == "cursor" ]]; then
  targets+=("$base/.agents/skills")
fi

for target in "${targets[@]}"; do
  mkdir -p "$target"
  for skill in "$SKILLS_DIR"/*/; do
    name="$(basename "$skill")"
    dest="$target/$name"

    if [[ -e "$dest" || -L "$dest" ]]; then
      if [[ -L "$dest" || -f "$dest/SKILL.md" ]]; then
        rm -rf "$dest"
        [[ "$uninstall" -eq 1 ]] && echo "removed  $dest"
      else
        echo "skip  $dest (exists and is not a skill directory)" >&2
        continue
      fi
    fi

    if [[ "$uninstall" -eq 1 ]]; then
      continue
    fi

    if [[ "$mode" == "link" ]]; then
      ln -s "${skill%/}" "$dest"
    else
      cp -R "${skill%/}" "$dest"
    fi
    echo "$mode  $dest"
  done
done
