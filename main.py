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
  python main.py --mode crawl --site adidas
  python main.py --mode kream
"""
import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

import config
from common.atomic_io import write_json_atomic
from common.browser import create_browser
from common.logger import get_logger
from common.models import MarketplaceProduct
from adidas.crawler import crawl_adidas
from diff_output import diff_date_pair, discover_dates
from kream.comparator import find_arbitrage
from kream.crawler import iter_kream_results
from kream.history_marker import RESULT_FILENAME, load_previous_price_map, mark_result
from common.pacing import CrawlBlockedError
from musinsa.crawler import crawl_musinsa
from naver.crawler import crawl_marketplace
from ssfshop.crawler import crawl_ssfshop
from nike.crawler import crawl_nike
from special.crawler import crawl_special

# 네이버 전체 상품을 Kream에 조회하는 날짜 (그 외 날짜는 diff 기반으로 조회)
FULL_CRAWL_DAYS: frozenset[int] = frozenset({1, 10, 20, 30})

# Kream 청크 처리 설정
KREAM_CHUNK_SIZE = 200           # 200개 초과 시 청크 분할

logger = get_logger("main")


@dataclass(frozen=True)
class SiteCrawler:
    """STEP 1에 한 번만 등록하는 사이트.

    key는 `{key}_products.json` 파일명과 SITE_DISCOUNT_RATES 키로 같이 쓴다.
    required면 네이버처럼 실패 시 파이프라인을 멈추고, 아니면 선택 사이트다.
    """

    key: str
    label: str
    crawl: Callable[[], Awaitable[list[MarketplaceProduct]]]
    required: bool


# 실행 순서는 등록 순서다. 필수 사이트는 네이버(marketplace)뿐이다.
SITE_CRAWLERS: tuple[SiteCrawler, ...] = (
    SiteCrawler("marketplace", "네이버", crawl_marketplace, True),
    SiteCrawler("adidas", "아디다스", crawl_adidas, False),
    SiteCrawler("musinsa", "무신사", crawl_musinsa, False),
    SiteCrawler("ssfshop", "SSF SHOP", crawl_ssfshop, False),
    SiteCrawler("nike", "나이키", crawl_nike, False),
    SiteCrawler("special", "special", crawl_special, False),
)


# ---------------------------------------------------------------------------
# 직렬화 / 로드 헬퍼
# ---------------------------------------------------------------------------

def _save_json(data: list, path: Path) -> None:
    """데이터클래스 리스트를 JSON 파일로 저장한다."""
    write_json_atomic(path, [asdict(item) for item in data])


def _load_products_json(path: Path) -> list[MarketplaceProduct]:
    """*_products.json 파일을 MarketplaceProduct 리스트로 복원한다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return [MarketplaceProduct(**item) for item in data]


def _load_all_products(output_dir: Path) -> list[MarketplaceProduct]:
    """output_dir 내 모든 *_products.json 을 로드하여 합산 반환한다."""
    all_products: list[MarketplaceProduct] = []
    for fpath in sorted(output_dir.glob("*_products.json")):
        try:
            items = _load_products_json(fpath)
            logger.info(f"{fpath.name} 로드 — {len(items)}개")
            all_products.extend(items)
        except Exception as exc:
            logger.warning(f"{fpath.name} 로드 실패 (건너뜀): {exc}")
    return all_products


def _apply_sale_price(products: list[MarketplaceProduct], source_key: str) -> None:
    """크롤러 결과에 SITE_DISCOUNT_RATES 의 할인율을 적용해 sale_price 를 부여한다."""
    rate = config.SITE_DISCOUNT_RATES.get(source_key, 0)
    if not rate:
        return
    for p in products:
        p.sale_price = int(round(p.price * (1 - rate / 100)))


def _select_sites(site_keys: list[str] | None) -> tuple[SiteCrawler, ...]:
    """None이면 등록 표 전체. 키 목록이면 중복을 빼고 표 순서로 고른다."""
    if site_keys is None:
        return SITE_CRAWLERS
    wanted = set(site_keys)
    return tuple(site for site in SITE_CRAWLERS if site.key in wanted)


