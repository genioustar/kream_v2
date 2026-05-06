"""
special/crawler.py
범용 적응형 크롤러 — config.SPECIAL_SALE_URL 에 지정된 임의의 쇼핑 페이지에서
상품 카드를 JS 휴리스틱으로 자동 탐지하여 NaverProduct 목록을 반환한다.

사전 준비:
  chrome.py 로 Chrome CDP를 먼저 실행해야 한다.

상품 수집 방식:
  1. CDP로 실제 Chrome에 연결
  2. 페이지 로드 후 스크롤하며 JS 휴리스틱 3단계 전략으로 상품 카드 자동 탐지
  3. seen_urls set 으로 중복 제거하며 NaverProduct 변환
"""
import asyncio
import json
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

from playwright.async_api import async_playwright

import config
from common.logger import get_logger
from common.models import NaverProduct
from naver.parser import extract_model_names

logger = get_logger("special.crawler")

# ---------------------------------------------------------------------------
# 상수
# ---------------------------------------------------------------------------
CDP_URL               = "http://127.0.0.1:9222"
SITE_NAME             = "special"
PAGE_LOAD_TIMEOUT_MS  = 60_000
PAGE_SETTLE_SEC       = 3.0
SCROLL_STEP_PX        = 800
SCROLL_STEP_DELAY_MS  = 1_500
NO_CHANGE_LIMIT       = 5   # scrollY 변화 없음 N회 연속 시 종료

# ---------------------------------------------------------------------------
# JS 휴리스틱 — 상품 카드 자동 탐지
# ---------------------------------------------------------------------------

_JS_EXTRACT = """
() => {
  const PRICE_RE = /(?:₩\\s*)?([\\d,]{4,})\\s*원|₩\\s*([\\d,]+)/;
  const MIN_PRICE = 100;
  const MAX_PRICE = 100_000_000;

  function parsePrice(text) {
    const m = PRICE_RE.exec(text);
    if (!m) return null;
    const raw = (m[1] || m[2]).replace(/,/g, '');
    const n = parseInt(raw, 10);
    if (isNaN(n) || n < MIN_PRICE || n > MAX_PRICE) return null;
    return n;
  }

  function hasPrice(el) {
    return parsePrice(el.innerText || '') !== null;
  }

  function extractCard(el) {
    // anchor
    let anchor = el.tagName === 'A' ? el : el.querySelector('a[href]');
    const url = anchor ? anchor.href : '';

    // img.alt 우선, 없으면 가격 제외 비가격 텍스트 중 가장 긴 것
    const img = el.querySelector('img');
    let name = (img && img.alt && img.alt.trim()) ? img.alt.trim() : '';
    if (!name) {
      // 모든 텍스트 노드 중 가격 패턴이 없는 가장 긴 텍스트
      const texts = Array.from(el.querySelectorAll('*'))
        .map(n => (n.childNodes.length === 1 && n.childNodes[0].nodeType === 3)
          ? n.textContent.trim() : '')
        .filter(t => t.length > 0 && !PRICE_RE.test(t));
      if (texts.length > 0) {
        name = texts.reduce((a, b) => a.length >= b.length ? a : b, '');
      }
    }

    // 가격
    const price = parsePrice(el.innerText || '');

    // code: URL 경로에서 /products/ 다음 세그먼트, 없으면 name
    let code = name;
    if (url) {
      try {
        const path = new URL(url).pathname;
        const m = path.match(/\\/products\\/([^/]+)/);
        if (m) code = m[1];
      } catch (_) {}
    }

    return { url, name, price, code };
  }

  const results = [];
  const seenUrls = new Set();

  function addCard(el) {
    const c = extractCard(el);
    if (!c.url || !c.name || c.price === null) return;
    if (seenUrls.has(c.url)) return;
    seenUrls.add(c.url);
    results.push(c);
  }

  // 전략 1: <li> 요소 중 img + a[href] + 가격패턴 모두 포함한 것
  document.querySelectorAll('li').forEach(li => {
    if (li.querySelector('img') && li.querySelector('a[href]') && hasPrice(li)) {
      addCard(li);
    }
  });

  // 전략 2: <a[href]> 요소 자체에 img + 가격패턴 포함한 것 (전략 1에서 못 잡은 경우)
  if (results.length === 0) {
    document.querySelectorAll('a[href]').forEach(a => {
      if (a.querySelector('img') && hasPrice(a)) {
        addCard(a);
      }
    });
  }

  // 전략 3: 가격 텍스트 leaf 요소에서 위로 올라가며 형제 2개 이상인 조상 탐색
  if (results.length === 0) {
    document.querySelectorAll('*').forEach(el => {
      if (el.children.length > 0) return;  // leaf 요소만
      if (!hasPrice(el)) return;
      let node = el.parentElement;
      while (node && node !== document.body) {
        if (node.children.length >= 2) {
          addCard(node);
          break;
        }
        node = node.parentElement;
      }
    });
  }

  return results;
}
"""


# ---------------------------------------------------------------------------
# 내부 헬퍼
# ---------------------------------------------------------------------------

