#!/usr/bin/env bash
# Commits the hourly check's results.
#   RISK=none -> only the page fingerprints / check report change
#   RISK=low  -> the ticket update goes straight to main (then gets published)
#   RISK=high -> the update goes to a pull request for review; fingerprints still go to main
set -euo pipefail

git config user.name "gppassport-ticket-bot"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"

published=false
summary=""
if [ -f .ticket-watch/summary.md ]; then
  summary="$(head -c 3000 .ticket-watch/summary.md)"
fi

if [ "${RISK}" = "high" ]; then
  cp tickets.json "${RUNNER_TEMP}/tickets.proposed.json"
  git checkout -- tickets.json
fi

git add state
if [ "${RISK}" = "low" ]; then
  git add tickets.json
fi

if ! git diff --cached --quiet; then
  if [ "${RISK}" = "low" ]; then
    git commit -m "Tickets: automatic update" -m "${summary}"
    published=true
  else
    git commit -m "Ticket watch: update page check state"
  fi
  git push origin HEAD:main
fi

if [ "${RISK}" = "high" ]; then
  branch="tickets/review-${GITHUB_RUN_ID}"
  git switch -c "${branch}"
  cp "${RUNNER_TEMP}/tickets.proposed.json" tickets.json
  git add tickets.json
  git commit -m "Tickets: update needs review" -m "${summary}"
  git push origin "${branch}"
  {
    echo "The hourly ticket check found changes that need a human look before they go live."
    echo
    cat .ticket-watch/report.md
    echo
    echo "### Claude's summary"
    echo
    echo "${summary:-_(none)_}"
    echo
    echo "**Merge** to publish, or **close** to discard."
  } > "${RUNNER_TEMP}/pr.md"
  gh pr create --base main --head "${branch}" \
    --title "Ticket update needs review ($(date -u +%F))" \
    --body-file "${RUNNER_TEMP}/pr.md"
fi

echo "published=${published}" >> "${GITHUB_OUTPUT}"