async def _crawl_site(site: SiteCrawler, output_dir: Path, *, force: bool = False) -> None:
    """오늘자 파일이 있으면 건너뛰고, 없으면 필수·선택 규칙으로 한 사이트를 수집한다.

    force면 오늘자 파일이 있어도 다시 수집한다. 빈 결과면 기존 파일 유지
    (선택 사이트, 그리고 --site 재수집 시 네이버).
    """
    output = output_dir / f"{site.key}_products.json"
    if output.exists() and not force:
        logger.info("오늘자 파일 존재 — %s 크롤링 스킵: %s", site.label, output)
        return
    if site.required:
        await _crawl_required_site(site, output)
    else:
        await _crawl_optional_site(site, output)


async def _crawl_required_site(site: SiteCrawler, output: Path) -> None:
    """네이버. 결과가 비어 있고 오늘자 파일이 있으면(= --site 재수집) 덮어쓰지 않는다.

    파일이 없으면 빈 리스트도 저장한다. RuntimeError면 프로세스를 종료한다.
    """
    logger.info("%s 크롤링 시작", site.label)
    try:
        products = await site.crawl()
        if not products and output.exists():
            logger.warning("%s 수집 결과 없음 — 기존 파일 유지: %s", site.label, output)
            return
        _apply_sale_price(products, site.key)
        _save_json(products, output)
        logger.info("%s 상품 %s개 저장 → %s", site.label, len(products), output)
    except RuntimeError as exc:
        # CrawlBlockedError는 RuntimeError라 이 경로로 종료한다.
        logger.error("%s 크롤링 전체 실패: %s", site.label, exc)
        sys.exit(1)
    except Exception as exc:
        logger.error("%s_products.json 저장 실패: %s", site.key, exc)
        raise


async def _crawl_optional_site(site: SiteCrawler, output: Path) -> None:
    """선택 사이트. 빈 결과는 저장하지 않고, 차단만 파이프라인을 멈춘다."""
    logger.info("%s 크롤링 시작", site.label)
    try:
        products = await site.crawl()
        if products:
            _apply_sale_price(products, site.key)
            _save_json(products, output)
            logger.info("%s 상품 %s개 저장 → %s", site.label, len(products), output)
        else:
            logger.warning(
                "%s 수집 결과 없음 — 파일 저장 생략 (다음 실행 시 재시도)",
                site.label,
            )
    except CrawlBlockedError:
        raise
    except Exception as exc:
        logger.warning("%s 크롤링 실패 (파이프라인은 계속): %s", site.label, exc)


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
# 메인 파이프라인
# ---------------------------------------------------------------------------

