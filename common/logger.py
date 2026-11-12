"""로깅 설정은 이 모듈의 setup_logging() 한 곳이다.

엔트리포인트(main, chrome, diff_output)의 __main__ 에서 1회 호출한다.
모듈을 import 해도 핸들러를 붙이거나 logs/ 를 만들지 않는다.
"""
import logging
from datetime import datetime
from pathlib import Path

LOG_FORMAT = "[%(asctime)s] %(levelname)-8s %(name)s (%(filename)s:%(lineno)d) - %(message)s"

# 직접 실행되는 세 이름과 패키지 로거. 여기 없는 로거(asyncio, websockets 등)는 루트 레벨을 따른다.
PROJECT_LOGGERS = (
    "main",
    "chrome",
    "diff_output",
    "common",
    "naver",
    "kream",
    "adidas",
    "musinsa",
    "nike",
    "special",
    "ssfshop",
)

_DEFAULT_LOG_DIR = Path(__file__).parent.parent / "logs"
_CONFIGURED = "_resell_sniper_handler"


def setup_logging(log_dir: Path | None = None) -> None:
    """루트 로거에 콘솔(INFO)·파일(DEBUG) 핸들러를 한 번만 붙인다.

    루트 레벨은 WARNING이라 서드파티 INFO는 걸러지고, 프로젝트 로그만 DEBUG로 파일에 남는다.
    이미 붙인 핸들러가 있으면 레벨만 다시 맞추고 핸들러는 늘리지 않는다.
    """
    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    for name in PROJECT_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)
    if any(getattr(handler, _CONFIGURED, False) for handler in root.handlers):
        return

    formatter = logging.Formatter(LOG_FORMAT)
    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(formatter)

    directory = _DEFAULT_LOG_DIR if log_dir is None else Path(log_dir)
    directory.mkdir(exist_ok=True)
    filename = directory / f"crawler_{datetime.now().strftime('%Y%m%d')}.log"
    file_handler = logging.FileHandler(filename, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    console._resell_sniper_handler = True
    file_handler._resell_sniper_handler = True
    root.addHandler(console)
    root.addHandler(file_handler)
