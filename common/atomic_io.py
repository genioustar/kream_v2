"""JSON 결과를 같은 디렉터리의 임시 파일을 거쳐 원자적으로 저장한다."""
import json
import os
import stat
from pathlib import Path
from tempfile import NamedTemporaryFile

JSON_INDENT = 2
TEMP_SUFFIX = ".tmp"
DEFAULT_FILE_MODE = 0o666


def _current_umask() -> int:
    """현재 프로세스 umask를 읽는다. 조회 API가 없어 설정 후 바로 되돌린다.

    짧게 프로세스 전체 umask를 0으로 바꾸므로 멀티스레드에서 동시 파일 생성 시 주의.
    """
    previous = os.umask(0)
    try:
        return previous
    finally:
        os.umask(previous)


def _result_mode(path: Path) -> int:
    """새 파일은 일반 생성 권한, 이미 있는 파일은 그 권한을 유지한다."""
    if path.is_file():
        return stat.S_IMODE(path.stat().st_mode)
    return DEFAULT_FILE_MODE & ~_current_umask()


def write_json_atomic(path: Path, payload: object) -> None:
    """payload를 UTF-8 JSON으로 path에 원자적으로 쓴다.

    부모 디렉터리는 만들지 않는다. 임시 파일은 0600으로 만들어지므로 replace 전에
    결과 파일 권한으로 맞춘다. 직렬화나 쓰기가 실패하면 임시 파일을 지우고
    예외를 다시 던지며, 기존 파일은 바꾸지 않는다.
    """
    temporary: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            suffix=TEMP_SUFFIX,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(payload, handle, ensure_ascii=False, indent=JSON_INDENT)
        os.chmod(temporary, _result_mode(path))
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
