"""write_json_atomic: 정상 저장, 권한, 실패 시 기존 파일 보존, 덮어쓰기."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

from common.atomic_io import write_json_atomic

EXISTING_FILE_MODE = 0o640


def _process_umask() -> int:
    previous = os.umask(0)
    try:
        return previous
    finally:
        os.umask(previous)


class AtomicIoTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def test_write_matches_json_dumps(self):
        payload = {"name": "상품", "price": 12000, "tags": ["세일", "신발"]}
        path = self.directory / "products.json"
        write_json_atomic(path, payload)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_serialization_failure_keeps_existing_file_and_removes_temp(self):
        path = self.directory / "products.json"
        original = '{"kept": true}\n'
        path.write_text(original, encoding="utf-8")
        with self.assertRaises(TypeError):
            write_json_atomic(path, {"bad": {1, 2}})
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_overwrite_replaces_previous_content(self):
        path = self.directory / "products.json"
        write_json_atomic(path, {"version": 1, "name": "이전"})
        write_json_atomic(path, {"version": 2, "name": "덮어씀"})
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            {"version": 2, "name": "덮어씀"},
        )
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps({"version": 2, "name": "덮어씀"}, ensure_ascii=False, indent=2),
        )
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_new_file_mode_matches_default_umask(self):
        path = self.directory / "products.json"
        write_json_atomic(path, {"name": "상품"})
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o666 & ~_process_umask())
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_overwrite_keeps_existing_mode(self):
        path = self.directory / "products.json"
        path.write_text('{"version": 1}', encoding="utf-8")
        os.chmod(path, EXISTING_FILE_MODE)
        payload = {"version": 2, "name": "덮어씀"}
        write_json_atomic(path, payload)
        self.assertNotEqual(EXISTING_FILE_MODE, 0o666 & ~_process_umask())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), EXISTING_FILE_MODE)
        self.assertEqual(
            path.read_text(encoding="utf-8"),
            json.dumps(payload, ensure_ascii=False, indent=2),
        )
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_replace_failure_on_directory_propagates_and_removes_temp(self):
        target = self.directory / "products.json"
        target.mkdir()
        with self.assertRaises(IsADirectoryError):
            write_json_atomic(target, {"name": "상품"})
        self.assertTrue(target.is_dir())
        self.assertEqual(list(target.iterdir()), [])
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

    def test_missing_parent_raises_without_creating_directory(self):
        parent = self.directory / "missing"
        path = parent / "products.json"
        with self.assertRaises(FileNotFoundError):
            write_json_atomic(path, {"name": "상품"})
        self.assertFalse(parent.exists())
        self.assertEqual(list(self.directory.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
