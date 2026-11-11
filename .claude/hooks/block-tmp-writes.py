#!/usr/bin/env python3
"""
PreToolUse 훅: 이 프로젝트의 scratchpad 루트 밖 임시 경로(/tmp, /private/tmp)에 쓰는 도구 호출을 막는다.

Claude Code 와 Grok CLI(Orca worker) 가 같은 .claude/settings.local.json 훅을 읽으므로
두 도구의 입력 형식(file_path / path / target_notebook 등)을 모두 처리한다.
읽기(ls, cat 등)와 정리용 rm 은 막지 않는다. 훅 자체 오류는 통과(fail open)시킨다.
"""
import json
import re
import sys

# /tmp 는 macOS 에서 /private/tmp 의 링크라 둘 다 같은 루트로 본다.
ALLOWED_ROOTS = (
    "/private/tmp/claude-501/-Users-ny-Documents-programs-resell-sniper/",
    "/tmp/claude-501/-Users-ny-Documents-programs-resell-sniper/",
)
TMP_PATH_RE = re.compile(r"(?:/private)?/tmp(?:/[^\s'\"|;&<>()`]*)?")
FILE_PATH_KEYS = ("file_path", "path", "target_file", "notebook_path", "target_notebook")
# 파일 경로를 검사하는 쓰기 도구. Claude 이름과 Grok/Cursor 이름을 함께 둔다. 읽기 도구는 검사하지 않는다.
FILE_WRITE_TOOLS = {
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "search_replace", "hashline_edit", "write", "StrReplace", "EditNotebook",
}
# 인자로 받은 경로 모두에 쓰는 명령.
WRITE_ALL_ARGS_COMMANDS = {"tee", "touch", "mkdir"}
# 마지막 인자(목적지)에만 쓰는 명령. 임시 경로에서 scratchpad 로 가져오는 복사는 허용한다.
WRITE_LAST_ARG_COMMANDS = {"cp", "mv", "install", "rsync", "ln"}
SEGMENT_SPLIT_RE = re.compile(r"\|\||&&|[;|\n]")
# heredoc 본문은 명령이 아니라 데이터다. 본문에 적힌 예시 명령을 쓰기로 오인하지 않게 지운다.
HEREDOC_BODY_RE = re.compile(r"<<-?\s*(['\"]?)(\w+)\1[^\n]*\n.*?\n\s*\2\s*(?=\n|$)", re.DOTALL)
ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
DENY_REASON = (
    "임시 파일은 scratchpad({root}) 안에만 쓴다. 차단된 경로: {path}. "
    "/tmp 나 다른 임시 경로에 쓰지 말고 scratchpad 경로로 바꿔라."
)


def _is_allowed(path: str) -> bool:
    candidate = path if path.endswith("/") else path + "/"
    return candidate.startswith(ALLOWED_ROOTS)


def _is_tmp(path: str) -> bool:
    return path == "/tmp" or path.startswith(("/tmp/", "/private/tmp"))


def _blocked_file_path(tool_input: dict) -> str | None:
    """파일 쓰기 도구의 대상 경로가 scratchpad 밖 임시 경로면 그 경로를 돌려준다."""
    for key in FILE_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and _is_tmp(value) and not _is_allowed(value):
            return value
    return None


def _command_words(segment: str) -> list[str]:
    """환경 변수 대입과 sudo/command/env 접두를 뺀 단어 목록. 첫 단어는 실행 파일 이름만 남긴다."""
    words = [word.strip("'\"") for word in segment.strip().split() if not ENV_ASSIGN_RE.match(word)]
    while words and words[0] in ("sudo", "command", "env"):
        words = words[1:]
    if words:
        words[0] = words[0].rsplit("/", 1)[-1]
    return words


def _writes_to(segment: str, path: str) -> bool:
    """명령 한 조각이 그 임시 경로에 쓰는지 본다. 읽기와 rm 은 쓰기로 보지 않는다."""
    escaped = re.escape(path)
    if re.search(r">{1,2}\s*" + escaped, segment):
        return True
    if re.search(r"(?:^|\s)(?:-o|--output)(?:=|\s+)" + escaped, segment):
        return True
    if re.search(r"open\(\s*['\"]" + escaped + r"['\"]\s*,\s*['\"][wax]", segment):
        return True
    if re.search(r"(?:^|\s)of=" + escaped, segment):
        return True
    words = _command_words(segment)
    if not words:
        return False
    if words[0] in WRITE_ALL_ARGS_COMMANDS:
        return path in words[1:]
    if words[0] in WRITE_LAST_ARG_COMMANDS:
        args = [word for word in words[1:] if not word.startswith("-")]
        return bool(args) and args[-1] == path
    return False


def _strip_heredoc_bodies(command: str) -> str:
    """`cat > 파일 <<EOF` 의 첫 줄(쓰기 대상)은 남기고 본문만 지운다."""
    return HEREDOC_BODY_RE.sub(lambda match: match.group(0).split("\n", 1)[0], command)


def _blocked_command_path(command: str) -> str | None:
    for segment in SEGMENT_SPLIT_RE.split(_strip_heredoc_bodies(command)):
        for path in TMP_PATH_RE.findall(segment):
            if not _is_allowed(path) and _writes_to(segment, path):
                return path
    return None


def _deny(path: str) -> None:
    reason = DENY_REASON.format(root=ALLOWED_ROOTS[0], path=path)
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))


def main() -> int:
    try:
        payload = json.loads(sys.stdin.read() or "{}")
    except json.JSONDecodeError:
        return 0
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return 0
    blocked = _blocked_file_path(tool_input) if tool_name in FILE_WRITE_TOOLS else None
    command = tool_input.get("command")
    if blocked is None and isinstance(command, str):
        blocked = _blocked_command_path(command)
    if blocked is not None:
        _deny(blocked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
