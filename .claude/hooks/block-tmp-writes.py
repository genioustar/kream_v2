#!/usr/bin/env python3
"""
PreToolUse 훅: scratchpad 루트 밖 임시 경로에 쓰는 도구 호출을 막는다.

대상은 /tmp·/private/tmp, macOS 시스템 임시 경로(/var/folders·/private/var/folders),
그리고 명령 문자열의 $TMPDIR·${TMPDIR} 이다.

실측(2026-11-12, headless claude -p 새 세션): settings.json 의 env.TMPDIR 은
Bash·Python·이 훅에 적용되지 않는다(같은 env 의 다른 변수는 적용됨).
SessionStart 가 CLAUDE_ENV_FILE 에 export TMPDIR=<루트> 를 넣으면 Bash 와
tempfile.gettempdir() 만 그 루트를 쓴다. PreToolUse 훅 프로세스에는
CLAUDE_ENV_FILE 이 없고 TMPDIR 은 /var/folders 다.
그래서 snake_case 키만 있는 Claude payload 의 $TMPDIR 쓰기는 허용하고,
camelCase 가 하나라도 있거나 Grok/Cursor payload 는 이 프로세스의 TMPDIR 이
ALLOWED_ROOTS 안일 때만 허용한다. 리터럴 /tmp·/var/folders 와 파일 경로는
키 형식과 무관하게 양쪽 키에서 값을 읽는다.

Claude Code 와 Grok CLI(Orca worker) 가 같은 .claude/settings.json 훅을 읽으므로
두 도구의 입력 형식(file_path / path / target_notebook 등)을 모두 처리한다.
읽기(ls, cat 등)와 정리용 rm 은 막지 않는다. 훅 자체 오류는 통과(fail open)시킨다.
"""
import json
import os
import re
import sys
from collections.abc import Mapping

# /tmp 는 macOS 에서 /private/tmp 의 링크라 둘 다 같은 루트로 본다.
ALLOWED_ROOTS = (
    "/private/tmp/claude-501/-Users-ny-Documents-programs-resell-sniper/",
    "/tmp/claude-501/-Users-ny-Documents-programs-resell-sniper/",
)
# 호출자 구분. Claude Code 는 snake_case 만, Grok/Cursor 는 camelCase.
CLAUDE_TOOL_NAME_KEY = "tool_name"
CLAUDE_TOOL_INPUT_KEY = "tool_input"
CLAUDE_PAYLOAD_KEYS = (CLAUDE_TOOL_NAME_KEY, CLAUDE_TOOL_INPUT_KEY)
GROK_TOOL_NAME_KEY = "toolName"
GROK_TOOL_INPUT_KEY = "toolInput"
GROK_TOOL_INPUT_TRUNCATED_KEY = "toolInputTruncated"
GROK_PAYLOAD_KEYS = (GROK_TOOL_NAME_KEY, GROK_TOOL_INPUT_KEY, GROK_TOOL_INPUT_TRUNCATED_KEY)
CALLER_CLAUDE = "claude"
CALLER_GROK = "grok"
# 경로 토큰에서 공백·따옴표·셸 구분자 앞까지만 취한다. heredoc 처리 후 세그먼트에 쓴다.
PATH_TOKEN_TAIL = r"[^\s'\"|;&<>()`]*"
TMP_PATH_RE = re.compile(rf"(?:/private)?/tmp(?:/{PATH_TOKEN_TAIL})?")
VAR_FOLDERS_PATH_RE = re.compile(rf"(?:/private)?/var/folders(?:/{PATH_TOKEN_TAIL})?")
# $TMPDIR 뒤에 식별자가 더 붙으면($TMPDIR_FOO) 다른 변수이므로 제외한다.
TMPDIR_REF_RE = re.compile(rf"\$(?:\{{TMPDIR\}}|TMPDIR)(?![A-Za-z0-9_])(?:/{PATH_TOKEN_TAIL})?")
TMP_EXACT = "/tmp"
TMP_PREFIXES = ("/tmp/", "/private/tmp")
VAR_FOLDERS_EXACT = "/var/folders"
VAR_FOLDERS_PREFIXES = ("/var/folders/", "/private/var/folders")
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
    "/tmp, /var/folders 나 다른 임시 경로에 쓰지 말고 scratchpad 경로로 바꿔라."
)


