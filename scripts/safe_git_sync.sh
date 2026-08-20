#!/usr/bin/env bash
set -euo pipefail

# safe_git_sync.sh
#
# A strictly constrained script to handle Git branching, staging, committing, and pushing.
# Used by Antigravity Workflows to securely mutate Git state without raw shell access.

# Helper: Write JSON output and exit
exit_json() {
  local ok="$1"
  local error_code="$2"
  local message="$3"

  cat <<EOF
{
  "ok": $ok,
  "error_code": "$error_code",
  "message": "$message"
}
EOF
  if [ "$ok" = "false" ]; then
    exit 1
  else
    exit 0
  fi
}

# Trap to catch unhandled errors
trap 'exit_json false "UNHANDLED_ERROR" "An unexpected error occurred during execution."' ERR

# Arguments
TASK_SLUG=""
MANIFEST_FILE=""
TASK_TITLE=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --task-slug)
      TASK_SLUG="$2"
      shift 2
      ;;
    --manifest)
      MANIFEST_FILE="$2"
      shift 2
      ;;
    --task-title)
      TASK_TITLE="$2"
      shift 2
      ;;
    *)
      exit_json false "INVALID_ARGUMENT" "Unknown argument: $1"
      ;;
  esac
done

if [ -z "$TASK_SLUG" ] || [ -z "$MANIFEST_FILE" ] || [ -z "$TASK_TITLE" ]; then
  exit_json false "MISSING_ARGUMENTS" "Usage: safe_git_sync.sh --task-slug <slug> --manifest <file> --task-title <title>"
fi

# 1. Detect repo root
if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  exit_json false "NOT_A_GIT_REPO" "Must be run inside a Git repository."
fi

# 2. Detect current branch
CURRENT_BRANCH=$(git symbolic-ref --quiet --short HEAD || echo "DETACHED")

if [ "$CURRENT_BRANCH" = "main" ] || [ "$CURRENT_BRANCH" = "master" ]; then
  exit_json false "PROTECTED_BRANCH" "Cannot commit or push directly to main/master."
fi

# 3. Branching Logic
TARGET_BRANCH="ai/$TASK_SLUG"

if [ "$CURRENT_BRANCH" = "DETACHED" ] || [ "$CURRENT_BRANCH" != "$TARGET_BRANCH" ]; then
  # Try to checkout existing or create new branch
  if git show-ref --verify --quiet "refs/heads/$TARGET_BRANCH"; then
    git checkout "$TARGET_BRANCH" >/dev/null 2>&1
  else
    git checkout -b "$TARGET_BRANCH" >/dev/null 2>&1
  fi
  CURRENT_BRANCH="$TARGET_BRANCH"
fi

# 4. Staging
if [ ! -f "$MANIFEST_FILE" ]; then
  exit_json false "MISSING_MANIFEST" "Manifest file $MANIFEST_FILE not found."
fi

# Unstage everything first to ensure clean state
git reset >/dev/null 2>&1

# Read paths from manifest and stage them (ignore empty lines)
while IFS= read -r file; do
  if [ -n "$file" ] && [ -e "$file" ]; then
    git add "$file"
  fi
done < "$MANIFEST_FILE"

# Verify we staged something
if git diff --cached --quiet; then
  exit_json false "EMPTY_DIFF" "No changes found in the specified manifest paths."
fi

# 5. Commit
git commit -m "[AI] $TASK_TITLE" >/dev/null

# 6. Push
if ! git push -u origin "$CURRENT_BRANCH" >/dev/null 2>&1; then
  exit_json false "PUSH_FAILED" "Failed to push $CURRENT_BRANCH to origin."
fi

COMMIT_SHA=$(git rev-parse HEAD)

cat <<EOF
{
  "ok": true,
  "stage": "push",
  "branch": "$CURRENT_BRANCH",
  "commit_sha": "$COMMIT_SHA",
  "remote": "origin"
}
EOF
exit 0
