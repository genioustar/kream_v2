"""
kream/comparator.py
네이버 상품 가격과 Kream 가격을 비교하여 차익 거래 가능 상품을 필터링한다.

필터 조건:
  - trade_count >= MIN_TRADE_COUNT (기본 100)
  - kream_price - naver_price >= MIN_PRICE_DIFF (기본 10,000원)
  - Kream 가격이 네이버 가격보다 비쌀 때만 유효 (네이버 구매 → Kream 판매)

이 모듈은 순수 동기 함수로만 구성되어 있으며, 비동기 크롤링은 포함하지 않는다.
"""
from datetime import datetime
from typing import Optional

from config import MIN_PRICE_DIFF, MIN_TRADE_COUNT
from common.logger import get_logger
from common.models import ArbitrageResult, KreamProduct, NaverProduct

logger = get_logger("kream.comparator")


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _is_opportunity(naver_price: int, kream_price: int, trade_count: int) -> bool:
    """
    단일 상품 쌍이 차익 거래 조건을 만족하는지 판단한다.

    Args:
        naver_price:  네이버 판매가 (원)
        kream_price:  Kream 즉시구매가 (원)
        trade_count:  Kream 거래 체결 수

    Returns:
        조건 충족 시 True
    """
    price_diff = kream_price - naver_price
    return trade_count >= MIN_TRADE_COUNT and price_diff >= MIN_PRICE_DIFF


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def find_arbitrage(
    naver_products: list[NaverProduct],
    kream_products_map: dict[str, list[KreamProduct]],
) -> list[ArbitrageResult]:
    """
    네이버 상품 목록과 Kream 상품 맵을 비교하여 차익 거래 가능 목록을 반환한다.

    처리 흐름:
    1. 각 NaverProduct에 대해 동일 model_name의 KreamProduct 목록 조회
    2. 모든 (NaverProduct, KreamProduct) 조합에 대해 필터 조건 적용
    3. 조건 통과 시 ArbitrageResult 생성
    4. price_diff 내림차순 정렬 후 반환

    Args:
        naver_products:    네이버 크롤러가 수집한 전체 상품 목록
        kream_products_map: {model_name: list[KreamProduct]} 형태의 딕셔너리

    Returns:
        차익 거래 가능한 ArbitrageResult 목록 (price_diff 내림차순).
        조건을 만족하는 상품이 없으면 빈 리스트.
    """
    results: list[ArbitrageResult] = []
    checked_at = datetime.now().isoformat(timespec="seconds")

    total_pairs = 0
    passed_pairs = 0

    for naver in naver_products:
        kream_list = kream_products_map.get(naver.model_name, [])
        if not kream_list:
            logger.debug(f"[{naver.model_name}] Kream 데이터 없음 — 건너뜀")
            continue

        for kream in kream_list:
            total_pairs += 1
            price_diff = kream.kream_price - naver.price

            if not _is_opportunity(naver.price, kream.kream_price, kream.trade_count):
                logger.debug(
                    f"[{naver.model_name}] 필터 탈락 — "
                    f"가격차={price_diff:,}원 (기준: {MIN_PRICE_DIFF:,}), "
                    f"거래량={kream.trade_count} (기준: {MIN_TRADE_COUNT})"
                )
                continue

            passed_pairs += 1
            result = ArbitrageResult(
                model_name=naver.model_name,
                naver_site=naver.site_name,
                naver_price=naver.price,
                kream_price=kream.kream_price,
                price_diff=price_diff,
                trade_count=kream.trade_count,
                naver_url=naver.url,
                kream_url=kream.kream_url,
                checked_at=checked_at,
            )
            results.append(result)
            logger.info(
                f"[{naver.model_name}] 차익 발견 — "
                f"네이버({naver.site_name})={naver.price:,}원, "
                f"Kream={kream.kream_price:,}원, "
                f"차익={price_diff:,}원, 거래량={kream.trade_count}"
            )

    # price_diff 내림차순 정렬 (수익성 높은 순)
    results.sort(key=lambda r: r.price_diff, reverse=True)

    logger.info(
        f"비교 완료 — 전체 조합: {total_pairs}개, "
        f"차익 가능: {passed_pairs}개 "
        f"(거래량 기준: >={MIN_TRADE_COUNT}, 가격차 기준: >={MIN_PRICE_DIFF:,}원)"
    )

    if not results:
        logger.info("차익 거래 가능한 상품이 없습니다.")

    return results
