#!/usr/bin/env bash
# Rebuild the published page and push it. This is the whole update mechanism.
#
# GitHub Pages serves docs/ straight from the repo, so pushing the built file IS publishing it —
# no Actions run, no deploy step, nothing to schedule on GitHub's side. That matters because
# GitHub's own scheduler never fired this workflow once across an entire trading day, while every
# manual run worked perfectly. Moving the trigger to this machine removes the unreliable part.
#
# Run by launchd every 15 minutes during market hours (see com.stocksel.refresh.plist), or by hand.
set -euo pipefail

cd "$(dirname "$0")"
export PATH="/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:$PATH"

LOG="$HOME/Library/Logs/stocksel-refresh.log"
say(){ echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

# Only during the session. launchd fires on wall-clock intervals and has no idea the market is shut;
# refreshing at 3am would burn Yahoo requests to redraw a page nobody is looking at, and repeated
# scanning is exactly what got us rate-limited before.
DOW=$(date +%u)                 # 1-5 = Mon-Fri
HM=$(date +%H%M)
if [ "$DOW" -gt 5 ] || [ "$HM" \< "0925" ] || [ "$HM" \> "1605" ]; then
  say "outside market hours ($DOW $HM) — skipping"; exit 0
fi

say "refreshing prices"
if ! python3 publish_static.py --prices >> "$LOG" 2>&1; then
  say "build FAILED — leaving the previous page in place"; exit 1
fi

# Nothing to commit is normal: prices may not have moved, or the same build ran twice.
if git diff --quiet -- docs/; then
  say "no change to publish"; exit 0
fi

git add docs/
git commit -qm "prices $(date '+%Y-%m-%d %H:%M')"
if git push -q origin main; then
  say "published"
else
  say "push FAILED — committed locally, will go out with the next run"
fi
