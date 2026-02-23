"""Tests for disk_utils shared utilities."""

from unittest.mock import MagicMock, patch

from django.test import TestCase

from apps.checkers.checkers.disk_utils import (
    dir_size,
    find_large_files,
    find_old_files,
    scan_directory,
)


class ScanDirectoryTests(TestCase):
    """Tests for the scan_directory function."""

    @patch("apps.checkers.checkers.disk_utils.dir_size", return_value=10 * 1024 * 1024)
    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_scan_directory_normal_with_dir_and_file(self, mock_isdir, mock_scandir, mock_dir_size):
        """Normal operation: scans directories and files, returns sorted results."""
        mock_dir_entry = MagicMock()
        mock_dir_entry.is_dir.return_value = True
        mock_dir_entry.path = "/tmp/subdir"

        mock_file_entry = MagicMock()
        mock_file_entry.is_dir.return_value = False
        mock_file_entry.path = "/tmp/bigfile.bin"
        mock_file_entry.stat.return_value = MagicMock(st_size=5 * 1024 * 1024)

        mock_scandir.return_value.__enter__ = MagicMock(
            return_value=iter([mock_dir_entry, mock_file_entry])
        )
        mock_scandir.return_value.__exit__ = MagicMock(return_value=False)

        results = scan_directory("/tmp")

        self.assertEqual(len(results), 2)
        # Sorted descending by size: 10 MB dir first, 5 MB file second
        self.assertEqual(results[0]["path"], "/tmp/subdir")
        self.assertAlmostEqual(results[0]["size_mb"], 10.0, places=1)
        self.assertEqual(results[1]["path"], "/tmp/bigfile.bin")
        self.assertAlmostEqual(results[1]["size_mb"], 5.0, places=1)

    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=False)
    def test_scan_directory_not_a_directory(self, mock_isdir):
        """Returns empty list when path is not a directory."""
        results = scan_directory("/nonexistent")
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_scan_directory_permission_error_on_scandir(self, mock_isdir, mock_scandir):
        """Returns empty list when scandir raises PermissionError."""
        mock_scandir.return_value.__enter__ = MagicMock(side_effect=PermissionError)
        mock_scandir.return_value.__exit__ = MagicMock(return_value=False)
        # PermissionError on context manager entry
        mock_scandir.side_effect = PermissionError("denied")

        results = scan_directory("/secret")
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_scan_directory_entry_permission_error_skips_entry(self, mock_isdir, mock_scandir):
        """Skips individual entries that raise PermissionError."""
        bad_entry = MagicMock()
        bad_entry.is_dir.side_effect = PermissionError("denied")

        mock_scandir.return_value.__enter__ = MagicMock(return_value=iter([bad_entry]))
        mock_scandir.return_value.__exit__ = MagicMock(return_value=False)

        results = scan_directory("/tmp")
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_scan_directory_filters_small_entries(self, mock_isdir, mock_scandir):
        """Entries smaller than 1 MB are filtered out."""
        small_file = MagicMock()
        small_file.is_dir.return_value = False
        small_file.path = "/tmp/tiny.txt"
        small_file.stat.return_value = MagicMock(st_size=100)  # 100 bytes

        mock_scandir.return_value.__enter__ = MagicMock(return_value=iter([small_file]))
        mock_scandir.return_value.__exit__ = MagicMock(return_value=False)

        results = scan_directory("/tmp")
        self.assertEqual(results, [])


