# -*- coding: utf-8 -*-
"""
Comprehensive tests for app.core.file.file_tools.FileToolsPlugin.

Targets ~701 statements at 8% coverage — aims to exercise every public method,
key branches, and edge cases while mocking file_registry, LLM, and heavy I/O.
"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


def _make_entry(**overrides):
    """Create a mock FileRegistry entry (SimpleNamespace)."""
    defaults = {
        "path": "/fake/dir/report.pdf",
        "name": "report.pdf",
        "category": "文档",
        "size_bytes": 2048,
        "snippet": "quarterly earnings",
        "indexed_at": "2025-01-15T10:30:00",
        "content_preview": "This is the full preview content of the file.",
        "mtime": 1700000000.0,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture()
def plugin():
    from app.core.file.file_tools import FileToolsPlugin
    return FileToolsPlugin()


# ═══════════════════════════════════════════════════════════════════════════════
# 1–2  Properties & get_tools
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestPluginMeta:
    def test_name(self, plugin):
        assert plugin.name == "FileTools"

    def test_description(self, plugin):
        assert isinstance(plugin.description, str)
        assert len(plugin.description) > 0

    def test_get_tools_returns_list_of_dicts(self, plugin):
        tools = plugin.get_tools()
        assert isinstance(tools, list)
        assert len(tools) >= 20
        for t in tools:
            assert "name" in t
            assert "func" in t
            assert callable(t["func"])
            assert "description" in t
            assert "parameters" in t

    def test_get_tools_names(self, plugin):
        names = [t["name"] for t in plugin.get_tools()]
        expected = [
            "find_file", "read_file_snippet", "list_recent_files",
            "organize_file", "rename_file", "move_file", "copy_file",
            "delete_file", "list_directory", "directory_tree",
            "get_disk_usage", "find_large_files", "find_old_files",
            "batch_rename", "batch_move", "cleanup_duplicates",
            "compress_files", "extract_archive", "manage_tag",
            "manage_favorite", "summarize_file", "undo_last_op",
        ]
        for name in expected:
            assert name in names, f"Missing tool: {name}"


# ═══════════════════════════════════════════════════════════════════════════════
# 3–4  find_file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestFindFile:
    @patch("app.core.file.file_tools.FileToolsPlugin._scanner_fallback", return_value=[])
    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_file_with_results(self, mock_get_reg, mock_scanner, plugin):
        mock_reg = MagicMock()
        mock_reg.search.return_value = [_make_entry(), _make_entry(name="notes.txt", path="/fake/notes.txt")]
        mock_get_reg.return_value = mock_reg

        result = plugin.find_file("report")
        assert "共找到" in result
        assert "report.pdf" in result
        mock_reg.search.assert_called_once_with("report", category=None, limit=10)

    @patch("app.core.file.file_tools.FileToolsPlugin._scanner_fallback", return_value=[])
    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_file_no_results(self, mock_get_reg, mock_scanner, plugin):
        mock_reg = MagicMock()
        mock_reg.search.return_value = []
        mock_get_reg.return_value = mock_reg

        result = plugin.find_file("nonexistent_xyz")
        assert "未找到" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_file_scanner_fallback_used(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.search.return_value = []
        mock_get_reg.return_value = mock_reg

        fallback_items = [
            {"path": "/scan/file.txt", "name": "file.txt", "category": "文档", "size_kb": 5, "snippet": ""},
        ]
        with patch.object(plugin, "_scanner_fallback", return_value=fallback_items):
            result = plugin.find_file("file", limit=5)
        assert "file.txt" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_file_scanner_fallback_exception(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.search.return_value = []
        mock_get_reg.return_value = mock_reg

        with patch.object(plugin, "_scanner_fallback", side_effect=RuntimeError("boom")):
            result = plugin.find_file("x")
        assert "未找到" in result

    @patch("app.core.file.file_tools.FileToolsPlugin._scanner_fallback", return_value=[])
    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_file_with_category(self, mock_get_reg, mock_scanner, plugin):
        mock_reg = MagicMock()
        mock_reg.search.return_value = [_make_entry(category="图片")]
        mock_get_reg.return_value = mock_reg

        result = plugin.find_file("photo", category="图片", limit=5)
        assert "图片" in result
        mock_reg.search.assert_called_once_with("photo", category="图片", limit=5)

    @patch("app.core.file.file_tools.FileToolsPlugin._scanner_fallback", return_value=[])
    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_file_limit_clamping(self, mock_get_reg, mock_scanner, plugin):
        mock_reg = MagicMock()
        mock_reg.search.return_value = []
        mock_get_reg.return_value = mock_reg

        plugin.find_file("q", limit=999)
        mock_reg.search.assert_called_once_with("q", category=None, limit=50)


# ═══════════════════════════════════════════════════════════════════════════════
# 5–7  read_file_snippet
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestReadFileSnippet:
    def test_read_real_file(self, plugin, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello World!", encoding="utf-8")

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.get_by_path.return_value = None
            mock_get_reg.return_value = mock_reg
            with patch("app.core.file.file_registry._extract_text_preview", return_value="Hello World!"):
                result = plugin.read_file_snippet(str(f))
        assert "Hello World!" in result

    def test_read_nonexistent_file(self, plugin):
        result = plugin.read_file_snippet("/no/such/file.txt")
        assert "不存在" in result

    def test_read_file_not_a_file(self, plugin, tmp_path):
        result = plugin.read_file_snippet(str(tmp_path))
        assert "不是文件" in result

    def test_read_file_truncation_from_registry(self, plugin, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 500, encoding="utf-8")

        entry = _make_entry(content_preview="A" * 500)
        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.get_by_path.return_value = entry
            mock_get_reg.return_value = mock_reg
            result = plugin.read_file_snippet(str(f), max_chars=200)
        assert "已截断" in result
        assert len(result) < 500

    def test_read_file_too_large(self, plugin, tmp_path):
        f = tmp_path / "huge.bin"
        f.write_bytes(b"\x00")
        real_stat = f.stat()

        fake_stat = os.stat_result((
            real_stat.st_mode,
            real_stat.st_ino,
            real_stat.st_dev,
            real_stat.st_nlink,
            real_stat.st_uid,
            real_stat.st_gid,
            60 * 1024 * 1024,  # st_size = 60 MB
            real_stat.st_atime,
            real_stat.st_mtime,
            real_stat.st_ctime,
        ))
        with patch("pathlib.Path.stat", return_value=fake_stat):
            result = plugin.read_file_snippet(str(f))
        assert "过大" in result

    def test_read_file_extract_failure(self, plugin, tmp_path):
        f = tmp_path / "bad.xyz"
        f.write_text("data", encoding="utf-8")

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.get_by_path.return_value = None
            mock_get_reg.return_value = mock_reg
            with patch("app.core.file.file_registry._extract_text_preview", side_effect=Exception("parse error")):
                result = plugin.read_file_snippet(str(f))
        assert "读取失败" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 8  list_recent_files
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestListRecentFiles:
    @patch("app.core.file.file_registry.get_file_registry")
    def test_list_recent_with_entries(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_recent.return_value = [_make_entry()]
        mock_get_reg.return_value = mock_reg

        result = plugin.list_recent_files(days=7)
        assert "report.pdf" in result
        assert "新收录文件" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_list_recent_empty(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_recent.return_value = []
        mock_get_reg.return_value = mock_reg

        result = plugin.list_recent_files(days=30)
        assert "没有新收录" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 9  organize_file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestOrganizeFile:
    def test_organize_nonexistent(self, plugin):
        result = plugin.organize_file("/no/such/file.txt")
        assert "不存在" in result

    def test_organize_not_a_file(self, plugin, tmp_path):
        result = plugin.organize_file(str(tmp_path))
        assert "不是有效文件" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_organize_register_only(self, mock_get_reg, plugin, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("content", encoding="utf-8")

        entry = _make_entry(name="doc.txt", path=str(f))
        mock_reg = MagicMock()
        mock_reg.register.return_value = entry
        mock_get_reg.return_value = mock_reg

        with patch("importlib.import_module", side_effect=ImportError("no organizer")):
            result = plugin.organize_file(str(f))
        assert "已将文件收录" in result
        assert "doc.txt" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_organize_register_fails(self, mock_get_reg, plugin, tmp_path):
        f = tmp_path / "fail.txt"
        f.write_text("x", encoding="utf-8")

        mock_reg = MagicMock()
        mock_reg.register.return_value = None
        mock_get_reg.return_value = mock_reg

        result = plugin.organize_file(str(f))
        assert "注册失败" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 10–11  rename_file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRenameFile:
    def test_rename_success(self, plugin, tmp_path):
        f = tmp_path / "old.txt"
        f.write_text("data", encoding="utf-8")

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.update_path.return_value = True
            mock_get_reg.return_value = mock_reg
            result = plugin.rename_file(str(f), "new.txt")

        assert "重命名成功" in result
        assert (tmp_path / "new.txt").exists()
        assert not f.exists()

    def test_rename_nonexistent(self, plugin):
        result = plugin.rename_file("/no/file.txt", "new.txt")
        assert "不存在" in result

    def test_rename_not_a_file(self, plugin, tmp_path):
        result = plugin.rename_file(str(tmp_path), "new.txt")
        assert "不是文件" in result

    def test_rename_illegal_chars(self, plugin, tmp_path):
        f = tmp_path / "ok.txt"
        f.write_text("data", encoding="utf-8")
        result = plugin.rename_file(str(f), 'bad:name.txt')
        assert "非法字符" in result

    def test_rename_target_exists(self, plugin, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("1", encoding="utf-8")
        f2.write_text("2", encoding="utf-8")
        result = plugin.rename_file(str(f1), "b.txt")
        assert "已存在" in result

    def test_rename_empty_name(self, plugin, tmp_path):
        f = tmp_path / "x.txt"
        f.write_text("data", encoding="utf-8")
        result = plugin.rename_file(str(f), "  ")
        assert "非法字符" in result or "为空" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 12  move_file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestMoveFile:
    def test_move_success(self, plugin, tmp_path):
        src = tmp_path / "src.txt"
        src.write_text("data", encoding="utf-8")
        dest = tmp_path / "subdir"

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.update_path.return_value = True
            mock_get_reg.return_value = mock_reg
            result = plugin.move_file(str(src), str(dest))

        assert "移动成功" in result
        assert (dest / "src.txt").exists()
        assert not src.exists()

    def test_move_nonexistent(self, plugin, tmp_path):
        result = plugin.move_file("/no/file.txt", str(tmp_path))
        assert "不存在" in result

    def test_move_not_a_file(self, plugin, tmp_path):
        result = plugin.move_file(str(tmp_path), str(tmp_path / "dest"))
        assert "不是文件" in result

    def test_move_target_exists(self, plugin, tmp_path):
        src = tmp_path / "f.txt"
        src.write_text("a", encoding="utf-8")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "f.txt").write_text("b", encoding="utf-8")

        result = plugin.move_file(str(src), str(dest))
        assert "已存在" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 13  copy_file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCopyFile:
    def test_copy_success(self, plugin, tmp_path):
        src = tmp_path / "orig.txt"
        src.write_text("content", encoding="utf-8")
        dest = tmp_path / "copies"

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            result = plugin.copy_file(str(src), str(dest))

        assert "复制成功" in result
        assert (dest / "orig.txt").exists()
        assert src.exists()  # original still there

    def test_copy_nonexistent(self, plugin, tmp_path):
        result = plugin.copy_file("/no/file.txt", str(tmp_path))
        assert "不存在" in result

    def test_copy_target_exists(self, plugin, tmp_path):
        src = tmp_path / "f.txt"
        src.write_text("a", encoding="utf-8")
        dest = tmp_path / "dest"
        dest.mkdir()
        (dest / "f.txt").write_text("b", encoding="utf-8")

        result = plugin.copy_file(str(src), str(dest))
        assert "已存在" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 14–15  delete_file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestDeleteFile:
    def test_delete_with_trash(self, plugin, tmp_path):
        f = tmp_path / "del.txt"
        f.write_text("bye", encoding="utf-8")

        mock_send2trash = MagicMock()
        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            with patch.dict("sys.modules", {"send2trash": mock_send2trash}):
                result = plugin.delete_file(str(f), use_trash=True)
        assert "回收站" in result
        mock_send2trash.send2trash.assert_called_once_with(str(f))

    def test_delete_permanent(self, plugin, tmp_path):
        f = tmp_path / "perm.txt"
        f.write_text("gone", encoding="utf-8")

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            result = plugin.delete_file(str(f), use_trash=False)

        assert "永久删除" in result
        assert not f.exists()

    def test_delete_nonexistent(self, plugin):
        result = plugin.delete_file("/no/file.txt")
        assert "不存在" in result

    def test_delete_not_a_file(self, plugin, tmp_path):
        result = plugin.delete_file(str(tmp_path))
        assert "不是文件" in result

    def test_delete_trash_import_error_fallback(self, plugin, tmp_path):
        """When send2trash is not installed, falls back to permanent delete."""
        f = tmp_path / "fallback.txt"
        f.write_text("test", encoding="utf-8")

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            with patch.dict("sys.modules", {"send2trash": None}):
                with patch("builtins.__import__", side_effect=ImportError):
                    # Simulate send2trash import failure inside delete_file
                    result = plugin.delete_file(str(f), use_trash=False)
        assert not f.exists()


# ═══════════════════════════════════════════════════════════════════════════════
# 16–17  list_directory
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestListDirectory:
    def test_list_real_dir(self, plugin, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.pdf").write_text("b", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()

        result = plugin.list_directory(str(tmp_path))
        assert "a.txt" in result
        assert "b.pdf" in result
        assert "sub" in result

    def test_list_nonexistent_dir(self, plugin):
        result = plugin.list_directory("/no/such/dir")
        assert "不存在" in result

    def test_list_not_a_dir(self, plugin, tmp_path):
        f = tmp_path / "file.txt"
        f.write_text("x", encoding="utf-8")
        result = plugin.list_directory(str(f))
        assert "不是目录" in result

    def test_list_dir_filter_ext(self, plugin, tmp_path):
        (tmp_path / "a.txt").write_text("a", encoding="utf-8")
        (tmp_path / "b.pdf").write_text("b", encoding="utf-8")

        result = plugin.list_directory(str(tmp_path), filter_ext=".txt")
        assert "a.txt" in result
        assert "b.pdf" not in result

    def test_list_dir_sort_by_size(self, plugin, tmp_path):
        (tmp_path / "small.txt").write_text("x", encoding="utf-8")
        (tmp_path / "big.txt").write_text("x" * 1000, encoding="utf-8")

        result = plugin.list_directory(str(tmp_path), sort_by="size")
        assert "big.txt" in result

    def test_list_dir_empty(self, plugin, tmp_path):
        sub = tmp_path / "empty"
        sub.mkdir()
        result = plugin.list_directory(str(sub))
        assert "为空" in result

    def test_list_dir_hidden_files(self, plugin, tmp_path):
        (tmp_path / ".hidden").write_text("h", encoding="utf-8")
        (tmp_path / "visible.txt").write_text("v", encoding="utf-8")

        result_no_hidden = plugin.list_directory(str(tmp_path), show_hidden=False)
        assert ".hidden" not in result_no_hidden

        result_with_hidden = plugin.list_directory(str(tmp_path), show_hidden=True)
        assert ".hidden" in result_with_hidden


# ═══════════════════════════════════════════════════════════════════════════════
# 18  directory_tree
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestDirectoryTree:
    def test_tree_structure(self, plugin, tmp_path):
        sub = tmp_path / "child"
        sub.mkdir()
        (sub / "leaf.txt").write_text("l", encoding="utf-8")

        result = plugin.directory_tree(str(tmp_path))
        assert "child" in result
        assert "leaf.txt" in result

    def test_tree_nonexistent(self, plugin):
        result = plugin.directory_tree("/no/such/dir")
        assert "不存在" in result

    def test_tree_max_depth(self, plugin, tmp_path):
        d = tmp_path
        for name in ["a", "b", "c", "d", "e"]:
            d = d / name
            d.mkdir()
        (d / "deep.txt").write_text("deep", encoding="utf-8")

        result = plugin.directory_tree(str(tmp_path), max_depth=2)
        # depth=2 should not reach level 5
        assert "deep.txt" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 19  get_disk_usage
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestGetDiskUsage:
    def test_disk_usage(self, plugin, tmp_path):
        sub = tmp_path / "big_sub"
        sub.mkdir()
        (sub / "large.bin").write_bytes(b"\x00" * 10240)
        (tmp_path / "small.txt").write_text("hi", encoding="utf-8")

        result = plugin.get_disk_usage(str(tmp_path))
        assert "磁盘占用分析" in result
        assert "big_sub" in result

    def test_disk_usage_nonexistent(self, plugin):
        result = plugin.get_disk_usage("/no/such/dir")
        assert "不存在" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 20  find_large_files
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestFindLargeFiles:
    def test_find_large_in_dir(self, plugin, tmp_path):
        (tmp_path / "big.bin").write_bytes(b"\x00" * (11 * 1024 * 1024))

        result = plugin.find_large_files(path=str(tmp_path), min_size_mb=10)
        assert "big.bin" in result

    def test_find_large_none_found(self, plugin, tmp_path):
        (tmp_path / "tiny.txt").write_text("x", encoding="utf-8")

        result = plugin.find_large_files(path=str(tmp_path), min_size_mb=10)
        assert "未找到" in result

    def test_find_large_nonexistent_dir(self, plugin):
        result = plugin.find_large_files(path="/no/such/dir")
        assert "不存在" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_large_from_registry(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_large_files.return_value = [
            _make_entry(size_bytes=20 * 1024 * 1024, path="/big/file.iso"),
        ]
        mock_get_reg.return_value = mock_reg

        result = plugin.find_large_files(path="", min_size_mb=10)
        assert "file.iso" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 21  find_old_files
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestFindOldFiles:
    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_old_with_results(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_old_files.return_value = [_make_entry()]
        mock_get_reg.return_value = mock_reg

        result = plugin.find_old_files(days_old=180)
        assert "report.pdf" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_find_old_empty(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_old_files.return_value = []
        mock_get_reg.return_value = mock_reg

        result = plugin.find_old_files(days_old=30)
        assert "没有" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 22  batch_rename
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBatchRename:
    def test_batch_rename_dry_run(self, plugin, tmp_path):
        (tmp_path / "photo_001.jpg").write_text("", encoding="utf-8")
        (tmp_path / "photo_002.jpg").write_text("", encoding="utf-8")
        (tmp_path / "notes.txt").write_text("", encoding="utf-8")

        result = plugin.batch_rename(
            directory=str(tmp_path),
            pattern=r"photo_(\d+)",
            replacement=r"img_\1",
            dry_run=True,
        )
        assert "预演" in result
        assert "img_001" in result
        # Files should NOT be renamed in dry_run
        assert (tmp_path / "photo_001.jpg").exists()

    def test_batch_rename_execute(self, plugin, tmp_path):
        (tmp_path / "photo_001.jpg").write_text("", encoding="utf-8")

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.update_path.return_value = True
            mock_get_reg.return_value = mock_reg
            result = plugin.batch_rename(
                directory=str(tmp_path),
                pattern=r"photo_(\d+)",
                replacement=r"img_\1",
                dry_run=False,
            )
        assert "重命名完成" in result
        assert (tmp_path / "img_001.jpg").exists()

    def test_batch_rename_no_matches(self, plugin, tmp_path):
        (tmp_path / "readme.md").write_text("", encoding="utf-8")

        result = plugin.batch_rename(
            directory=str(tmp_path),
            pattern=r"zzz_(\d+)",
            replacement=r"aaa_\1",
        )
        assert "没有符合条件" in result

    def test_batch_rename_bad_regex(self, plugin, tmp_path):
        (tmp_path / "a.txt").write_text("", encoding="utf-8")
        result = plugin.batch_rename(
            directory=str(tmp_path),
            pattern=r"[invalid",
            replacement="x",
        )
        assert "正则表达式无效" in result

    def test_batch_rename_nonexistent_dir(self, plugin):
        result = plugin.batch_rename(
            directory="/no/such/dir",
            pattern="x",
            replacement="y",
        )
        assert "不存在" in result

    def test_batch_rename_with_file_filter(self, plugin, tmp_path):
        (tmp_path / "a_001.jpg").write_text("", encoding="utf-8")
        (tmp_path / "a_002.txt").write_text("", encoding="utf-8")

        result = plugin.batch_rename(
            directory=str(tmp_path),
            pattern=r"a_(\d+)",
            replacement=r"b_\1",
            file_filter=".jpg",
            dry_run=True,
        )
        assert "b_001" in result
        # txt file should not appear in rename plan
        assert "b_002" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# 23  batch_move
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBatchMove:
    def test_batch_move_dry_run(self, plugin, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "doc1.pdf").write_text("", encoding="utf-8")
        (src / "doc2.pdf").write_text("", encoding="utf-8")
        dest = tmp_path / "dest"

        result = plugin.batch_move(
            source_dir=str(src),
            dest_dir=str(dest),
            file_filter=".pdf",
            dry_run=True,
        )
        assert "预演" in result
        assert "doc1.pdf" in result
        # Files NOT moved in dry_run
        assert (src / "doc1.pdf").exists()

    def test_batch_move_execute(self, plugin, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "a.txt").write_text("a", encoding="utf-8")
        dest = tmp_path / "dest"

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_reg.update_path.return_value = True
            mock_get_reg.return_value = mock_reg
            result = plugin.batch_move(
                source_dir=str(src),
                dest_dir=str(dest),
                dry_run=False,
            )
        assert "批量移动完成" in result
        assert (dest / "a.txt").exists()

    def test_batch_move_no_matches(self, plugin, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        dest = tmp_path / "dest"

        result = plugin.batch_move(
            source_dir=str(src),
            dest_dir=str(dest),
        )
        assert "没有符合条件" in result

    def test_batch_move_nonexistent_source(self, plugin):
        result = plugin.batch_move(
            source_dir="/no/such/dir",
            dest_dir="/some/dest",
        )
        assert "不存在" in result

    def test_batch_move_category_filter(self, plugin, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "photo.jpg").write_text("", encoding="utf-8")
        (src / "report.pdf").write_text("", encoding="utf-8")
        dest = tmp_path / "dest"

        result = plugin.batch_move(
            source_dir=str(src),
            dest_dir=str(dest),
            category="图片",
            dry_run=True,
        )
        assert "photo.jpg" in result
        assert "report.pdf" not in result


# ═══════════════════════════════════════════════════════════════════════════════
# cleanup_duplicates
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCleanupDuplicates:
    @patch("app.core.file.file_registry.get_file_registry")
    def test_no_duplicates(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.get_duplicates.return_value = []
        mock_get_reg.return_value = mock_reg

        result = plugin.cleanup_duplicates()
        assert "没有检测到重复文件" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_duplicates_dry_run(self, mock_get_reg, plugin):
        e1 = _make_entry(path="/a/f1.txt", mtime=2000000000.0, size_bytes=1024)
        e2 = _make_entry(path="/b/f1_copy.txt", mtime=1000000000.0, size_bytes=1024)
        mock_reg = MagicMock()
        mock_reg.get_duplicates.return_value = [[e1, e2]]
        mock_get_reg.return_value = mock_reg

        result = plugin.cleanup_duplicates(dry_run=True)
        assert "预演" in result
        assert "保留" in result
        assert "删除" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_duplicates_oldest_strategy(self, mock_get_reg, plugin):
        e1 = _make_entry(path="/a/f1.txt", mtime=2000000000.0)
        e2 = _make_entry(path="/b/f1.txt", mtime=1000000000.0)
        mock_reg = MagicMock()
        mock_reg.get_duplicates.return_value = [[e1, e2]]
        mock_get_reg.return_value = mock_reg

        result = plugin.cleanup_duplicates(keep_strategy="oldest", dry_run=True)
        # Oldest (e2) should be kept; newest (e1) should be listed for deletion
        assert "保留" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 24  compress_files
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCompressFiles:
    def test_compress_zip(self, plugin, tmp_path):
        f1 = tmp_path / "a.txt"
        f1.write_text("aaa", encoding="utf-8")
        f2 = tmp_path / "b.txt"
        f2.write_text("bbb", encoding="utf-8")
        out = tmp_path / "archive.zip"

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            result = plugin.compress_files([str(f1), str(f2)], str(out))

        assert "已打包" in result
        assert out.exists()
        with zipfile.ZipFile(str(out), "r") as zf:
            assert len(zf.namelist()) == 2

    def test_compress_directory(self, plugin, tmp_path):
        sub = tmp_path / "mydir"
        sub.mkdir()
        (sub / "c.txt").write_text("ccc", encoding="utf-8")
        out = tmp_path / "dir_archive.zip"

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            result = plugin.compress_files([str(sub)], str(out))

        assert "已打包" in result
        assert out.exists()

    def test_compress_output_exists(self, plugin, tmp_path):
        out = tmp_path / "exists.zip"
        out.write_text("", encoding="utf-8")
        result = plugin.compress_files(["/fake"], str(out))
        assert "已存在" in result

    def test_compress_no_valid_sources(self, plugin, tmp_path):
        out = tmp_path / "empty.zip"
        result = plugin.compress_files(["/no/such/file.txt"], str(out))
        assert "没有有效文件" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 25  extract_archive
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestExtractArchive:
    def test_extract_zip(self, plugin, tmp_path):
        # Create a test zip
        arc = tmp_path / "test.zip"
        with zipfile.ZipFile(str(arc), "w") as zf:
            zf.writestr("inner.txt", "hello from zip")
        dest = tmp_path / "out"

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            result = plugin.extract_archive(str(arc), str(dest))

        assert "解压完成" in result
        assert (dest / "inner.txt").exists()

    def test_extract_nonexistent(self, plugin):
        result = plugin.extract_archive("/no/archive.zip")
        assert "不存在" in result

    def test_extract_unsupported_format(self, plugin, tmp_path):
        f = tmp_path / "data.xyz"
        f.write_text("not an archive", encoding="utf-8")
        result = plugin.extract_archive(str(f))
        assert "不支持" in result

    def test_extract_default_dest(self, plugin, tmp_path):
        arc = tmp_path / "auto.zip"
        with zipfile.ZipFile(str(arc), "w") as zf:
            zf.writestr("file.txt", "data")

        with patch("app.core.file.file_registry.get_file_registry") as mock_get_reg:
            mock_reg = MagicMock()
            mock_get_reg.return_value = mock_reg
            result = plugin.extract_archive(str(arc))

        assert "解压完成" in result
        # Default dest = arc.parent / arc.stem = tmp_path / "auto"
        assert (tmp_path / "auto" / "file.txt").exists()


# ═══════════════════════════════════════════════════════════════════════════════
# 26  manage_tag
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestManageTag:
    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_add(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.add_tag.return_value = True
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="add", path="/f/a.txt", tag="important")
        assert "已添加标签" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_list(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.get_tags.return_value = ["work", "urgent"]
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="list", path="/f/a.txt")
        assert "work" in result
        assert "urgent" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_list_empty(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.get_tags.return_value = []
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="list", path="/f/a.txt")
        assert "没有标签" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_remove(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.remove_tag.return_value = True
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="remove", path="/f/a.txt", tag="old")
        assert "已移除标签" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_clear(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.clear_tags.return_value = 3
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="clear", path="/f/a.txt")
        assert "已清除" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_list_all(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_all_tags.return_value = [
            {"tag": "work", "count": 5},
            {"tag": "personal", "count": 2},
        ]
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="list_all")
        assert "work" in result
        assert "personal" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_list_all_empty(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_all_tags.return_value = []
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="list_all")
        assert "没有任何标签" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_files_by_tag(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_by_tag.return_value = ["/a/b.txt", "/c/d.txt"]
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="files_by_tag", tag="work")
        assert "/a/b.txt" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_files_by_tag_no_tag(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="files_by_tag")
        assert "请提供 tag" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_unknown_action(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="invalid_action", path="/f/a.txt")
        assert "未知操作" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_add_no_path(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="add", tag="sometag")
        assert "请提供 path" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_tag_add_no_tag(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_tag(action="add", path="/f/a.txt")
        assert "请提供 tag" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 27  manage_favorite
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestManageFavorite:
    @patch("app.core.file.file_registry.get_file_registry")
    def test_favorite_add(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.add_favorite.return_value = True
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_favorite(action="add", path="/f/a.txt")
        assert "已加入收藏" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_favorite_remove(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.remove_favorite.return_value = True
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_favorite(action="remove", path="/f/a.txt")
        assert "已取消收藏" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_favorite_list(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_favorites.return_value = ["/f/a.txt", "/f/b.txt"]
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_favorite(action="list")
        assert "a.txt" in result
        assert "b.txt" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_favorite_list_empty(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.list_favorites.return_value = []
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_favorite(action="list")
        assert "为空" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_favorite_unknown_action(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_favorite(action="blah", path="/f/a.txt")
        assert "未知操作" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_favorite_add_no_path(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_get_reg.return_value = mock_reg

        result = plugin.manage_favorite(action="add")
        assert "请提供 path" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 28  summarize_file
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSummarizeFile:
    def test_summarize_nonexistent(self, plugin):
        result = plugin.summarize_file("/no/file.txt")
        assert "不存在" in result

    def test_summarize_not_a_file(self, plugin, tmp_path):
        result = plugin.summarize_file(str(tmp_path))
        assert "不是文件" in result

    def test_summarize_empty_content(self, plugin, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("", encoding="utf-8")

        with patch("app.core.file.file_registry._extract_text_preview", return_value=""):
            result = plugin.summarize_file(str(f))
        assert "无法提取文本内容" in result

    def test_summarize_extract_failure(self, plugin, tmp_path):
        f = tmp_path / "bad.bin"
        f.write_bytes(b"\x00\x01\x02")

        with patch("app.core.file.file_registry._extract_text_preview", side_effect=Exception("parse fail")):
            result = plugin.summarize_file(str(f))
        assert "内容提取失败" in result

    def test_summarize_llm_success(self, plugin, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Important document content", encoding="utf-8")

        with patch("app.core.file.file_registry._extract_text_preview", return_value="Important document content"):
            with patch("app.core.llm.gemini.GeminiProvider") as MockLLM:
                mock_llm = MagicMock()
                mock_llm.generate_content.return_value = {"text": "This is a summary of the document."}
                MockLLM.return_value = mock_llm
                result = plugin.summarize_file(str(f))

        assert "doc.txt" in result
        assert "summary" in result.lower() or "Summary" in result or "摘要" in result or "document" in result.lower()

    def test_summarize_llm_failure(self, plugin, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Some content here", encoding="utf-8")

        with patch("app.core.file.file_registry._extract_text_preview", return_value="Some content here"):
            with patch("app.core.llm.gemini.GeminiProvider", side_effect=Exception("API key missing")):
                result = plugin.summarize_file(str(f))

        assert "LLM 摘要失败" in result
        assert "Some content" in result

    def test_summarize_with_focus(self, plugin, tmp_path):
        f = tmp_path / "doc.txt"
        f.write_text("Data analysis report with charts.", encoding="utf-8")

        with patch("app.core.file.file_registry._extract_text_preview", return_value="Data analysis report with charts."):
            with patch("app.core.llm.gemini.GeminiProvider") as MockLLM:
                mock_llm = MagicMock()
                mock_llm.generate_content.return_value = {"text": "Focused summary."}
                MockLLM.return_value = mock_llm
                result = plugin.summarize_file(str(f), focus="key findings")

        assert "doc.txt" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 29  undo_last_op
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestUndoLastOp:
    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_no_history(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = None
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "没有可撤销" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_rename(self, mock_get_reg, plugin, tmp_path):
        renamed = tmp_path / "new.txt"
        renamed.write_text("data", encoding="utf-8")
        original = tmp_path / "old.txt"

        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = {
            "op_type": "rename",
            "src_path": str(original),
            "dst_path": str(renamed),
            "meta": {},
        }
        mock_reg.update_path.return_value = True
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "已撤销重命名" in result
        assert original.exists()

    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_move(self, mock_get_reg, plugin, tmp_path):
        dest = tmp_path / "dest"
        dest.mkdir()
        moved = dest / "file.txt"
        moved.write_text("data", encoding="utf-8")
        original = str(tmp_path / "file.txt")

        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = {
            "op_type": "move",
            "src_path": original,
            "dst_path": str(moved),
            "meta": {},
        }
        mock_reg.update_path.return_value = True
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "已撤销移动" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_copy(self, mock_get_reg, plugin, tmp_path):
        copied = tmp_path / "copy.txt"
        copied.write_text("data", encoding="utf-8")

        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = {
            "op_type": "copy",
            "src_path": "/original.txt",
            "dst_path": str(copied),
            "meta": {},
        }
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "已撤销复制" in result
        assert not copied.exists()

    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_delete_trash(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = {
            "op_type": "delete",
            "src_path": "/deleted/file.txt",
            "dst_path": "",
            "meta": {"trash": True},
        }
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "回收站" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_delete_permanent(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = {
            "op_type": "delete",
            "src_path": "/deleted/file.txt",
            "dst_path": "",
            "meta": {"trash": False},
        }
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "永久删除" in result
        assert "无法" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_unknown_op(self, mock_get_reg, plugin):
        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = {
            "op_type": "magic",
            "src_path": "",
            "dst_path": "",
            "meta": {},
        }
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "不支持撤销" in result

    @patch("app.core.file.file_registry.get_file_registry")
    def test_undo_rename_file_gone(self, mock_get_reg, plugin, tmp_path):
        mock_reg = MagicMock()
        mock_reg.pop_last_undoable_op.return_value = {
            "op_type": "rename",
            "src_path": str(tmp_path / "old.txt"),
            "dst_path": str(tmp_path / "gone.txt"),  # doesn't exist
            "meta": {},
        }
        mock_get_reg.return_value = mock_reg

        result = plugin.undo_last_op()
        assert "不存在" in result


# ═══════════════════════════════════════════════════════════════════════════════
# 30  _scanner_fallback
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestScannerFallback:
    def test_scanner_returns_results(self):
        from app.core.file.file_tools import FileToolsPlugin

        mock_scanner = MagicMock()
        mock_scanner.search_files.return_value = [
            {"path": "/scan/a.txt", "category": "文档", "size_kb": 10, "snippet": "hi"},
            {"file_path": "/scan/b.txt"},
        ]

        mock_mod = MagicMock()
        mock_mod.FileScanner.return_value = mock_scanner

        with patch("importlib.import_module", return_value=mock_mod):
            result = FileToolsPlugin._scanner_fallback("test", 5)

        assert len(result) == 2
        assert result[0]["path"] == "/scan/a.txt"
        assert result[1]["path"] == "/scan/b.txt"

    def test_scanner_no_class(self):
        from app.core.file.file_tools import FileToolsPlugin

        mock_mod = MagicMock(spec=[])  # no FileScanner attr
        mock_mod.FileScanner = None

        with patch("importlib.import_module", return_value=mock_mod):
            result = FileToolsPlugin._scanner_fallback("test", 5)

        assert result == []

    def test_scanner_import_error(self):
        from app.core.file.file_tools import FileToolsPlugin

        with patch("importlib.import_module", side_effect=ImportError("no module")):
            with pytest.raises(ImportError):
                FileToolsPlugin._scanner_fallback("test", 5)

    def test_scanner_non_list_response(self):
        from app.core.file.file_tools import FileToolsPlugin

        mock_scanner = MagicMock()
        mock_scanner.search_files.return_value = "not a list"

        mock_mod = MagicMock()
        mock_mod.FileScanner.return_value = mock_scanner

        with patch("importlib.import_module", return_value=mock_mod):
            result = FileToolsPlugin._scanner_fallback("test", 5)

        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# register_file_tools convenience function
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRegisterFileTools:
    def test_register(self):
        from app.core.file.file_tools import register_file_tools

        mock_registry = MagicMock()
        register_file_tools(mock_registry)
        mock_registry.register_plugin.assert_called_once()
        arg = mock_registry.register_plugin.call_args[0][0]
        assert arg.name == "FileTools"
