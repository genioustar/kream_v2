"""
저장소 규칙: 로그는 lazy %-formatting 으로 쓴다(CLAUDE.md 로그 출력 규칙).
로깅 설정은 common.logger.setup_logging() 한 곳이고, 모듈은 logging.getLogger(__name__) 를 쓴다.

반복된 실수: 규칙이 문서에만 있어 새 코드가 f-string 로그를 계속 늘렸다(AST 기준 9d5690a 126개 → cdb3c09 129개).
기존 호출은 많아서 한 번에 고치지 않고, 기준선보다 늘어나면 실패하는 래칫으로 막는다.
기존 호출을 고치면 MAX_FSTRING_LOG_CALLS 를 그 수만큼 내린다.
모듈마다 get_logger 로 핸들러를 붙이면 import 만으로 logs/ 가 생겼다.
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


ENTRY_LOGGER_NAMES = {
    "main.py": "main",
    "chrome.py": "chrome",
    "diff_output.py": "diff_output",
}
SETUP_MODULE = "common/logger.py"
LOGGING_EXCLUDED_DIRS = frozenset({"venv", ".venv", "tests", ".claude", "__pycache__"})
SETUP_HINT = (
    "로깅 설정은 common.logger.setup_logging() 한 곳이다. "
    "호출은 main.py·chrome.py·diff_output.py 의 __main__ 첫 문장에서만 하고, "
    "그 세 파일은 logging.getLogger('main'|'chrome'|'diff_output') 를 쓴다. "
    "나머지 모듈은 logging.getLogger(__name__) 만 쓴다. "
    "basicConfig·dictConfig·fileConfig 와 get_logger 는 쓰지 않는다. "
    "PROJECT_LOGGERS 는 getLogger 를 쓰는 최상위 패키지와 그 세 이름의 합과 같다."
)
CONFIG_CALLS = frozenset({"basicConfig", "dictConfig", "fileConfig"})
_PACKAGE_SKIP = frozenset({"venv", ".venv", "tests", "__pycache__"})


def _call_name(node: ast.Call) -> str | None:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _is_main_guard(test: ast.AST) -> bool:
    if not isinstance(test, ast.Compare) or len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return False
    if len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    name_main = isinstance(left, ast.Name) and left.id == "__name__"
    const_main = isinstance(right, ast.Constant) and right.value == "__main__"
    swapped_name = isinstance(right, ast.Name) and right.id == "__name__"
    swapped_const = isinstance(left, ast.Constant) and left.value == "__main__"
    return (name_main and const_main) or (swapped_name and swapped_const)


def _is_logging_getlogger(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "getLogger"
        and isinstance(func.value, ast.Name)
        and func.value.id == "logging"
    )


def _is_dunder_name_logger(node: ast.Call) -> bool:
    return (
        not node.keywords
        and len(node.args) == 1
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "__name__"
    )


def _const_logger_name(node: ast.Call) -> str | None:
    if node.keywords or len(node.args) != 1:
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def _is_allowed_setup_getlogger(node: ast.Call) -> bool:
    if node.keywords or len(node.args) > 1:
        return False
    if len(node.args) == 0:
        return True
    return isinstance(node.args[0], ast.Name)


def _setup_function(tree: ast.AST) -> ast.FunctionDef | None:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "setup_logging":
            return node
    return None


def _split_setup_calls(tree: ast.AST) -> tuple[list[int], list[int]]:
    inside_ids: set[int] = set()
    inside: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not _is_main_guard(node.test):
            continue
        for child in ast.walk(node):
            if isinstance(child, ast.Call) and _call_name(child) == "setup_logging" and id(child) not in inside_ids:
                inside_ids.add(id(child))
                inside.append(child.lineno)
    outside = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _call_name(node) == "setup_logging" and id(node) not in inside_ids:
            outside.append(node.lineno)
    return inside, outside


def _getlogger_form_violations(tree: ast.AST, label: str) -> list[str]:
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call) and _is_logging_getlogger(node)]
    if label in ENTRY_LOGGER_NAMES:
        expected = ENTRY_LOGGER_NAMES[label]
        if len(calls) == 1 and _const_logger_name(calls[0]) == expected:
            return []
        return [f"{label}: 로거는 logging.getLogger({expected!r}) 하나여야 한다"]
    if label == SETUP_MODULE:
        return _setup_module_getlogger_violations(tree, calls)
    return [
        f"{label}:{call.lineno} logging.getLogger"
        for call in calls
        if not _is_dunder_name_logger(call)
    ]


def _setup_module_getlogger_violations(tree: ast.AST, calls: list[ast.Call]) -> list[str]:
    setup = _setup_function(tree)
    if setup is None:
        return [f"{SETUP_MODULE}: setup_logging 함수가 없다"]
    inside = {id(node) for node in ast.walk(setup)}
    found = []
    for call in calls:
        if id(call) not in inside or not _is_allowed_setup_getlogger(call):
            found.append(f"{SETUP_MODULE}:{call.lineno} logging.getLogger")
    if not any(len(call.args) == 0 and id(call) in inside for call in calls):
        found.append(f"{SETUP_MODULE}: 루트 logging.getLogger() 가 없다")
    if not any(
        len(call.args) == 1 and isinstance(call.args[0], ast.Name) and id(call) in inside
        for call in calls
    ):
        found.append(f"{SETUP_MODULE}: 프로젝트 로거 logging.getLogger(name) 가 없다")
    return found


def _is_setup_call_stmt(stmt: ast.stmt | None) -> bool:
    if not isinstance(stmt, ast.Expr) or not isinstance(stmt.value, ast.Call):
        return False
    call = stmt.value
    return _call_name(call) == "setup_logging" and not call.args and not call.keywords


def _main_preface_violations(tree: ast.AST, label: str) -> list[str]:
    guards = [node for node in tree.body if isinstance(node, ast.If) and _is_main_guard(node.test)]
    first = guards[0].body[0] if len(guards) == 1 and guards[0].body else None
    if len(guards) == 1 and _is_setup_call_stmt(first):
        return []
    return [f"{label}: __main__ 첫 문장이 setup_logging() 이 아니다"]


def _setup_call_violations(tree: ast.AST, label: str) -> list[str]:
    inside, outside = _split_setup_calls(tree)
    if label in ENTRY_LOGGER_NAMES:
        found = [f"{label}:{line} setup_logging() 가 __main__ 밖에 있다" for line in outside]
        if len(inside) != 1:
            found.append(f"{label}: setup_logging() 호출이 __main__ 에 {len(inside)}개다")
        found.extend(_main_preface_violations(tree, label))
        return found
    return [f"{label}:{line} setup_logging()" for line in inside + outside]


def _addhandler_lines_outside_setup(tree: ast.AST) -> list[int]:
    setup = _setup_function(tree)
    inside = {id(node) for node in ast.walk(setup)} if setup is not None else set()
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _call_name(node) == "addHandler" and id(node) not in inside
    ]


def logging_violations(source: str, label: str) -> list[str]:
    """get_logger·설정 호출·잘못된 getLogger·setup_logging 위치 위반을 반환한다."""
    tree = ast.parse(source)
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "get_logger":
                    found.append(f"{label}:{node.lineno} get_logger import")
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name == "get_logger":
            found.append(f"{label}:{node.lineno} get_logger")
        elif name in CONFIG_CALLS:
            found.append(f"{label}:{node.lineno} {name}")
        elif isinstance(node.func, ast.Name) and node.func.id == "getLogger":
            found.append(f"{label}:{node.lineno} getLogger")
    found.extend(_getlogger_form_violations(tree, label))
    found.extend(_setup_call_violations(tree, label))
    if label == SETUP_MODULE:
        found.extend(
            f"{label}:{line} addHandler" for line in _addhandler_lines_outside_setup(tree)
        )
    return found


def project_logger_names(source: str) -> set[str] | None:
    """PROJECT_LOGGERS 할당의 문자열 원소. 상수 나열이 아니면 None."""
    tree = ast.parse(source)
    for node in tree.body:
        value = None
        target = None
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            target = node.targets[0].id
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target.id
            value = node.value
        if target != "PROJECT_LOGGERS" or not isinstance(value, (ast.Tuple, ast.List)):
            continue
        names = set()
        for elt in value.elts:
            if not isinstance(elt, ast.Constant) or not isinstance(elt.value, str):
                return None
            names.add(elt.value)
        return names
    return None


def logged_top_level_packages() -> set[str]:
    """getLogger 호출이 있는 최상위 디렉터리 이름. __init__.py 는 요구하지 않는다."""
    found = set()
    for child in ROOT.iterdir():
        if not child.is_dir() or child.name in _PACKAGE_SKIP or child.name.startswith("."):
            continue
        for path in child.rglob("*.py"):
            relative = path.relative_to(ROOT)
            if any(part in _PACKAGE_SKIP for part in relative.parts):
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            if any(isinstance(node, ast.Call) and _is_logging_getlogger(node) for node in ast.walk(tree)):
                found.add(child.name)
                break
    return found


def logging_sources() -> list[Path]:
    found = []
    for path in sorted(ROOT.rglob("*.py")):
        relative = path.relative_to(ROOT)
        if any(part in LOGGING_EXCLUDED_DIRS for part in relative.parts):
            continue
        found.append(path)
    return found


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


class LoggingSetupRuleTests(unittest.TestCase):
    def test_logging_setup_is_centralized(self):
        found = []
        for path in logging_sources():
            relative = path.relative_to(ROOT).as_posix()
            found.extend(logging_violations(path.read_text(encoding="utf-8"), relative))
        self.assertEqual(found, [], "\n".join(found + [SETUP_HINT]))

    def test_detector_flags_get_logger_and_basicconfig(self):
        source = (
            "from common.logger import get_logger\n"
            "logger = get_logger(\"kream.crawler\")\n"
            "logging.basicConfig()\n"
            "getLogger(__name__)\n"
        )
        found = logging_violations(source, "kream/crawler.py")
        self.assertIn("kream/crawler.py:1 get_logger import", found)
        self.assertIn("kream/crawler.py:2 get_logger", found)
        self.assertIn("kream/crawler.py:3 basicConfig", found)
        self.assertIn("kream/crawler.py:4 getLogger", found)
        configured = (
            "logging.dictConfig({})\n"
            "logging.fileConfig('log.cfg')\n"
        )
        configured_found = logging_violations(configured, "kream/crawler.py")
        self.assertIn("kream/crawler.py:1 dictConfig", configured_found)
        self.assertIn("kream/crawler.py:2 fileConfig", configured_found)

    def test_detector_accepts_module_logger_and_entrypoint_guard(self):
        module = "import logging\nlogger = logging.getLogger(__name__)\ntext = \"get_logger('x')\"\n"
        self.assertEqual(logging_violations(module, "kream/crawler.py"), [])
        named = "import logging\nlogger = logging.getLogger(\"kream.crawler\")\n"
        self.assertEqual(
            logging_violations(named, "kream/crawler.py"),
            ["kream/crawler.py:2 logging.getLogger"],
        )
        entry = (
            "import logging\n"
            "from common.logger import setup_logging\n"
            "logger = logging.getLogger(\"main\")\n"
            "def main():\n"
            "    return None\n"
            "if __name__ == \"__main__\":\n"
            "    setup_logging()\n"
            "    main()\n"
        )
        self.assertEqual(logging_violations(entry, "main.py"), [])
        outside = (
            "import logging\n"
            "from common.logger import setup_logging\n"
            "logger = logging.getLogger(\"main\")\n"
            "def main():\n"
            "    setup_logging()\n"
            "if __name__ == \"__main__\":\n"
            "    main()\n"
        )
        outside_found = logging_violations(outside, "main.py")
        self.assertTrue(any("밖에" in item for item in outside_found))
        self.assertTrue(any("0개" in item for item in outside_found))
        setup = (
            "import logging\n"
            "def setup_logging(log_dir=None):\n"
            "    logging.getLogger()\n"
            "    for name in names:\n"
            "        logging.getLogger(name)\n"
        )
        self.assertEqual(logging_violations(setup, "common/logger.py"), [])
        outside_handler = setup + "logging.getLogger().addHandler(logging.StreamHandler())\n"
        self.assertTrue(
            any("addHandler" in item for item in logging_violations(outside_handler, "common/logger.py"))
        )
        inside_handler = (
            "import logging\n"
            "def setup_logging(log_dir=None):\n"
            "    logging.getLogger()\n"
            "    for name in names:\n"
            "        logging.getLogger(name)\n"
            "    logging.getLogger().addHandler(logging.StreamHandler())\n"
        )
        self.assertEqual(logging_violations(inside_handler, "common/logger.py"), [])

    def test_project_loggers_match_packages_and_entrypoints(self):
        source = (ROOT / "common" / "logger.py").read_text(encoding="utf-8")
        declared = project_logger_names(source)
        expected = logged_top_level_packages() | set(ENTRY_LOGGER_NAMES.values())
        self.assertEqual(
            declared,
            expected,
            "PROJECT_LOGGERS 는 getLogger 를 쓰는 최상위 패키지와 엔트리포인트 이름의 합이어야 한다. "
            + SETUP_HINT,
        )

    def test_main_guard_requires_setup_logging_first(self):
        late = (
            "import logging\n"
            "from common.logger import setup_logging\n"
            "logger = logging.getLogger(\"main\")\n"
            "def parse_args():\n"
            "    return None\n"
            "if __name__ == \"__main__\":\n"
            "    parse_args()\n"
            "    setup_logging()\n"
        )
        found = logging_violations(late, "main.py")
        self.assertTrue(any("첫 문장" in item for item in found), found)


if __name__ == "__main__":
    unittest.main()
