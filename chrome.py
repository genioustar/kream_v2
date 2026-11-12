"""ego lite Task Space 연결을 확인한다.

크롤러는 새 브라우저 창을 만들지 않는다. 이미 설치된 ego lite 안에서
resell-sniper Space 를 열고, 확인이 끝나면 그 Space 만 닫는다.
"""
import asyncio
import logging
import sys

from common.logger import setup_logging

# 직접 실행하면 __name__ 이 "__main__" 이 되어 로거 이름이 "chrome" 이 아니고 DEBUG 목록에서도 빠진다.
logger = logging.getLogger("chrome")


async def check_connection() -> bool:
    """사이트를 열지 않고 webdriver 표시가 켜져 있지 않은지 확인한다."""
    from common.browser import create_browser

    async with create_browser() as tabs:
        page = await tabs.new_page()
        await page.goto("about:blank")
        webdriver = await page.evaluate("() => navigator.webdriver === true")
    logger.info("ego lite 연결 확인 — navigator.webdriver === true 은 %s", webdriver)
    return webdriver is not True


def main() -> int:
    try:
        ok = asyncio.run(check_connection())
    except (RuntimeError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    return 0 if ok else 1


if __name__ == "__main__":
    setup_logging()
    sys.exit(main())
