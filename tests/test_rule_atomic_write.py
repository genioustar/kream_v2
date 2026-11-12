"""
저장소 규칙: JSON 파일은 common/atomic_io.write_json_atomic 으로만 쓴다.

반복된 실수: 체크포인트는 NamedTemporaryFile을 직접 써서 권한이 0600으로 남았고,
네이버 진단은 고정 이름 .tmp를, 아디다스 진단은 write_text(json.dumps(...))를 썼다.
제품 코드의 json.dump(와 .write_text(json.dumps(...))는 common/atomic_io.py 밖에 있으면 실패한다.
json.dumps 단독 호출(로그·해시)은 허용한다. 주석과 문자열은 AST라 잡지 않는다.
별칭 import(from json import dump, import json as j)와 open().write(json.dumps(...))는 검사하지 않는다.
"""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_JSON_DUMP = Path("common") / "atomic_io.py"
EXCLUDED_DIRS = frozenset({"venv", "tests", ".claude"})
JSON_MODULE = "json"
DUMP_ATTR = "dump"
DUMPS_ATTR = "dumps"
WRITE_TEXT_ATTR = "write_text"
FIX_HINT = "JSON 파일은 common.atomic_io.write_json_atomic 으로 써라."


def _is_json_call(node: ast.AST, attr: str) -> bool:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr != attr:
        return False
    return isinstance(node.func.value, ast.Name) and node.func.value.id == JSON_MODULE


def _contains_json_dumps(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if _is_json_call(child, DUMPS_ATTR):
            return True
    return False


def _write_text_wraps_dumps(node: ast.Call) -> bool:
    if not isinstance(node.func, ast.Attribute) or node.func.attr != WRITE_TEXT_ATTR:
        return False
    for arg in node.args:
        if _contains_json_dumps(arg):
            return True
    for keyword in node.keywords:
        if keyword.value is not None and _contains_json_dumps(keyword.value):
            return True
    return False


def violations(source: str, label: str, *, allow_json_dump: bool) -> list[str]:
    """json.dump 와 write_text(json.dumps(...)) 의 파일:줄을 반환한다."""
    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        if _is_json_call(node, DUMP_ATTR) and not allow_json_dump:
            found.append(f"{label}:{node.lineno} json.dump")
        if _write_text_wraps_dumps(node):
            found.append(f"{label}:{node.lineno} write_text(json.dumps)")
    return found


def product_sources() -> list[Path]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        blocked = False
        for part in relative.parts:
            if part in EXCLUDED_DIRS:
                blocked = True
                break
        if not blocked:
            found.append(path)
    return found


class AtomicWriteRuleTests(unittest.TestCase):
    def test_product_code_writes_json_only_through_atomic_io(self):
        found = []
        for path in product_sources():
            relative = path.relative_to(ROOT)
            found.extend(violations(
                path.read_text(encoding="utf-8"),
                relative.as_posix(),
                allow_json_dump=relative == ALLOWED_JSON_DUMP,
            ))
        self.assertEqual(found, [], "\n".join(found + [FIX_HINT]))

    def test_detector_reports_file_and_line(self):
        source = "import json\njson.dump({}, handle)\npath.write_text(json.dumps({}))\n"
        self.assertEqual(
            violations(source, "sample.py", allow_json_dump=False),
            ["sample.py:2 json.dump", "sample.py:3 write_text(json.dumps)"],
        )

    def test_dumps_comments_and_strings_are_allowed(self):
        source = (
            'import json\n'
            'logger.info("%s", json.dumps({}))\n'
            'key = json.dumps({"version": 1}, sort_keys=True)\n'
            'text = "json.dump({}, handle)"\n'
            '# path.write_text(json.dumps({}))\n'
        )
        self.assertEqual(violations(source, "sample.py", allow_json_dump=False), [])

    def test_atomic_io_may_call_json_dump_but_not_write_text(self):
        dump_only = "import json\njson.dump({}, handle)\n"
        self.assertEqual(violations(dump_only, "common/atomic_io.py", allow_json_dump=True), [])
        both = dump_only + "path.write_text(json.dumps({}))\n"
        self.assertEqual(
            violations(both, "common/atomic_io.py", allow_json_dump=True),
            ["common/atomic_io.py:3 write_text(json.dumps)"],
        )


if __name__ == "__main__":
    unittest.main()