def _is_allowed(path: str) -> bool:
    candidate = path if path.endswith("/") else path + "/"
    return candidate.startswith(ALLOWED_ROOTS)


def _is_tmp(path: str) -> bool:
    return path == TMP_EXACT or path.startswith(TMP_PREFIXES)


def _is_var_folders(path: str) -> bool:
    return path == VAR_FOLDERS_EXACT or path.startswith(VAR_FOLDERS_PREFIXES)


def _is_blocked_temp(path: str) -> bool:
    return _is_tmp(path) or _is_var_folders(path)


def _tmpdir_is_inside_allowed_roots(environ: Mapping[str, str]) -> bool:
    """훅이 물려받은 TMPDIR 이 scratchpad 루트 안인지 본다."""
    tmpdir = environ.get("TMPDIR")
    return isinstance(tmpdir, str) and bool(tmpdir) and _is_allowed(tmpdir)


def caller_kind(payload: Mapping) -> str:
    """snake_case 만 있으면 Claude. camelCase 가 있거나 둘 다 없으면 Grok.

    값 유무가 아니라 키 존재로 본다. None 이어도 키가 있으면 있는 것이다.
    혼합 payload 는 Grok 으로 보아 $TMPDIR 허용을 주지 않는다.
    """
    has_snake = any(key in payload for key in CLAUDE_PAYLOAD_KEYS)
    has_camel = any(key in payload for key in GROK_PAYLOAD_KEYS)
    if has_snake and not has_camel:
        return CALLER_CLAUDE
    return CALLER_GROK


def tmpdir_write_allowed(caller: str, environ: Mapping[str, str]) -> bool:
    """$TMPDIR·${TMPDIR} 쓰기 허용 여부.

    Claude 는 SessionStart 가 Bash 의 TMPDIR 을 루트로 두므로 훅 환경과 무관하게 허용한다.
    Grok/Cursor 는 SessionStart·CLAUDE_ENV_FILE 적용이 미확인이라, 훅 환경의 TMPDIR 이
    ALLOWED_ROOTS 안일 때만 허용한다. 리터럴 경로 판정은 이 함수를 쓰지 않는다.
    """
    if caller == CALLER_CLAUDE:
        return True
    return _tmpdir_is_inside_allowed_roots(environ)


def _blocked_file_path(tool_input: dict) -> str | None:
    """파일 쓰기 도구의 대상 경로가 scratchpad 밖 임시 경로면 그 경로를 돌려준다."""
    for key in FILE_PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and _is_blocked_temp(value) and not _is_allowed(value):
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


def _blocked_command_path(command: str, environ: Mapping[str, str], caller: str) -> str | None:
    """셸 쓰기 대상이 scratchpad 밖 임시 경로거나, 이 호출자에게 허용되지 않은 $TMPDIR 이면 그 토큰을 돌려준다."""
    tmpdir_allowed = tmpdir_write_allowed(caller, environ)
    for segment in SEGMENT_SPLIT_RE.split(_strip_heredoc_bodies(command)):
        for path in TMP_PATH_RE.findall(segment):
            if not _is_allowed(path) and _writes_to(segment, path):
                return path
        for path in VAR_FOLDERS_PATH_RE.findall(segment):
            if not _is_allowed(path) and _writes_to(segment, path):
                return path
        if tmpdir_allowed:
            continue
        for path in TMPDIR_REF_RE.findall(segment):
            if _writes_to(segment, path):
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
    if not isinstance(payload, dict):
        return 0
    # 리터럴·파일 경로는 호출자와 무관하다. 어느 쪽 키에 값이 있든 읽는다.
    tool_name = payload.get("tool_name") or payload.get("toolName") or ""
    tool_input = payload.get("tool_input") or payload.get("toolInput") or {}
    if not isinstance(tool_input, dict):
        return 0
    caller = caller_kind(payload)
    blocked = _blocked_file_path(tool_input) if tool_name in FILE_WRITE_TOOLS else None
    command = tool_input.get("command")
    if blocked is None and isinstance(command, str):
        blocked = _blocked_command_path(command, os.environ, caller)
    if blocked is not None:
        _deny(blocked)
    return 0


if __name__ == "__main__":
    sys.exit(main())
