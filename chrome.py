"""Chrome CDP 디버깅 모드 실행 스크립트 (Windows/macOS 공용)."""
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

DEFAULT_PORT = 9222
PORT_SPAN = 11
PORT_FILE = Path(__file__).resolve().parent / "output" / "session" / "cdp_port"

if sys.platform == "win32":
    CHROME_BIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    CHROME_DIR = r"C:\Temp\chrome-cdp"
else:
    CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    CHROME_DIR = "/tmp/chrome-cdp"


def _devtools_url(port: int) -> str:
    return f"http://127.0.0.1:{port}"


def is_devtools(port: int) -> bool:
    """포트가 Chrome DevTools HTTP 엔드포인트인지 확인한다."""
    try:
        with urllib.request.urlopen(f"{_devtools_url(port)}/json/version", timeout=2) as resp:
            payload = json.loads(resp.read().decode())
        return isinstance(payload, dict) and "webSocketDebuggerUrl" in payload
    except Exception:
        return False


def resolve_cdp_url() -> str:
    """크롤러가 접속할 DevTools 주소.

    9222가 DevTools면 그 주소를 쓴다. 아니면 chrome.py가 기록한 포트를 쓴다.
    """
    if is_devtools(DEFAULT_PORT):
        return _devtools_url(DEFAULT_PORT)
    if PORT_FILE.exists():
        text = PORT_FILE.read_text(encoding="utf-8").strip()
        if text.isdigit() and is_devtools(int(text)):
            return _devtools_url(int(text))
    return _devtools_url(DEFAULT_PORT)


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _choose_port() -> int | None:
    if is_devtools(DEFAULT_PORT):
        return DEFAULT_PORT
    for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SPAN):
        if _port_free(port):
            return port
    return None


def _write_port(port: int) -> None:
    PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORT_FILE.write_text(str(port), encoding="utf-8")


def main() -> int:
    port = _choose_port()
    if port is None:
        last = DEFAULT_PORT + PORT_SPAN - 1
        print(f"Chrome CDP port {DEFAULT_PORT}-{last} is unavailable")
        return 1
    if is_devtools(port):
        _write_port(port)
        print(f"Chrome CDP already running ({_devtools_url(port)})")
        return 0

    Path(CHROME_DIR).mkdir(parents=True, exist_ok=True)
    subprocess.Popen(
        [
            CHROME_BIN,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={CHROME_DIR}",
            "--no-first-run",
        ]
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if is_devtools(port):
            _write_port(port)
            print(f"Chrome CDP ready ({_devtools_url(port)})")
            return 0
        time.sleep(0.5)
    print(f"Chrome CDP not ready ({_devtools_url(port)})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
