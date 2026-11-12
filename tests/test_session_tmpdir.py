"""
.claude/hooks/session-tmpdir.py 회귀 테스트.

prepare 가 없는 루트를 만들고, CLAUDE_ENV_FILE 에 export TMPDIR 줄을 한 번만 넣는지 확인한다.
main 을 subprocess 로 돌리지 않는다. main 은 실제 ALLOWED_ROOTS[0] 를 만들어
CI 에서 그 경로를 건드리거나 권한 때문에 실패할 수 있다.
"""
import importlib.util
import io
import os
import shlex
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

HOOK = Path(__file__).resolve().parent.parent / ".claude" / "hooks" / "session-tmpdir.py"


def load_hook():
    """하이픈이 있는 훅 파일을 모듈로 불러 prepare 를 호출한다."""
    spec = importlib.util.spec_from_file_location("session_tmpdir", HOOK)
    if spec is None or spec.loader is None:
        raise AssertionError(f"훅 모듈을 불러오지 못함: {HOOK}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PrepareTests(unittest.TestCase):
    def test_prepare_creates_missing_root_and_one_export_line(self):
        hook = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "nested" / "scratch-root"
            env_file = base / "claude.env"
            self.assertFalse(root.exists())
            hook.prepare(str(root), str(env_file))
            self.assertTrue(root.is_dir())
            line = "export TMPDIR=" + shlex.quote(str(root))
            self.assertEqual(env_file.read_text(encoding="utf-8"), line + "\n")

    def test_prepare_appends_export_only_once(self):
        hook = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "scratch root"
            env_file = base / "claude.env"
            hook.prepare(str(root), str(env_file))
            hook.prepare(str(root), str(env_file))
            line = "export TMPDIR=" + shlex.quote(str(root))
            text = env_file.read_text(encoding="utf-8")
            self.assertEqual(text, line + "\n")
            self.assertEqual(text.count(line), 1)
            self.assertIn("'", line)

    def test_prepare_without_env_file_creates_only_root(self):
        hook = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "scratch-root"
            hook.prepare(str(root), None)
            self.assertTrue(root.is_dir())
            self.assertEqual(list(base.iterdir()), [root])

    def test_prepare_swallows_env_file_write_error(self):
        hook = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            root = base / "scratch-root"
            env_dir = base / "not-a-file"
            env_dir.mkdir()
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                hook.prepare(str(root), str(env_dir))
            self.assertTrue(root.is_dir())
            self.assertIn("session-tmpdir:", stderr.getvalue())

    def test_root_path_is_not_copied_into_session_hook(self):
        text = HOOK.read_text(encoding="utf-8")
        self.assertNotIn("claude-501", text)

    def test_main_without_env_file_returns_zero(self):
        # subprocess 로 main 을 돌리면 실제 ALLOWED_ROOTS[0] 가 생긴다. 루트만 임시 경로로 바꾼다.
        hook = load_hook()
        with tempfile.TemporaryDirectory() as tmp:
            root = str(Path(tmp) / "scratch-root")
            environ = os.environ.copy()
            environ.pop("CLAUDE_ENV_FILE", None)
            with mock.patch.object(hook, "allowed_root", return_value=root), mock.patch.dict(os.environ, environ, clear=True):
                self.assertEqual(hook.main(), 0)
            self.assertTrue(Path(root).is_dir())

    def test_main_fail_open_when_root_lookup_fails(self):
        hook = load_hook()
        stderr = io.StringIO()
        with mock.patch.object(hook, "allowed_root", side_effect=RuntimeError("불러오기 실패")), redirect_stderr(stderr):
            self.assertEqual(hook.main(), 0)
        self.assertIn("session-tmpdir:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
