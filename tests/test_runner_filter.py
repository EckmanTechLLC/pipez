import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "infra", "runner"))
from pipez_runner import filter_paths, is_ignored  # noqa: E402


class RunnerFilterTest(unittest.TestCase):
    def test_pyc_filtered(self):
        self.assertEqual(filter_paths(["tests/__pycache__/t.cpython-312.pyc", "a.pyc"]), [])

    def test_source_kept(self):
        self.assertEqual(filter_paths(["src/x.py"]), ["src/x.py"])

    def test_pipez_dir_filtered(self):
        self.assertEqual(filter_paths([".pipez/result.json", "src/x.py"]), ["src/x.py"])

    def test_symlink_names_ignored(self):
        self.assertTrue(is_ignored(".venv"))
        self.assertTrue(is_ignored("node_modules"))
        self.assertTrue(is_ignored("?? .venv"))
        self.assertFalse(is_ignored("app/.venvx.py"))


if __name__ == "__main__":
    unittest.main()
