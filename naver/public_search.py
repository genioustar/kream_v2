"""공개 스토어의 검색 UI를 통해 비로그인 검색 목록으로 이동한다."""
import re
from urllib.parse import parse_qs, urlsplit

from common.pacing import CrawlBlockedError
from naver.diagnostics import NaverCollectionError


def parse_public_search_url(url: str) -> dict:
    """UI로 재현할 수 있는 첫 검색 페이지 설정만 허용한다."""
    try:
        parsed = urlsplit(url)
        query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        valid = (
            parsed.scheme == "https"
            and parsed.hostname in ("brand.naver.com", "smartstore.naver.com")
            and parsed.port in (None, 443)
            and parsed.username is None
            and parsed.password is None
            and not parsed.fragment
            and re.fullmatch(r"/[A-Za-z0-9_-]+/search", parsed.path) is not None
            and set(query) <= {"q", "st", "dt", "page", "size"}
            and all(len(values) == 1 for values in query.values())
            and bool(query.get("q", [""])[0].strip())
            and query.get("st", ["RELEVANCE"])[0] == "RELEVANCE"
            and query.get("dt", ["IMAGE"])[0] == "IMAGE"
            and query.get("page", ["1"])[0] == "1"
            and query.get("size", ["40"])[0] in {"20", "40", "60", "80"}
        )
    except (AttributeError, TypeError, ValueError):
        valid = False
    if not valid:
        raise ValueError("공개 검색 UI에서 지원하지 않는 검색 URL 설정입니다.")
    return {
        "home_url": f"https://{parsed.hostname}{parsed.path.removesuffix('/search')}",
        "query": query["q"][0],
        "size": int(query.get("size", ["40"])[0]),
    }


def _matches_search(url, target, include_size=False):
    try:
        actual = parse_public_search_url(str(url))
    except ValueError:
        return False
    return (
        actual["home_url"] == target["home_url"]
        and actual["query"] == target["query"]
        and (not include_size or actual["size"] == target["size"])
    )


async def navigate_public_search(page, url, diagnostics, timeout_ms) -> int:
    """동일 검색 조건·페이지 크기·공개 총수를 확인하고 예상 카드 수를 반환한다."""
    diagnostics.data["entry_mode"] = "public_store_search"
    try:
        target = parse_public_search_url(url)
    except ValueError:
        diagnostics.data["failure_detail"] = "unsupported_public_search_url"
        raise NaverCollectionError("collection_error", diagnostics.report_path) from None

    try:
        while True:
            await diagnostics.run(
                page.goto(target["home_url"], wait_until="domcontentloaded", timeout=timeout_ms),
                "public_search_home",
            )
            if not diagnostics.pause_reason:
                break
            await diagnostics.wait_until_resumed()
        opener = page.get_by_role("button", name="검색어를 입력해주세요", exact=True)
        await diagnostics.run(opener.wait_for(state="visible", timeout=timeout_ms), "public_search_open")
        await diagnostics.run(opener.click(timeout=timeout_ms), "public_search_open")
        submit = page.get_by_role("button", name="검색하기", exact=True)
        await diagnostics.run(submit.wait_for(state="visible", timeout=timeout_ms), "public_search_form")
        search_input = page.get_by_placeholder("검색어를 입력해주세요.", exact=True)
        await diagnostics.run(search_input.fill(target["query"], timeout=timeout_ms), "public_search_form")
        await diagnostics.run(submit.click(timeout=timeout_ms), "public_search_submit")
        await diagnostics.run(
            page.wait_for_url(lambda current: _matches_search(current, target), timeout=timeout_ms),
            "public_search_results",
        )
        heading = page.get_by_text(f"'{target['query']}' 검색결과", exact=True)
        await diagnostics.run(heading.wait_for(state="visible", timeout=timeout_ms), "public_search_results")

        total_locator = page.get_by_text(re.compile(r"검색결과\s*전체\s*상품\s*\(총\s*[\d,]+개\)"))
        # innerText는 페이지 안에서 숫자로만 축약하고 본문·검색어를 진단에 저장하지 않는다.
        await diagnostics.run(total_locator.first.wait_for(state="visible", timeout=timeout_ms), "public_search_count")
        expected_count = await diagnostics.run(page.evaluate("""() => {
            const text = (document.body?.innerText || '').replace(/\\s+/g, ' ');
            const count = text.match(/검색결과\\s*전체\\s*상품\\s*\\(총\\s*([\\d,]+)개\\)/);
            return count ? Number(count[1].replace(/,/g, '')) : null;
        }"""), "public_search_count")
        if type(expected_count) is not int or expected_count < 0:
            diagnostics.data["failure_detail"] = "search_result_count_missing"
            raise NaverCollectionError("collection_error", diagnostics.report_path)
        diagnostics.data["expected_card_count"] = expected_count
        diagnostics.data["requested_page_size"] = target["size"]
        if expected_count > target["size"]:
            diagnostics.data["failure_detail"] = "incomplete_search_results"
            raise NaverCollectionError("collection_error", diagnostics.report_path)

        # 검색 화면의 현재 선택 버튼을 이용한다. URL만 바꾸거나 API를 직접 호출하지 않는다.
        size_button = page.get_by_role("button", name=re.compile(r"^(20|40|60|80)개씩 보기$"))
        await diagnostics.run(size_button.wait_for(state="visible", timeout=timeout_ms), "public_search_page_size")
        current_size = await diagnostics.run(size_button.inner_text(timeout=timeout_ms), "public_search_page_size")
        if current_size.strip() != f"{target['size']}개씩 보기":
            await diagnostics.run(size_button.click(timeout=timeout_ms), "public_search_page_size")
            desired_size = page.get_by_role("button", name=f"{target['size']}개씩 보기", exact=True)
            await diagnostics.run(desired_size.click(timeout=timeout_ms), "public_search_page_size")
        await diagnostics.run(
            page.wait_for_url(lambda current: _matches_search(current, target, include_size=True), timeout=timeout_ms),
            "public_search_page_size",
        )
        await diagnostics.run(heading.wait_for(state="visible", timeout=timeout_ms), "public_search_results")
        await diagnostics.check("public_search_results")
        diagnostics.data["observed_card_count"] = await diagnostics.run(
            page.locator(diagnostics.item_selector).count(), "public_search_results",
        )
        return expected_count
    except (CrawlBlockedError, NaverCollectionError):
        raise
    except Exception as exc:
        await diagnostics.check("public_search_failed")
        diagnostics.data["exception_class"] = type(exc).__name__
        diagnostics.data["failure_detail"] = "public_search_navigation_failed"
        raise NaverCollectionError("collection_error", diagnostics.report_path) from None