def _to_naver_product(card: dict, crawled_at: str) -> NaverProduct | None:
    """
    JS 휴리스틱이 추출한 카드 딕셔너리를 NaverProduct로 변환한다.

    model_name 결정:
    - extract_model_names(name) 의 첫 번째 결과 사용
    - 없으면 code 사용
    """
    url   = card.get("url", "")
    name  = card.get("name", "")
    price = card.get("price")
    code  = card.get("code", "") or name

    if not url or not name or price is None:
        return None

    model_names = extract_model_names(name)
    model_name  = model_names[0] if model_names else code

    return NaverProduct(
        site_name    = SITE_NAME,
        product_name = name,
        model_name   = model_name,
        price        = price,
        url          = url,
        crawled_at   = crawled_at,
    )


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------

async def crawl_special() -> list[NaverProduct]:
    """
    config.SPECIAL_SALE_URL 페이지를 범용 JS 휴리스틱으로 크롤링하여
    NaverProduct 목록을 반환한다.

    미설정/빈 값이면 즉시 [] 반환 (크롤링 스킵).
    CDP 연결 실패 시 [] 반환 + error 로그.

    Returns:
        site_name='special' 인 NaverProduct 리스트.
    """
    special_url = getattr(config, "SPECIAL_SALE_URL", "") or ""
    if not special_url.strip():
        logger.info("config.SPECIAL_SALE_URL 미설정 — special 크롤링 건너뜀")
        return []

    logger.info(f"SPECIAL_SALE_URL 크롤링 시작: {special_url}")
    crawled_at = datetime.now().isoformat(timespec="seconds")

    pw = await async_playwright().start()
    try:
        try:
            browser = await pw.chromium.connect_over_cdp(CDP_URL)
            logger.info(f"실제 Chrome CDP 연결 성공 ({CDP_URL})")
        except Exception as exc:
            logger.error(
                f"Chrome CDP 연결 실패: {exc}\n"
                f"먼저 chrome.py를 실행해 Chrome CDP를 시작하세요."
            )
            return []

        context = browser.contexts[0]
        page = await context.new_page()

        try:
            try:
                await page.goto(
                    special_url,
                    wait_until="domcontentloaded",
                    timeout=PAGE_LOAD_TIMEOUT_MS,
                )
            except Exception as exc:
                logger.warning(f"goto 에러(계속 진행): {type(exc).__name__}: {exc}")

            logger.info(f"페이지 로드 완료 — URL: {page.url} | 제목: {await page.title()!r}")
            await asyncio.sleep(PAGE_SETTLE_SEC)

            seen_urls: set[str] = set()
            products: list[NaverProduct] = []

            def _merge_cards(cards: list[dict]) -> None:
                for card in cards:
                    url = card.get("url", "")
                    if not url or url in seen_urls:
                        continue
                    p = _to_naver_product(card, crawled_at)
                    if p:
                        seen_urls.add(url)
                        products.append(p)
                        logger.info(
                            f"수집: {p.product_name!r} | {p.model_name} | {p.price:,}원"
                        )

            # 초기 수집 (스크롤 전)
            try:
                initial_cards = await page.evaluate(_JS_EXTRACT)
                _merge_cards(initial_cards)
                logger.info(f"초기 수집: {len(products)}개")
            except Exception as exc:
                logger.debug(f"초기 JS 실행 실패: {exc}")

            # 스크롤 루프
            stall_streak = 0
            attempt = 0
            while True:
                prev_y: int = await page.evaluate("() => window.scrollY")
                await page.evaluate(f"window.scrollBy(0, {SCROLL_STEP_PX})")
                await page.wait_for_timeout(SCROLL_STEP_DELAY_MS)
                curr_y: int = await page.evaluate("() => window.scrollY")

                attempt += 1
                logger.info(
                    f"스크롤 {attempt}회 — scrollY: {prev_y}→{curr_y}, 누적: {len(products)}개"
                )

                try:
                    cards = await page.evaluate(_JS_EXTRACT)
                    before = len(products)
                    _merge_cards(cards)
                    logger.debug(f"  신규 {len(products) - before}개 추가")
                except Exception as exc:
                    logger.debug(f"스크롤 후 JS 실행 실패: {exc}")

                if curr_y == prev_y:
                    stall_streak += 1
                    if stall_streak >= NO_CHANGE_LIMIT:
                        logger.info("페이지 끝 도달 → 종료")
                        break
                else:
                    stall_streak = 0

            logger.info(f"special 크롤링 완료 — 총 {len(products)}개")
            return products

        finally:
            await page.close()

    finally:
        await pw.stop()


# ---------------------------------------------------------------------------
# 단독 실행 진입점
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    async def _main():
        products = await crawl_special()
        if not products:
            print("수집된 상품이 없습니다.")
            return

        today = date.today().strftime("%Y%m%d")
        out_dir = Path(config.OUTPUT_DIR) / today
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "special_products.json"
        out_path.write_text(
            json.dumps([asdict(p) for p in products], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"\n총 {len(products)}개 저장 → {out_path}")

    asyncio.run(_main())