class FindOldFilesTests(TestCase):
    """Tests for the find_old_files function."""

    @patch("apps.checkers.checkers.disk_utils.dir_size", return_value=20 * 1024 * 1024)
    @patch("apps.checkers.checkers.disk_utils.time.time")
    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_old_files_normal(self, mock_isdir, mock_scandir, mock_time, mock_dir_size):
        """Finds files older than max_age_days."""
        now = 1_000_000.0
        mock_time.return_value = now
        old_mtime = now - (10 * 86400)  # 10 days old

        old_file = MagicMock()
        old_file.is_dir.return_value = False
        old_file.path = "/tmp/old_file.log"
        old_file.stat.return_value = MagicMock(st_size=2 * 1024 * 1024, st_mtime=old_mtime)

        mock_scandir.return_value.__enter__ = MagicMock(return_value=iter([old_file]))
        mock_scandir.return_value.__exit__ = MagicMock(return_value=False)

        results = find_old_files("/tmp", max_age_days=7)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "/tmp/old_file.log")
        self.assertEqual(results[0]["age_days"], 10)

    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=False)
    def test_find_old_files_not_a_directory(self, mock_isdir):
        """Returns empty list when path is not a directory."""
        results = find_old_files("/nonexistent")
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_old_files_permission_error(self, mock_isdir, mock_scandir):
        """Returns empty list when scandir raises PermissionError."""
        mock_scandir.side_effect = PermissionError("denied")

        results = find_old_files("/secret")
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.dir_size", return_value=5 * 1024 * 1024)
    @patch("apps.checkers.checkers.disk_utils.time.time")
    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_old_files_old_directory(self, mock_isdir, mock_scandir, mock_time, mock_dir_size):
        """Finds old directories and uses dir_size for their size."""
        now = 1_000_000.0
        mock_time.return_value = now
        old_mtime = now - (15 * 86400)  # 15 days old

        old_dir = MagicMock()
        old_dir.is_dir.return_value = True
        old_dir.path = "/tmp/old_dir"
        old_dir.stat.return_value = MagicMock(st_size=0, st_mtime=old_mtime)

        mock_scandir.return_value.__enter__ = MagicMock(return_value=iter([old_dir]))
        mock_scandir.return_value.__exit__ = MagicMock(return_value=False)

        results = find_old_files("/tmp", max_age_days=7)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "/tmp/old_dir")
        mock_dir_size.assert_called_once_with("/tmp/old_dir", timeout=None)

    @patch("apps.checkers.checkers.disk_utils.time.time")
    @patch("apps.checkers.checkers.disk_utils.os.scandir")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_old_files_entry_permission_error(self, mock_isdir, mock_scandir, mock_time):
        """Skips entries that raise PermissionError on stat."""
        mock_time.return_value = 1_000_000.0

        bad_entry = MagicMock()
        bad_entry.stat.side_effect = PermissionError("denied")

        mock_scandir.return_value.__enter__ = MagicMock(return_value=iter([bad_entry]))
        mock_scandir.return_value.__exit__ = MagicMock(return_value=False)

        results = find_old_files("/tmp")
        self.assertEqual(results, [])


class FindLargeFilesTests(TestCase):
    """Tests for the find_large_files function."""

    @patch("apps.checkers.checkers.disk_utils.os.path.getsize")
    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=False)
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_large_files_normal(self, mock_isdir, mock_walk, mock_islink, mock_getsize):
        """Finds files larger than min_size_mb."""
        mock_walk.return_value = [("/data", [], ["big.iso", "small.txt"])]
        mock_getsize.side_effect = lambda fp: (200 * 1024 * 1024 if "big" in fp else 50)

        results = find_large_files("/data", min_size_mb=100.0)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["path"], "/data/big.iso")

    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=False)
    def test_find_large_files_not_a_directory(self, mock_isdir):
        """Returns empty list when path is not a directory."""
        results = find_large_files("/nonexistent")
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.time.monotonic")
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_large_files_timeout_at_directory_level(
        self, mock_isdir, mock_walk, mock_monotonic
    ):
        """Stops scanning when timeout expires at directory level."""
        # First call sets deadline, second call exceeds it
        mock_monotonic.side_effect = [0.0, 10.0]
        mock_walk.return_value = [
            ("/data/dir1", [], ["file1.bin"]),
            ("/data/dir2", [], ["file2.bin"]),
        ]

        results = find_large_files("/data", timeout=5.0)
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=False)
    @patch("apps.checkers.checkers.disk_utils.os.path.getsize")
    @patch("apps.checkers.checkers.disk_utils.time.monotonic")
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_large_files_timeout_at_file_level(
        self, mock_isdir, mock_walk, mock_monotonic, mock_getsize, mock_islink
    ):
        """Stops scanning when timeout expires at file level."""
        # monotonic calls: deadline calc, dir check, file1 check, file2 check (exceeds)
        mock_monotonic.side_effect = [0.0, 1.0, 2.0, 10.0]
        mock_walk.return_value = [("/data", [], ["a.bin", "b.bin"])]
        mock_getsize.return_value = 200 * 1024 * 1024

        results = find_large_files("/data", timeout=5.0, min_size_mb=100.0)
        # Should only get a.bin before timeout on b.bin
        self.assertEqual(len(results), 1)

    @patch("apps.checkers.checkers.disk_utils.os.walk")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_large_files_exclude_paths(self, mock_isdir, mock_walk):
        """Excluded paths are pruned from the walk."""
        mock_walk.return_value = [
            ("/data/cache", ["subdir"], ["file.bin"]),
        ]

        results = find_large_files("/data", exclude_paths={"/data/cache"})
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=True)
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_large_files_skips_symlinks(self, mock_isdir, mock_walk, mock_islink):
        """Symlinks are skipped."""
        mock_walk.return_value = [("/data", [], ["link.bin"])]

        results = find_large_files("/data", min_size_mb=1.0)
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.path.getsize")
    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=False)
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_large_files_file_permission_error(
        self, mock_isdir, mock_walk, mock_islink, mock_getsize
    ):
        """Skips files that raise PermissionError on getsize."""
        mock_walk.return_value = [("/data", [], ["secret.bin"])]
        mock_getsize.side_effect = PermissionError("denied")

        results = find_large_files("/data", min_size_mb=1.0)
        self.assertEqual(results, [])

    @patch("apps.checkers.checkers.disk_utils.os.walk")
    @patch("apps.checkers.checkers.disk_utils.os.path.isdir", return_value=True)
    def test_find_large_files_walk_permission_error(self, mock_isdir, mock_walk):
        """Returns empty list when os.walk raises PermissionError."""
        mock_walk.side_effect = PermissionError("denied")

        results = find_large_files("/secret")
        self.assertEqual(results, [])


