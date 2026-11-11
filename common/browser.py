"""ego lite 의 Task Space 하나에 연결하고 작업 탭을 1개로 유지한다.

별도 브라우저 프로세스를 띄우지 않는다. 사용자의 ego lite 안에서
이름이 resell-sniper 인 Space 만 쓰고, 그 밖의 탭은 건드리지 않는다.
"""
import asyncio
from contextlib import asynccontextmanager

from common.ego_session import SPACE_NAME, EgoClient, EgoPage
from common.logger import get_logger

logger = get_logger("common.browser")


class _CrawlerTabs:
    def __init__(self, client):
        self._client = client
        self._page = None
        self._page_lock = asyncio.Lock()

    async def new_page(self):
        async with self._page_lock:
            if self._page is not None and not self._page.is_closed():
                raise RuntimeError("크롤러 작업 탭은 하나만 사용할 수 있습니다. 기존 작업 탭을 먼저 닫으세요.")
            page = EgoPage(self._client)
            self._client.page = page
            opened = await self._client.call("open", {"name": SPACE_NAME})
            page.url = opened["url"]
            self._page = page
            logger.info("ego lite Task Space 연결: %s", opened.get("spaceId"))
            return page

    async def close_work_page(self) -> None:
        page = self._page
        self._page = None
        if page is None or page.is_closed():
            return
        try:
            await page.close()
        except Exception as exc:
            logger.warning("작업 탭 종료 실패 (%s)", type(exc).__name__)


@asynccontextmanager
async def create_browser(client=None):
    """사용자의 ego lite 에 Task Space 를 연다. 끝나면 그 Space 만 닫는다."""
    owns_client = client is None
    client = client or EgoClient()
    started = False
    try:
        if owns_client:
            await client.start()
            started = True
        tabs = _CrawlerTabs(client)
        try:
            yield tabs
        finally:
            await tabs.close_work_page()
    finally:
        if owns_client and started:
            await client.finish()
