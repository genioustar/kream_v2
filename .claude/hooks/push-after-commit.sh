#!/bin/bash
# git commit 직후 현재 브랜치를 upstream 으로 push 한다.
# upstream 이 없는 브랜치(예: Orca 임시 worktree 브랜치)는 건드리지 않는다.
payload=$(cat)
work_dir=$(printf '%s' "$payload" | jq -r '.cwd // empty')
[ -n "$work_dir" ] && cd "$work_dir" || exit 0

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || exit 0
upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) || {
  printf '{"systemMessage":"auto-push 건너뜀: upstream 이 없는 브랜치"}\n'
  exit 0
}

if output=$(git push 2>&1); then
  printf '{"systemMessage":"auto-push 완료 → %s"}\n' "$upstream"
else
  # push 실패는 커밋을 되돌리지 않는다. 사용자에게 알리기만 한다.
  jq -n --arg u "$upstream" --arg o "$output" \
    '{systemMessage:("auto-push 실패 ("+$u+")"),hookSpecificOutput:{hookEventName:"PostToolUse",additionalContext:("git push 실패: "+$o)}}'
fi
exit 0