async def main(mode: str, site_keys: list[str] | None = None) -> None:
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

        # crawl이 아니거나 --site가 없으면 None이라 표 전체다. 재수집은 crawl의 --site만.
        force = mode == "crawl" and site_keys is not None
        selected = _select_sites(site_keys if force else None)
        if force:
            logger.info(
                "지정 사이트 재수집: %s",
                ", ".join(site.key for site in selected),
            )
        for site in selected:
            await _crawl_site(site, output_dir, force=force)

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
    logger.info("STEP 2: Kream 가격 비교 시작 (단일 탭 순차 검색)")
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
                logger.info("변경된 모델 없음 — Kream 검색 생략")
        else:
            logger.info("이전 날짜 데이터 없음 — 전체 모델명으로 폴백")
            for p in all_products:
                mn = p.model_name.strip()
                if mn and mn not in seen:
                    seen.add(mn)
                    unique_models.append(mn)

    if not unique_models:
        logger.info("검색할 모델 없음 — 브라우저를 열지 않습니다.")
        return

    # 200개 초과 시 청크 분할
    if len(unique_models) > KREAM_CHUNK_SIZE:
        model_chunks = [
            unique_models[i:i + KREAM_CHUNK_SIZE]
            for i in range(0, len(unique_models), KREAM_CHUNK_SIZE)
        ]
        logger.info(
            f"고유 모델명 {len(unique_models)}개 → "
            f"{len(model_chunks)}개 청크로 분할 (청크당 최대 {KREAM_CHUNK_SIZE}개, 추가 휴식 없음)"
        )
    else:
        model_chunks = [unique_models]
        logger.info(f"고유 모델명 {len(unique_models)}개 → Kream 단일 탭 순차 검색 시작")

    arbitrage_results: list = []
    total_kream = 0
    arb_output = output_dir / RESULT_FILENAME
    previous_prices = load_previous_price_map(output_dir)

    for chunk_idx, chunk in enumerate(model_chunks):
        chunk_label = f"청크 [{chunk_idx + 1}/{len(model_chunks)}]"
        logger.info("=" * 60)
        logger.info(f"STEP 2~3 {chunk_label}: Kream 검색 + 차익 비교 ({len(chunk)}개)")
        logger.info("=" * 60)

        kream_products_map: dict = {}

        search_error = None
        try:
            async with create_browser() as context:
                async for mn, products in iter_kream_results(chunk, context):
                    kream_products_map[mn] = products
        except Exception as exc:
            search_error = exc
            logger.error(f"{chunk_label} 검색 중단 — 완료 결과를 저장합니다: {exc}")

        if search_error is not None and not kream_products_map:
            raise search_error

        chunk_kream = sum(len(v) for v in kream_products_map.values())
        total_kream += chunk_kream
        logger.info(f"{chunk_label} Kream 수집 완료 — {chunk_kream}개")

        # 차익 비교 후 누적
        chunk_results = find_arbitrage(all_products, kream_products_map)
        arbitrage_results.extend(chunk_results)
        arbitrage_results.sort(key=lambda r: r.price_diff, reverse=True)

        # 청크마다 파일 저장 (누적 결과)
        try:
            arb_dicts = []
            for item in arbitrage_results:
                d = asdict(item)
                d["price_diff"] = f"{d['price_diff']:,}"
                arb_dicts.append(mark_result(d, previous_prices))
            write_json_atomic(arb_output, arb_dicts)
            logger.info(
                f"{chunk_label} 누적 차익 {len(arbitrage_results)}개 저장 → {arb_output}"
            )
        except Exception as exc:
            logger.error(f"{RESULT_FILENAME} 저장 실패: {exc}")
            raise

        if search_error is not None:
            raise search_error

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

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="네이버 → Kream 차익 거래 탐색 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "모드 설명:\n"
            "  crawl  전체 사이트 크롤링만 실행 (Kream 검색 없음)\n"
            "  full   크롤링 + Kream 검색 전체 실행 (기본값)\n"
            "  kream  Kream 검색만 실행 (오늘자 *_products.json 사용)\n"
            "\n"
            "사이트 지정 (--site, crawl 전용):\n"
            "  python main.py --mode crawl --site adidas\n"
            "  python main.py --mode crawl --site adidas nike\n"
            "  키는 등록 표 순서로 한 번씩 다시 수집한다.\n"
            "  오늘자 파일이 있어도 덮어쓴다. "
            "빈 결과면 기존 파일 유지(선택 사이트, 그리고 --site 재수집 시 네이버).\n"
            "  full·kream 과 함께 쓰면 오류로 종료한다."
        ),
    )
    parser.add_argument(
        "--mode",
        choices=["crawl", "full", "kream"],
        default="full",
        help="실행 모드 (기본값: full)",
    )
    parser.add_argument(
        "--site",
        nargs="+",
        choices=[site.key for site in SITE_CRAWLERS],
        help="crawl 모드에서 다시 수집할 사이트 키 (중복은 한 번, 등록 표 순서)",
    )
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.site and args.mode != "crawl":
        parser.error("--site 는 --mode crawl 에서만 사용할 수 있습니다.")
    return args


if __name__ == "__main__":
    args = parse_args()
    asyncio.run(main(args.mode, args.site))
