"""요청 사이 휴식과 차단 시 중단 정책."""
import asyncio
import logging
import random

logger = logging.getLogger(__name__)


class CrawlBlockedError(RuntimeError):
    """자동 재시도하지 않고 현재 실행을 중단해야 하는 응답."""


async def pause(min_seconds: float, max_seconds: float, reason: str) -> None:
    if not 0 <= min_seconds <= max_seconds:
        raise ValueError("대기 시간은 0 <= 최소 <= 최대여야 합니다.")
    seconds = random.uniform(min_seconds, max_seconds)
    logger.info("%s — %.1f초 대기", reason, seconds)
    await asyncio.sleep(seconds)


def check_response(response) -> None:
    if response is not None and response.status in (403, 429):
        raise CrawlBlockedError(
            f"HTTP {response.status} 접근 제한 — 자동 재시도 없이 중단합니다. "
            "사이트의 제한 안내를 확인한 뒤 다시 실행하세요."
        )
