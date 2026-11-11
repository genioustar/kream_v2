"""
저장소 규칙: 로그는 lazy %-formatting 으로 쓴다(CLAUDE.md 로그 출력 규칙).

반복된 실수: 규칙이 문서에만 있어 새 코드가 f-string 로그를 계속 늘렸다(AST 기준 9d5690a 126개 → cdb3c09 129개).
기존 호출은 많아서 한 번에 고치지 않고, 기준선보다 늘어나면 실패하는 래칫으로 막는다.
기존 호출을 고치면 MAX_FSTRING_LOG_CALLS 를 그 수만큼 내린다.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 2026-11-11 STEP 1 등록 표로 f-string 로그 19개를 뺀 저장소 전체(tests·venv 제외) 호출 수. 올리지 말고 내리기만 한다.
MAX_FSTRING_LOG_CALLS = 107
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
EXCLUDED_DIRS = {"venv", ".venv", "tests", "__pycache__"}
FIX_HINT = (
    'logger.info(f"... {x}") 대신 logger.info("... %s", x) 로 써라. '
    "기존 f-string 로그를 고쳤다면 MAX_FSTRING_LOG_CALLS 를 줄여라."
)


def fstring_log_calls(source: str, label: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
            and node.args
            and isinstance(node.args[0], ast.JoinedStr)
        ):
            found.append(f"{label}:{node.lineno}")
    return found


def project_sources() -> list[Path]:
    return sorted(
        path for path in ROOT.rglob("*.py")
        if not EXCLUDED_DIRS & set(path.relative_to(ROOT).parts)
    )


class LoggingRuleTests(unittest.TestCase):
    def test_fstring_logs_do_not_grow(self):
        found = []
        for path in project_sources():
            found += fstring_log_calls(path.read_text(encoding="utf-8"), str(path.relative_to(ROOT)))
        self.assertLessEqual(
            len(found), MAX_FSTRING_LOG_CALLS,
            f"f-string 로그 {len(found)}개(기준 {MAX_FSTRING_LOG_CALLS}). {FIX_HINT}",
        )

    def test_rule_catches_a_new_fstring_log(self):
        # 실제 실수의 모양: SSF 블록이 main.py 에 f-string 로그를 더했다.
        new_code = 'logger.info(f"SSF SHOP 상품 {len(products)}개 저장 → {path}")\n'
        self.assertEqual(len(fstring_log_calls(new_code, "main.py")), 1)
        self.assertEqual(fstring_log_calls('logger.info("저장 %s개", n)\n', "ok"), [])


if __name__ == "__main__":
    unittest.main()
