#!/bin/zsh
# Regenerates assets/claude-usage.svg from local Claude Code transcripts and pushes it if anything changed.
set -e
cd "${0:A:h}/.."
/usr/bin/python3 -I scripts/claude_usage.py
if [[ -n "$(git status --porcelain assets)" ]]; then
  git add assets
  git commit -q -m "Update Claude Code activity ($(date +%Y-%m-%d))"
  git push -q origin HEAD
fi
