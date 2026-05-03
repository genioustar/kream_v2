"""Chrome CDP 디버깅 모드 실행 스크립트 (Windows/macOS 공용)"""
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

if sys.platform == "win32":
    CHROME_BIN = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    CHROME_DIR = r"C:\Temp\chrome-cdp"
else:
    CHROME_BIN = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    CHROME_DIR = "/tmp/chrome-cdp"


def is_cdp_running() -> bool:
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2)
        return True
    except Exception:
        return False


if __name__ == "__main__":
    if is_cdp_running():
        print("Chrome CDP already running (port 9222)")
        sys.exit(0)

    Path(CHROME_DIR).mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        CHROME_BIN,
        "--remote-debugging-port=9222",
        f"--user-data-dir={CHROME_DIR}",
        "--no-first-run",
    ])
    time.sleep(3)
    if is_cdp_running():
        print("Chrome CDP ready (port 9222)")
    else:
        print("Chrome CDP not ready yet — retry in a moment")
