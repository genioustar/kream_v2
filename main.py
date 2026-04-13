"""
main.py
네이버 → Kream 차익 거래 탐색 파이프라인 진입점.

실행 모드 (--mode):
  crawl  STEP 1만 실행 — 전체 사이트 크롤링 후 저장, Kream 검색 없음
  full   STEP 1~4 전체 실행 (기본값)
  kream  STEP 2~4만 실행 — 오늘자 *_products.json 로드 후 Kream 검색

실행 예시:
  python main.py                   # full (기본)
  python main.py --mode crawl
  python main.py --mode kream
"""
import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

import config
from common.browser import create_browser
from common.logger import get_logger
from common.models import NaverProduct
from adidas.crawler import crawl_adidas
from diff_output import diff_date_pair, discover_dates
from kream.comparator import find_arbitrage
from kream.crawler import DELAY_MAX_SEC, DELAY_MIN_SEC, init_kream_page, search_kream, _human_wait
from naver.crawler import crawl_naver

# 네이버 전체 상품을 Kream에 조회하는 날짜 (그 외 날짜는 diff 기반으로 조회)
FULL_CRAWL_DAYS: frozenset[int] = frozenset({1, 10, 20, 30})

logger = get_logger("main")


# ---------------------------------------------------------------------------
# 직렬화 / 로드 헬퍼
# ---------------------------------------------------------------------------

