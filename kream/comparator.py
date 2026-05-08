"""
kream/comparator.py
마켓플레이스 상품 가격과 Kream 가격을 비교하여 차익 거래 가능 상품을 필터링한다.

필터 조건:
  - trade_count >= MIN_TRADE_COUNT (기본 100)
  - kream_price - marketplace_price >= MIN_PRICE_DIFF (기본 10,000원)
  - Kream 가격이 마켓플레이스 가격보다 비쌀 때만 유효 (마켓플레이스 구매 → Kream 판매)

이 모듈은 순수 동기 함수로만 구성되어 있으며, 비동기 크롤링은 포함하지 않는다.
"""
from datetime import datetime

from config import MIN_PRICE_DIFF, MIN_TRADE_COUNT
from common.logger import get_logger
from common.models import ArbitrageResult, KreamProduct, MarketplaceProduct

logger = get_logger("kream.comparator")


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _effective_price(p: MarketplaceProduct) -> int:
    """sale_price 가 있으면 그것을, 없으면 price 를 반환한다."""
    return p.sale_price if p.sale_price is not None else p.price


def _is_opportunity(marketplace_price: int, kream_price: int, trade_count: int) -> bool:
    """
    단일 상품 쌍이 차익 거래 조건을 만족하는지 판단한다.

    Args:
        marketplace_price:  판매가 (원)
        kream_price:  Kream 즉시구매가 (원)
        trade_count:  Kream 거래 체결 수

    Returns:
        조건 충족 시 True
    """
    price_diff = kream_price - marketplace_price
    return trade_count >= MIN_TRADE_COUNT and price_diff >= MIN_PRICE_DIFF


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

def find_arbitrage(
    marketplace_products: list[MarketplaceProduct],
    kream_products_map: dict[str, list[KreamProduct]],
) -> list[ArbitrageResult]:
    """
    마켓플레이스 상품 목록과 Kream 상품 맵을 비교하여 차익 거래 가능 목록을 반환한다.

    처리 흐름:
    1. 각 MarketplaceProduct에 대해 동일 model_name의 KreamProduct 목록 조회
    2. 모든 (MarketplaceProduct, KreamProduct) 조합에 대해 필터 조건 적용
    3. 조건 통과 시 ArbitrageResult 생성
    4. price_diff 내림차순 정렬 후 반환

    Args:
        marketplace_products:    크롤러가 수집한 전체 상품 목록
        kream_products_map: {model_name: list[KreamProduct]} 형태의 딕셔너리

    Returns:
        차익 거래 가능한 ArbitrageResult 목록 (price_diff 내림차순).
        조건을 만족하는 상품이 없으면 빈 리스트.
    """
    results: list[ArbitrageResult] = []
    checked_at = datetime.now().isoformat(timespec="seconds")

    total_pairs = 0
    passed_pairs = 0

    for marketplace in marketplace_products:
        kream_list = kream_products_map.get(marketplace.model_name, [])
        if not kream_list:
            logger.debug(f"[{marketplace.model_name}] Kream 데이터 없음 — 건너뜀")
            continue

        for kream in kream_list:
            total_pairs += 1
            effective = _effective_price(marketplace)
            price_diff = kream.kream_price - effective

            if not _is_opportunity(effective, kream.kream_price, kream.trade_count):
                logger.debug(
                    f"[{marketplace.model_name}] 필터 탈락 — "
                    f"가격차={price_diff:,}원 (기준: {MIN_PRICE_DIFF:,}), "
                    f"거래량={kream.trade_count} (기준: {MIN_TRADE_COUNT})"
                )
                continue

            passed_pairs += 1
            result = ArbitrageResult(
                model_name=marketplace.model_name,
                marketplace_site=marketplace.site_name,
                marketplace_price=effective,
                kream_price=kream.kream_price,
                price_diff=price_diff,
                trade_count=kream.trade_count,
                marketplace_url=marketplace.url,
                kream_url=kream.kream_url,
                checked_at=checked_at,
            )
            results.append(result)
            logger.info(
                f"[{marketplace.model_name}] 차익 발견 — "
                f"마켓플레이스({marketplace.site_name})={effective:,}원, "
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
