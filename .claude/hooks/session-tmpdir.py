#!/usr/bin/env python3
"""SessionStart 훅: scratchpad 루트를 만들고 Bash 의 TMPDIR 을 그 루트로 지정한다.

실측(2026-11-12): settings.json env.TMPDIR 은 적용되지 않는다(다른 env 변수는 적용됨).
CLAUDE_ENV_FILE 에 `export TMPDIR=<루트>` 를 넣으면 Bash 와 tempfile 만 그 루트를 쓴다.
PreToolUse 훅 프로세스에는 이 파일이 전달되지 않는다.

루트가 없으면 Python tempfile 은 /tmp, BSD mktemp 는 /var/folders 로 조용히 바뀐다.
루트 문자열은 block-tmp-writes.py 의 ALLOWED_ROOTS[0] 만 둔다.
실패해도 세션을 막지 않는다. 예외는 stderr 한 줄 후 exit 0.
"""
import importlib.util
import os
import shlex
import sys
from pathlib import Path

BLOCK_HOOK = Path(__file__).resolve().parent / "block-tmp-writes.py"
EXPORT_PREFIX = "export TMPDIR="


def allowed_root() -> str:
    """block-tmp-writes.py 의 ALLOWED_ROOTS[0] 을 읽는다."""
    spec = importlib.util.spec_from_file_location("block_tmp_writes_root", BLOCK_HOOK)
    if spec is None or spec.loader is None:
        raise RuntimeError("block-tmp-writes.py 를 불러오지 못했습니다.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.ALLOWED_ROOTS[0]


def _export_line(root: str) -> str:
    """export 값과 멱등 비교는 같은 줄을 쓴다."""
    return EXPORT_PREFIX + shlex.quote(root)


def _append_export(path: Path, root: str) -> None:
    """env 파일에 export 줄을 한 번만 추가한다. 쓰기 실패는 stderr 후 삼킨다."""
    line = _export_line(root)
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        if any(item.strip() == line for item in existing.splitlines()):
            return
        with path.open("a", encoding="utf-8") as handle:
            if existing and not existing.endswith("\n"):
                handle.write("\n")
            handle.write(line + "\n")
    except OSError as exc:
        print("session-tmpdir: " + str(exc), file=sys.stderr)


def prepare(root: str, env_file: str | None) -> None:
    """루트를 만들고, env_file 이 있으면 export TMPDIR=<root> 한 줄을 추가한다."""
    Path(root).mkdir(parents=True, exist_ok=True)
    if env_file is None:
        return
    _append_export(Path(env_file), root)


def main() -> int:
    try:
        env_file = os.environ.get("CLAUDE_ENV_FILE") or None
        prepare(allowed_root(), env_file)
    except Exception as exc:
        print("session-tmpdir: " + str(exc), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