def _save_json(data: list, path: Path) -> None:
    """데이터클래스 리스트를 JSON 파일로 저장한다."""
    path.write_text(
        json.dumps([asdict(item) for item in data], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_products_json(path: Path) -> list[NaverProduct]:
    """*_products.json 파일을 NaverProduct 리스트로 복원한다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [NaverProduct(**item) for item in data]


def _load_all_products(output_dir: Path) -> list[NaverProduct]:
    """output_dir 내 모든 *_products.json 을 로드하여 합산 반환한다."""
    all_products: list[NaverProduct] = []
    for fpath in sorted(output_dir.glob("*_products.json")):
        try:
            items = _load_products_json(fpath)
            logger.info(f"{fpath.name} 로드 — {len(items)}개")
            all_products.extend(items)
        except Exception as exc:
            logger.warning(f"{fpath.name} 로드 실패 (건너뜀): {exc}")
    return all_products


def _extract_models_from_diff(diff_path: Path) -> list[str]:
    """diff 파일의 added/modified 항목에서 고유 model_name 목록을 순서대로 반환한다."""
    data = json.loads(diff_path.read_text(encoding="utf-8"))
    seen: set[str] = set()
    models: list[str] = []
    for change in data.get("changes", []):
        new_item = change.get("new_item") or {}
        mn = new_item.get("model_name", "").strip()
        if mn and mn not in seen:
            seen.add(mn)
            models.append(mn)
    return models


# ---------------------------------------------------------------------------
# Kream 페이지 풀 래퍼
# ---------------------------------------------------------------------------

async def _search_with_page_pool(
    model_name: str,
    page_pool: asyncio.Queue,
) -> tuple[str, list]:
    """
    페이지 풀에서 페이지를 꺼내 검색하고 반환한다.
    풀 크기(KREAM_MAX_CONCURRENCY)가 동시 요청 수를 자연스럽게 제한한다.
    """
    page = await page_pool.get()
    try:
        result = await search_kream(model_name, page)
        await _human_wait(DELAY_MIN_SEC, DELAY_MAX_SEC)
        return model_name, result
    finally:
        await page_pool.put(page)


# ---------------------------------------------------------------------------
# 메인 파이프라인
# ---------------------------------------------------------------------------

async def main(mode: str) -> None:
    today = date.today().strftime("%Y%m%d")
    output_dir = Path(__file__).parent / config.OUTPUT_DIR / today
    output_dir.mkdir(parents=True, exist_ok=True)
    base_output_dir = Path(__file__).parent / config.OUTPUT_DIR

    logger.info("=" * 60)
    logger.info(f"실행 모드: {mode.upper()}")
    logger.info("=" * 60)

    # =========================================================================
    # STEP 1: 전체 사이트 크롤링 (crawl / full 모드)
    # =========================================================================
    if mode in ("crawl", "full"):
        logger.info("=" * 60)
        logger.info("STEP 1: 사이트 크롤링")
        logger.info("=" * 60)

        # 네이버 크롤링
        naver_output = output_dir / "naver_products.json"
        if naver_output.exists():
            logger.info(f"오늘자 파일 존재 — 네이버 크롤링 스킵: {naver_output}")
        else:
            try:
                naver_products = await crawl_naver()
                _save_json(naver_products, naver_output)
                logger.info(f"네이버 상품 {len(naver_products)}개 저장 → {naver_output}")
            except RuntimeError as exc:
                logger.error(f"네이버 크롤링 전체 실패: {exc}")
                sys.exit(1)
            except Exception as exc:
                logger.error(f"naver_products.json 저장 실패: {exc}")
                raise

        # 아디다스 크롤링
        adidas_output = output_dir / "adidas_products.json"
        if adidas_output.exists():
            logger.info(f"오늘자 파일 존재 — 아디다스 크롤링 스킵: {adidas_output}")
        else:
            logger.info("아디다스 Extra Sale 크롤링 시작")
            try:
                adidas_products = await crawl_adidas()
                if adidas_products:
                    _save_json(adidas_products, adidas_output)
                    logger.info(f"아디다스 상품 {len(adidas_products)}개 저장 → {adidas_output}")
                else:
                    logger.warning("아디다스 수집 결과 없음 — 파일 저장 생략 (다음 실행 시 재시도)")
            except Exception as exc:
                logger.warning(f"아디다스 크롤링 실패 (파이프라인은 계속): {exc}")

        if mode == "crawl":
            all_products = _load_all_products(output_dir)
            logger.info("=" * 60)
            logger.info(f"crawl 모드 완료 — 전체 수집: {len(all_products)}개")
            logger.info("=" * 60)
            return

    # =========================================================================
    # STEP 2: Kream 검색 대상 로드 및 모델명 결정 (full / kream 모드)
    # =========================================================================
    logger.info("=" * 60)
    logger.info(f"STEP 2: Kream 가격 비교 시작 (페이지 풀 {config.KREAM_MAX_CONCURRENCY}개)")
    logger.info("=" * 60)

    # 오늘자 *_products.json 전체 로드
    all_products = _load_all_products(output_dir)
    if not all_products:
        logger.error("*_products.json 파일이 없습니다. 먼저 --mode crawl 을 실행하세요.")
        sys.exit(1)

    day = int(today[6:8])
    is_full_crawl_day = day in FULL_CRAWL_DAYS

    seen: set[str] = set()
    unique_models: list[str] = []

    if is_full_crawl_day:
        # 전체 크롤 날짜: 모든 소스의 전체 모델명 사용
        for p in all_products:
            mn = p.model_name.strip()
            if mn and mn not in seen:
                seen.add(mn)
                unique_models.append(mn)
        logger.info(f"전체 크롤 날짜({day}일) — 고유 모델명 {len(unique_models)}개")
    else:
        # 그 외 날짜 (kream 모드 포함): diff가 있으면 diff 기반, 없으면 전체 모델명 사용
        all_dates = discover_dates(base_output_dir)
        if len(all_dates) >= 2:
            logger.info(f"diff 기반 — diff 생성: {all_dates[-2]} → {all_dates[-1]}")
            diff_files = diff_date_pair(all_dates[-2], all_dates[-1], base_output_dir)
            for diff_file in diff_files:
                for mn in _extract_models_from_diff(diff_file):
                    if mn not in seen:
                        seen.add(mn)
                        unique_models.append(mn)
            if unique_models:
                logger.info(f"diff 기반 — 변경된 모델명 {len(unique_models)}개")
            else:
                logger.warning("diff에 변경 항목 없음 — 전체 모델명으로 폴백")
                for p in all_products:
                    mn = p.model_name.strip()
                    if mn and mn not in seen:
                        seen.add(mn)
                        unique_models.append(mn)
        else:
            logger.info("이전 날짜 데이터 없음 — 전체 모델명으로 폴백")
            for p in all_products:
                mn = p.model_name.strip()
                if mn and mn not in seen:
                    seen.add(mn)
                    unique_models.append(mn)

    logger.info(f"고유 모델명 {len(unique_models)}개 → Kream 검색 시작 (페이지 풀: {config.KREAM_MAX_CONCURRENCY}개)")

    kream_products_map: dict = {}

    async with create_browser(headless=False) as context:
        page_pool: asyncio.Queue = asyncio.Queue()
        for i in range(config.KREAM_MAX_CONCURRENCY):
            logger.info(f"페이지 풀 초기화 [{i+1}/{config.KREAM_MAX_CONCURRENCY}]")
            page = await init_kream_page(context)
            await page_pool.put(page)
            if i < config.KREAM_MAX_CONCURRENCY - 1:
                await _human_wait(2.0, 4.0)

        tasks = [
            _search_with_page_pool(mn, page_pool)
            for mn in unique_models
        ]
        pair_results = await asyncio.gather(*tasks, return_exceptions=True)

    for idx, item in enumerate(pair_results):
        if isinstance(item, Exception):
            mn = unique_models[idx]
            logger.error(f"[{mn}] 예상치 못한 오류: {item}", exc_info=True)
            kream_products_map[mn] = []
        else:
            mn, products = item
            kream_products_map[mn] = products

    total_kream = sum(len(v) for v in kream_products_map.values())
    logger.info(f"Kream 수집 완료 — 총 {total_kream}개 상품")

    # =========================================================================
    # STEP 3: 가격 비교 + 필터링
    # =========================================================================
    logger.info("=" * 60)
    logger.info("STEP 3: 차익 거래 필터링")
    logger.info("=" * 60)

    arbitrage_results = find_arbitrage(all_products, kream_products_map)

    arb_output = output_dir / "arbitrage_results.json"
    try:
        _save_json(arbitrage_results, arb_output)
        logger.info(f"차익 거래 가능 {len(arbitrage_results)}개 저장 → {arb_output}")
    except Exception as exc:
        logger.error(f"arbitrage_results.json 저장 실패: {exc}")
        raise

    # =========================================================================
    # STEP 4: 이전 날짜 대비 변경분 비교 (전체 크롤 날짜 / full 모드에만 실행)
    # =========================================================================
    if mode == "full" and is_full_crawl_day:
        logger.info("=" * 60)
        logger.info("STEP 4: 이전 날짜 대비 변경분 비교")
        logger.info("=" * 60)
        try:
            all_dates = discover_dates(base_output_dir)
            if len(all_dates) >= 2:
                diff_date_pair(all_dates[-2], all_dates[-1], base_output_dir)
            else:
                logger.info("이전 날짜 데이터가 없어 변경분 비교를 건너뜁니다.")
        except Exception as exc:
            logger.warning(f"변경분 비교 중 오류 발생 (파이프라인은 계속): {exc}")
    else:
        if mode == "full":
            logger.info("STEP 4: diff는 STEP 2 전에 이미 생성됨 — 건너뜀")
        else:
            logger.info("STEP 4: kream 모드 — diff는 STEP 2에서 이미 생성됨")

    # =========================================================================
    # 완료 요약
    # =========================================================================
    logger.info("=" * 60)
    logger.info(f"파이프라인 완료 ({mode.upper()} 모드)")
    logger.info(f"  전체 수집:  {len(all_products)}개")
    logger.info(f"  Kream 수집: {total_kream}개")
    logger.info(f"  차익 기회:  {len(arbitrage_results)}개")
    logger.info(f"  결과 파일:  {arb_output}")
    logger.info("=" * 60)


# ---------------------------------------------------------------------------
# CLI 진입점
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="네이버 → Kream 차익 거래 탐색 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "모드 설명:\n"
            "  crawl  전체 사이트 크롤링만 실행 (Kream 검색 없음)\n"
            "  full   크롤링 + Kream 검색 전체 실행 (기본값)\n"
            "  kream  Kream 검색만 실행 (오늘자 *_products.json 사용)"
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["crawl", "full", "kream"],
        default="full",
        help="실행 모드 (기본값: full)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args.mode))
