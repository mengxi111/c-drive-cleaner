import tempfile
import unittest
from pathlib import Path

from c_drive_cleaner import CleanupRule, ScanResult, clean_results, format_size, remove_nested_paths


class CleanerCoreTests(unittest.TestCase):
    def test_format_size(self):
        self.assertEqual(format_size(0), "0 B")
        self.assertEqual(format_size(1024), "1.00 KB")
        self.assertEqual(format_size(1024 * 1024), "1.00 MB")

    def test_remove_nested_paths_keeps_only_parent(self):
        parent = Path("C:/cache")
        nested = parent / "nested"
        self.assertEqual(remove_nested_paths([nested, parent]), [parent])

    def test_clean_results_keeps_protected_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            child = root / "child"
            child.mkdir()
            cached_file = child / "cache.tmp"
            cached_file.write_bytes(b"cache")

            rule = CleanupRule(key="test", name="test", description="test", paths=(root,))
            scan = ScanResult(
                rule=rule,
                files=[cached_file],
                directories=[child, root],
                size=cached_file.stat().st_size,
            )

            result = clean_results([scan])

            self.assertEqual(result.removed_files, 1)
            self.assertFalse(cached_file.exists())
            self.assertFalse(child.exists())
            self.assertTrue(root.exists())


if __name__ == "__main__":
    unittest.main()
