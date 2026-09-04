#!/usr/bin/env bash
# A/B test: the same question, on the same repo, with and without LeanVFS.
#
#   ./try-it.sh <path-to-any-python-repo> "your question"
#
# Prints what an agent would receive in each case, and what it costs in tokens.
set -euo pipefail

REPO="${1:?usage: ./try-it.sh <repo-path> [question]}"
Q="${2:-how are timeouts configured}"
REPO="$(cd "$REPO" && pwd)"
PY="$(dirname "$0")/.venv/bin/python"
export PYTHONPATH="$(dirname "$0")"

hr(){ printf '%*s\n' 78 '' | tr ' ' '-'; }
tok(){ $PY -c "import sys;t=sys.stdin.read();print(max(1,round(len(t)/4)) if t.strip() else 0)"; }

echo; hr; echo "  REPO: $REPO"; echo "  ASK:  \"$Q\""; hr; echo

# ── A: no index. What an agent does with grep and cat. ────────────────────────
echo "A · WITHOUT LeanVFS  (grep, then read the files it hits)"; hr
A_OUT="$(mktemp)"
mapfile -t HITS < <(grep -ril --include='*.py' "${Q%% *}" "$REPO" 2>/dev/null | head -3 || true)
if [ ${#HITS[@]} -eq 0 ]; then mapfile -t HITS < <(find "$REPO" -name '*.py' | head -3); fi
for f in "${HITS[@]}"; do cat "$f" >> "$A_OUT"; done
A_TOK=$(tok < "$A_OUT")
echo "  files opened : ${#HITS[@]}"
for f in "${HITS[@]}"; do echo "                 ${f#$REPO/}"; done
echo "  lines read   : $(wc -l < "$A_OUT")"
echo "  TOKENS       : $A_TOK"
echo

# ── B: LeanVFS. Index once, then ask. --------------------------------─────────
echo "B · WITH LeanVFS  (index once, then ask)"; hr
$PY -m leanvfs --repo "$REPO" sync 2>/dev/null | grep -E "^throughput" | sed "s/^/  /"
B_OUT="$(mktemp)"
$PY -m leanvfs --repo "$REPO" search "$Q" --limit 8 > "$B_OUT" 2>/dev/null
B_TOK=$(tok < "$B_OUT")
echo
sed 's/^/  /' "$B_OUT"
echo
echo "  TOKENS       : $B_TOK"
echo

# ── The comparison --------------------------------────────────────────────────
hr
$PY - "$A_TOK" "$B_TOK" <<'PYEOF'
import sys
a, b = int(sys.argv[1]), max(int(sys.argv[2]), 1)
print(f"  without LeanVFS : {a:>7,} tokens")
print(f"  with LeanVFS    : {b:>7,} tokens")
print(f"  reduction       : {a/b:>7.1f}x   ({100*(1-b/a):.1f}% fewer)" if a else "")
PYEOF
hr
cat <<'NOTE'

  What this does and does not show
  --------------------------------
  It shows the token cost of the FIRST step — locating the answer. That is where
  the saving is, and it is the step an agent repeats constantly.

  It is not a correctness measurement. Side B is only a win if its results are
  actually right, which is exactly what the benchmark exists to check:

      .venv/bin/python -m leanbench evaluate \
          --candidate leanvfs/leanvfs-candidate.toml --suite suites/httpx --track both

  A cheap wrong answer is worse than an expensive right one, so never read the
  token number without the correctness number beside it.
NOTE
