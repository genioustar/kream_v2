import json
import random
import re
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

_stealth = Stealth()

# 세션 쿠키 저장 경로
COOKIE_FILE = Path(__file__).parent.parent / "output" / "session" / "kream_cookies.json"

# 랜덤 User-Agent 풀 — Chrome 130~136 최신 버전, Mac/Windows/Linux 플랫폼 포함
USER_AGENTS = [
    # Mac — Chrome 130~136
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4_1) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
    # Windows — Chrome 130~136
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/132.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    # Linux — Chrome 131~136
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
]


# ---------------------------------------------------------------------------
# B) HTTP 핑거프린트 강화 — sec-ch-ua 헤더 생성
# ---------------------------------------------------------------------------

def _get_client_hints(ua: str) -> dict[str, str]:
    """
    User-Agent 문자열에서 Chrome Client Hints 헤더 값을 생성한다.
    UA와 일치하지 않는 sec-ch-ua 헤더는 오히려 봇 신호가 되므로 반드시 일치시킨다.
    """
    version_match = re.search(r"Chrome/(\d+)", ua)
    version = version_match.group(1) if version_match else "136"

    if "Macintosh" in ua or "Mac OS" in ua:
        platform = '"macOS"'
    elif "Windows" in ua:
        platform = '"Windows"'
    else:
        platform = '"Linux"'

    return {
        "sec-ch-ua": (
            f'"Not A(Brand";v="8", '
            f'"Chromium";v="{version}", '
            f'"Google Chrome";v="{version}"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": platform,
    }


# ---------------------------------------------------------------------------
# A) 쿠키 영속성
# ---------------------------------------------------------------------------

async def _load_cookies(context) -> None:
    """저장된 세션 쿠키를 컨텍스트에 로드한다. 파일이 없거나 오류 시 무시한다."""
    if not COOKIE_FILE.exists():
        return
    try:
        cookies = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
        if cookies:
            await context.add_cookies(cookies)
    except Exception:
        pass  # 손상된 쿠키 파일은 무시하고 새 세션으로 시작


async def _save_cookies(context) -> None:
    """컨텍스트 세션 쿠키를 파일에 저장한다. 오류 시 무시한다."""
    try:
        cookies = await context.cookies()
        if cookies:
            COOKIE_FILE.parent.mkdir(parents=True, exist_ok=True)
            COOKIE_FILE.write_text(
                json.dumps(cookies, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# 브라우저 컨텍스트 팩토리
# ---------------------------------------------------------------------------

@asynccontextmanager
async def create_browser(headless: bool = False):
    """
    Playwright Chromium 브라우저를 생성하고 stealth 컨텍스트를 반환한다.

    변경 사항:
    - A) 시작 시 쿠키 로드, 종료 시 쿠키 저장 (세션 영속성)
    - A) timezone_id="Asia/Seoul" 추가 (시간대 일치)
    - B) UA와 일치하는 sec-ch-ua 헤더 자동 생성 (HTTP 핑거프린트 강화)
    - UA를 컨텍스트 생성 시 1회만 선택 (페이지마다 변경하지 않음 → 일관성)
    """
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=headless,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--window-size=1280,720",
            "--start-maximized",
            "--disable-extensions",
            "--disable-plugins-discovery",
            "--no-first-run",
            "--no-service-autorun",
            "--password-store=basic",
        ],
    )

    # UA 1회 선택 — 컨텍스트 전체에서 동일한 UA 사용 (일관성)
    ua = random.choice(USER_AGENTS)
    client_hints = _get_client_hints(ua)

    context = await browser.new_context(
        viewport={"width": 1280, "height": 720},
        locale="ko-KR",
        timezone_id="Asia/Seoul",       # A) 시간대 추가
        user_agent=ua,
        extra_http_headers={
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
            "Accept-Encoding": "gzip, deflate, br",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
            **client_hints,             # B) sec-ch-ua 헤더 추가
        },
    )

    await _load_cookies(context)        # A) 저장된 쿠키 로드
    try:
        yield context
    finally:
        await _save_cookies(context)    # A) 세션 쿠키 저장
        await context.close()
        await browser.close()
        await pw.stop()


async def new_stealth_page(context):
    """
    stealth가 적용된 새 페이지를 반환한다.

    UA는 컨텍스트 레벨에서 이미 설정되어 있으므로 페이지별 재설정을 제거한다.
    (페이지마다 다른 UA → HTTP 요청과 JS navigator.userAgent 불일치 → 봇 신호)
    """
    page = await context.new_page()
    await _stealth.apply_stealth_async(page)
    # 자동화 탐지 프로퍼티 제거
    await page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
        });
        Object.defineProperty(navigator, 'plugins', {
            get: () => [1, 2, 3, 4, 5],
        });
        Object.defineProperty(navigator, 'languages', {
            get: () => ['ko-KR', 'ko', 'en-US', 'en'],
        });
        window.chrome = {
            runtime: {},
        };
    """)
    return page
