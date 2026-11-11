"""
저장소 규칙: ARCHITECTURE.md 의 tests 트리와 설정 표가 실제 파일·config.py 와 같다.

반복된 실수: 묶음 1이 tests 트리에 test_naver_parser.py 를 빠뜨려 검증에서 실패했고,
할인율 표는 아디다스 30% 로 남아 config.py 의 20 과 어긋났다. 평가 때마다 사람이 손으로 대조했다.
"""
import re
import unittest
from pathlib import Path

import config

ROOT = Path(__file__).resolve().parent.parent
ARCHITECTURE = ROOT / "ARCHITECTURE.md"
TREE_TEST_RE = re.compile(r"^│\s+[├└]── (test_\w+\.py)", re.MULTILINE)
DISCOUNT_ROW_RE = re.compile(r"^\| `SITE_DISCOUNT_RATES` \| ([^|]+)\|", re.MULTILINE)
DISCOUNT_PAIR_RE = re.compile(r"(\w+) (\d+)%")
FIX_HINT = "ARCHITECTURE.md 를 실제 코드에 맞게 같은 커밋에서 고쳐라."


def documented_tests(text: str) -> set[str]:
    return set(TREE_TEST_RE.findall(text))


def documented_discount_rates(text: str) -> dict[str, int]:
    match = DISCOUNT_ROW_RE.search(text)
    if match is None:
        return {}
    return {key: int(value) for key, value in DISCOUNT_PAIR_RE.findall(match.group(1))}


class DocsSyncRuleTests(unittest.TestCase):
    def setUp(self):
        self.text = ARCHITECTURE.read_text(encoding="utf-8")

    def test_tests_tree_matches_files(self):
        actual = {path.name for path in (ROOT / "tests").glob("test_*.py")}
        documented = documented_tests(self.text)
        self.assertEqual(
            documented, actual,
            f"트리에 없는 테스트: {sorted(actual - documented)}, 없는 파일을 적음: {sorted(documented - actual)}. {FIX_HINT}",
        )

    def test_discount_rate_table_matches_config(self):
        self.assertEqual(
            documented_discount_rates(self.text), dict(config.SITE_DISCOUNT_RATES),
            f"SITE_DISCOUNT_RATES 표가 config.py 와 다르다. {FIX_HINT}",
        )

    def test_rules_catch_the_real_past_mistakes(self):
        missing_parser_test = "│   ├── test_naver_collection.py\n│   └── test_model_names.py\n"
        self.assertNotIn("test_naver_parser.py", documented_tests(missing_parser_test))
        stale_row = "| `SITE_DISCOUNT_RATES` | marketplace 0%, adidas 30%, musinsa 0% | 설명 |\n"
        self.assertEqual(documented_discount_rates(stale_row)["adidas"], 30)
        self.assertNotEqual(documented_discount_rates(stale_row)["adidas"], config.SITE_DISCOUNT_RATES["adidas"])


if __name__ == "__main__":
    unittest.main()
