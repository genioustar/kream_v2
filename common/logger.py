import logging
from pathlib import Path
from datetime import datetime


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.DEBUG)

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-8s %(name)s - %(message)s"))

    log_dir = Path(__file__).parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)
    today = datetime.now().strftime("%Y%m%d")
    fh = logging.FileHandler(log_dir / f"crawler_{today}.log", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)-8s %(name)s - %(message)s"))

    logger.addHandler(console)
    logger.addHandler(fh)
    return logger
