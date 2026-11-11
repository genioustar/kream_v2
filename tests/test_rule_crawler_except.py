"""
저장소 규칙: 크롤러가 요청 실패를 삼키고 수집 루프를 끝내 일부 결과를 정상처럼 반환하지 않는다.

반복된 실수: 무신사(9d5690a) 의 `except Exception: logger.warning(...); break` 가
그때까지 모은 일부를 정상 결과로 반환해 부분 파일이 하루 종일 쓰였다.
상품 하나를 건너뛰는 `return None`/`continue` 는 허용하고, 루프 전체를 끝내는 `break` 만 막는다.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX_HINT = (
    "except 블록에서 break 로 수집을 끝내지 말고 logger.error(...) 후 raise 하라. "
    "HTTP 403/429 는 common.pacing.check_response 로 CrawlBlockedError 로 바꾼다. "
    "예: musinsa/crawler.py 의 _crawl_brand_products"
)
# 이 이전 코드에서 실제로 일어난 실수. 검사가 이를 잡는지 테스트로 고정한다.
PAST_MISTAKE_SOURCE = '''
async def _crawl_brand_products(sale_url, crawled_at):
    products = []
    while next_url:
        try:
            data = await _fetch_json_async(next_url, referer)
        except Exception as exc:
            logger.warning(f"무신사 API 요청 실패 (page={page_num}): {exc}")
            break
        products.extend(parse(data))
    return products
'''


def crawler_modules() -> list[Path]:
    """사이트 크롤러 모듈. 패키지마다 crawler.py 하나다."""
    return sorted(path for path in ROOT.glob("*/crawler.py") if path.parent.name != "venv")


def violations(source: str, label: str) -> list[str]:
    """raise 없이 break 하는 except, 또는 CrawlBlockedError 를 삼키는 except 를 찾는다."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.ExceptHandler):
            continue
        inner = [child for statement in node.body for child in ast.walk(statement)]
        reraises = any(isinstance(child, ast.Raise) for child in inner)
        caught = ast.unparse(node.type) if node.type is not None else "모든 예외"
        if not reraises and any(isinstance(child, ast.Break) for child in inner):
            found.append(f"{label}:{node.lineno} except {caught}: raise 없이 break")
        if "CrawlBlockedError" in caught and not reraises:
            found.append(f"{label}:{node.lineno} except {caught}: 접근 제한을 삼킴")
    return found


class CrawlerExceptRuleTests(unittest.TestCase):
    def test_crawler_modules_are_found(self):
        names = {path.parent.name for path in crawler_modules()}
        self.assertTrue({"musinsa", "ssfshop", "naver", "kream"} <= names, names)

    def test_no_crawler_swallows_failure_and_ends_loop(self):
        found = []
        for path in crawler_modules():
            found += violations(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
        self.assertEqual(found, [], "\n".join(found + [FIX_HINT]))

    def test_rule_catches_the_real_past_mistake(self):
        self.assertEqual(len(violations(PAST_MISTAKE_SOURCE, "musinsa@9d5690a")), 1)

    def test_item_level_skip_is_allowed(self):
        source = "for item in items:\n    try:\n        parse(item)\n    except ValueError:\n        continue\n"
        self.assertEqual(violations(source, "ok"), [])


if __name__ == "__main__":
    unittest.main()
