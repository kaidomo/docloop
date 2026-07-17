#!/bin/sh
# Public-repo leak scan (hardening plan D4, impl r3-01).
# Usage: tools/leak_scan.sh '<private-token-regex>'
# Exit contract: 0 = no matches anywhere; 1 = any match (release blocker);
# 2 = scan command error. (Raw `git grep` exits 0 on match — inverted here.)
set -u
[ $# -eq 1 ] || { echo "usage: $0 '<token-regex>'" >&2; exit 2; }
P="$1"
out_wt=$(git grep --untracked -inE "$P" 2>&1); rc_wt=$?
out_ix=$(git grep --cached -inE "$P" 2>&1); rc_ix=$?
if [ "$rc_wt" -ge 2 ] || [ "$rc_ix" -ge 2 ]; then
  printf '%s\n%s\n' "$out_wt" "$out_ix" >&2; echo "scan error" >&2; exit 2
fi
if [ "$rc_wt" -eq 0 ] || [ "$rc_ix" -eq 0 ]; then
  printf '%s\n%s\n' "$out_wt" "$out_ix" | sed '/^$/d'; exit 1
fi
exit 0