class DirSizeTests(TestCase):
    """Tests for the dir_size function."""

    @patch("apps.checkers.checkers.disk_utils.os.path.getsize")
    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=False)
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    def test_dir_size_normal(self, mock_walk, mock_islink, mock_getsize):
        """Calculates total size of directory recursively."""
        mock_walk.return_value = [
            ("/dir", ["sub"], ["a.txt", "b.txt"]),
            ("/dir/sub", [], ["c.txt"]),
        ]
        mock_getsize.return_value = 1000

        result = dir_size("/dir")
        self.assertEqual(result, 3000)

    @patch("apps.checkers.checkers.disk_utils.time.monotonic")
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    def test_dir_size_timeout_at_directory_level(self, mock_walk, mock_monotonic):
        """Stops when timeout expires at directory level."""
        mock_monotonic.side_effect = [0.0, 10.0]
        mock_walk.return_value = [
            ("/dir", [], ["a.txt"]),
            ("/dir/sub", [], ["b.txt"]),
        ]

        result = dir_size("/dir", timeout=5.0)
        self.assertEqual(result, 0)

    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=False)
    @patch("apps.checkers.checkers.disk_utils.os.path.getsize")
    @patch("apps.checkers.checkers.disk_utils.time.monotonic")
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    def test_dir_size_timeout_at_file_level(
        self, mock_walk, mock_monotonic, mock_getsize, mock_islink
    ):
        """Stops when timeout expires at file level."""
        # monotonic calls: deadline calc, dir check, file1 check, file2 check (exceeds)
        mock_monotonic.side_effect = [0.0, 1.0, 2.0, 10.0]
        mock_walk.return_value = [("/dir", [], ["a.txt", "b.txt"])]
        mock_getsize.return_value = 500

        result = dir_size("/dir", timeout=5.0)
        # Only first file counted before timeout on second
        self.assertEqual(result, 500)

    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=True)
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    def test_dir_size_skips_symlinks(self, mock_walk, mock_islink):
        """Symlinks are skipped and not counted."""
        mock_walk.return_value = [("/dir", [], ["link.txt"])]

        result = dir_size("/dir")
        self.assertEqual(result, 0)

    @patch("apps.checkers.checkers.disk_utils.os.path.getsize")
    @patch("apps.checkers.checkers.disk_utils.os.path.islink", return_value=False)
    @patch("apps.checkers.checkers.disk_utils.os.walk")
    def test_dir_size_permission_error_on_file(self, mock_walk, mock_islink, mock_getsize):
        """Skips files that raise PermissionError."""
        mock_walk.return_value = [("/dir", [], ["secret.txt"])]
        mock_getsize.side_effect = PermissionError("denied")

        result = dir_size("/dir")
        self.assertEqual(result, 0)

    @patch("apps.checkers.checkers.disk_utils.os.walk")
    def test_dir_size_walk_permission_error(self, mock_walk):
        """Returns 0 when os.walk raises PermissionError."""
        mock_walk.side_effect = PermissionError("denied")

        result = dir_size("/secret")
        self.assertEqual(result, 0)
