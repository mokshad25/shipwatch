#!/usr/bin/env bash
# Drive one throwaway PR through its whole lifecycle against the `sandbox` branch so GitHub sends
# pull_request webhooks for: opened, synchronize, edited, closed (not merged), reopened, closed (merged).
# Run `make forward` (or the capture flow in docs/SETUP_GITHUB.md) in another terminal first.
set -euo pipefail

BASE="${SANDBOX_BASE:-sandbox}"
PAUSE="${SANDBOX_PAUSE:-3}" # seconds between steps, so deliveries arrive in order

die() { echo "sandbox_pr: $*" >&2; exit 1; }

cd "$(git rev-parse --show-toplevel)"

REPO="${GITHUB_REPO:-}"
if [[ -z "$REPO" && -f .env ]]; then
  REPO="$(sed -n 's/^GITHUB_REPO=//p' .env | tail -1)"
fi
[[ -n "$REPO" && "$REPO" != "your-user/your-repo" ]] || die "set GITHUB_REPO in .env"

case "$BASE" in
  main | master) die "refusing to target '$BASE'; test PRs only go to 'sandbox'" ;;
esac
DEFAULT_BRANCH="$(gh repo view "$REPO" --json defaultBranchRef --jq .defaultBranchRef.name)"
[[ "$BASE" != "$DEFAULT_BRANCH" ]] || die "refusing to target the default branch '$BASE'"

git fetch --quiet origin "$BASE" || die "origin/$BASE not found; create it (see docs/SETUP_GITHUB.md)"

BRANCH="sandbox-pr/$(date +%Y%m%d-%H%M%S)-$RANDOM"
WORKTREE="$(mktemp -d)"
cleanup() {
  git worktree remove --force "$WORKTREE" 2>/dev/null || true
  git branch -D "$BRANCH" >/dev/null 2>&1 || true
}
trap cleanup EXIT

# A separate worktree keeps your current checkout and branch untouched.
git worktree add --quiet -b "$BRANCH" "$WORKTREE" "origin/$BASE"
NOTE="sandbox/${BRANCH#sandbox-pr/}.md"

step() { echo "==> $*"; }

step "commit 1 + push"
mkdir -p "$WORKTREE/sandbox"
echo "Throwaway change for webhook testing ($BRANCH)." >"$WORKTREE/$NOTE"
git -C "$WORKTREE" add "$NOTE"
git -C "$WORKTREE" commit --quiet -m "sandbox: add $NOTE"
git -C "$WORKTREE" push --quiet -u origin "$BRANCH"

step "open PR against $BASE (opened)"
URL="$(gh pr create --repo "$REPO" --base "$BASE" --head "$BRANCH" \
  --title "Sandbox PR $BRANCH" --body "Automated by scripts/sandbox_pr.sh for webhook fixtures.")"
PR="${URL##*/}"
ACTUAL_BASE="$(gh pr view "$PR" --repo "$REPO" --json baseRefName --jq .baseRefName)"
[[ "$ACTUAL_BASE" == "$BASE" ]] || die "PR #$PR has base '$ACTUAL_BASE', expected '$BASE'; close it by hand"
echo "    $URL"
sleep "$PAUSE"

step "commit 2 + push (synchronize)"
echo "Second commit." >>"$WORKTREE/$NOTE"
git -C "$WORKTREE" commit --quiet -am "sandbox: second commit"
git -C "$WORKTREE" push --quiet
sleep "$PAUSE"

step "edit title (edited)"
gh pr edit "$PR" --repo "$REPO" --title "Sandbox PR $BRANCH (edited)" >/dev/null
sleep "$PAUSE"

step "close without merging (closed)"
gh pr close "$PR" --repo "$REPO" >/dev/null
sleep "$PAUSE"

step "reopen (reopened)"
gh pr reopen "$PR" --repo "$REPO" >/dev/null
sleep "$PAUSE"

step "squash-merge and delete branch (closed, merged=true)"
gh pr merge "$PR" --repo "$REPO" --squash --delete-branch

echo "done: $URL"
