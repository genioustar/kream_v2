"""setup_logging() 이 루트에만 핸들러를 붙이고, import 만으로는 logs/ 를 만들지 않는다."""
import io
import logging
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.test_rule_logging import logging_sources

ROOT = Path(__file__).resolve().parent.parent
EXPECTED_FORMAT = "[%(asctime)s] %(levelname)-8s %(name)s (%(filename)s:%(lineno)d) - %(message)s"
# mkdir 호출 자체만 센다. 저장소에 logs/ 가 이미 있어도, 테스트 import 순서와 무관하다.
_IMPORT_PROBE = """
import importlib
import logging
import os
import sys
from pathlib import Path

recorded = []

def _note(path):
    recorded.append(os.fspath(path))

def _path_mkdir(self, *args, **kwargs):
    _note(self)

def _os_mkdir(path, *args, **kwargs):
    _note(path)

def _os_makedirs(name, *args, **kwargs):
    _note(name)

Path.mkdir = _path_mkdir
os.mkdir = _os_mkdir
os.makedirs = _os_makedirs

for name in sys.argv[1:]:
    importlib.import_module(name)

logger_count = 0
for item in logging.Logger.manager.loggerDict.values():
    if isinstance(item, logging.Logger):
        logger_count += len(item.handlers)
logs_mkdirs = sum(1 for path in recorded if "logs" in Path(path).parts)
print("mkdirs", len(recorded))
print("logs_mkdirs", logs_mkdirs)
for path in recorded:
    print("mkdir_path", path)
print("root", len(logging.getLogger().handlers))
print("loggers", logger_count)
"""


def _product_module_names() -> list[str]:
    names = []
    for path in logging_sources():
        parts = list(path.relative_to(ROOT).with_suffix("").parts)
        if parts[-1] == "__init__":
            parts.pop()
        if parts:
            names.append(".".join(parts))
    return names


def _log_names(directory: Path) -> list[str] | None:
    if not directory.exists():
        return None
    return sorted(path.name for path in directory.iterdir())


class ImportProbeTests(unittest.TestCase):
    def test_import_does_not_attach_handlers(self):
        # cwd 는 저장소가 아니다. import 중 mkdir 은 자식이 호출 전에 가로채 개수만 출력한다.
        with tempfile.TemporaryDirectory() as cwd:
            result = subprocess.run(
                [sys.executable, "-c", _IMPORT_PROBE, *_product_module_names()],
                cwd=cwd,
                env={**os.environ, "PYTHONPATH": str(ROOT)},
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        detail = result.stdout + result.stderr
        fields = {}
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in {"mkdirs", "logs_mkdirs", "root", "loggers"}:
                fields[parts[0]] = int(parts[1])
        self.assertEqual(sorted(fields), ["loggers", "logs_mkdirs", "mkdirs", "root"], detail)
        self.assertEqual(fields["logs_mkdirs"], 0, detail)
        self.assertEqual(fields["mkdirs"], 0, detail)
        self.assertEqual(fields["root"], 0, detail)
        self.assertEqual(fields["loggers"], 0, detail)


class SetupLoggingTests(unittest.TestCase):
    def setUp(self):
        from common.logger import LOG_FORMAT, PROJECT_LOGGERS, setup_logging

        self._log_format = LOG_FORMAT
        self._project_loggers = PROJECT_LOGGERS
        self._setup_logging = setup_logging
        self._tmp = None
        self.root = logging.getLogger()
        self._saved_handlers = self.root.handlers[:]
        self._saved_level = self.root.level
        self._saved_levels = {
            name: logging.getLogger(name).level for name in self._project_loggers
        }
        for handler in self._saved_handlers:
            self.root.removeHandler(handler)
        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        try:
            for handler in self.root.handlers[:]:
                self.root.removeHandler(handler)
                handler.close()
            for handler in self._saved_handlers:
                self.root.addHandler(handler)
            self.root.setLevel(self._saved_level)
            for name, level in self._saved_levels.items():
                logging.getLogger(name).setLevel(level)
        finally:
            if self._tmp is not None:
                self._tmp.cleanup()

    def test_handlers_share_format_without_datefmt(self):
        self._setup_logging(Path(self._tmp.name))
        self.assertEqual(len(self.root.handlers), 2)
        self.assertEqual(self.root.level, logging.WARNING)
        console, file_handler = self._split_handlers()
        self.assertIs(console.formatter, file_handler.formatter)
        self.assertEqual(console.formatter._fmt, EXPECTED_FORMAT)
        self.assertIsNone(console.formatter.datefmt)
        self.assertEqual(self._log_format, EXPECTED_FORMAT)
        self.assertEqual(console.level, logging.INFO)
        self.assertEqual(file_handler.level, logging.DEBUG)
        for name in self._project_loggers:
            self.assertEqual(logging.getLogger(name).level, logging.DEBUG)

    def test_second_call_keeps_two_handlers(self):
        log_dir = Path(self._tmp.name)
        self._setup_logging(log_dir)
        handlers = list(self.root.handlers)
        other = log_dir / "other"
        other.mkdir()
        self._setup_logging(other)
        self.assertEqual(self.root.handlers, handlers)
        self.assertEqual(len(self.root.handlers), 2)
        self.assertEqual(list(other.iterdir()), [])

    def test_project_debug_is_filed_and_third_party_info_is_dropped(self):
        self._setup_logging(Path(self._tmp.name))
        console, file_handler = self._split_handlers()
        captured = io.StringIO()
        console.setStream(captured)
        self.assertEqual(logging.getLogger("kream.crawler").getEffectiveLevel(), logging.DEBUG)
        self.assertGreaterEqual(
            logging.getLogger("websockets").getEffectiveLevel(), logging.WARNING,
        )

        logging.getLogger("kream.crawler").debug("project-debug-marker")
        logging.getLogger("kream.crawler").info("project-info-marker")
        logging.getLogger("websockets").info("third-party-info-marker")

        file_handler.flush()
        logged = Path(file_handler.baseFilename).read_text(encoding="utf-8")
        shown = captured.getvalue()
        self.assertIn("project-debug-marker", logged)
        self.assertIn("project-info-marker", logged)
        self.assertNotIn("third-party-info-marker", logged)
        self.assertIn("project-info-marker", shown)
        self.assertNotIn("project-debug-marker", shown)
        self.assertNotIn("third-party-info-marker", shown)

    def test_log_dir_keeps_repository_logs_untouched(self):
        repository_logs = ROOT / "logs"
        before = _log_names(repository_logs)
        log_dir = Path(self._tmp.name)
        self._setup_logging(log_dir)
        _console, file_handler = self._split_handlers()
        log_path = Path(file_handler.baseFilename)
        self.assertTrue(log_path.is_relative_to(log_dir))
        self.assertRegex(log_path.name, r"^crawler_\d{8}\.log$")
        self.assertEqual(_log_names(repository_logs), before)

    def _split_handlers(self):
        console = [handler for handler in self.root.handlers if type(handler) is logging.StreamHandler]
        files = [handler for handler in self.root.handlers if isinstance(handler, logging.FileHandler)]
        self.assertEqual(len(console), 1)
        self.assertEqual(len(files), 1)
        return console[0], files[0]


if __name__ == "__main__":
    unittest.main()
