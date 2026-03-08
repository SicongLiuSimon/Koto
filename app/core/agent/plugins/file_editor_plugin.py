from typing import Any, Dict, List
from app.core.agent.base import AgentPlugin
from app.core.services.file_service import FileService


class FileEditorPlugin(AgentPlugin):
    def __init__(self, workspace_dir: str = None):
        self.service = FileService(workspace_dir)

    @property
    def name(self) -> str:
        return "FileEditor"

    @property
    def description(self) -> str:
        return "Tools for full local file management: read, write, edit, copy, move, delete, rename, find files."

    def get_tools(self) -> List[Dict[str, Any]]:
        return [
            {"name": "read_file",       "func": self.read_file,       "description": "Read the content of a local file."},
            # — 精准编辑首选（无需预先读文件）—
            {"name": "replace_text",    "func": self.replace_text,    "description": "[PREFERRED for targeted edits] Replace the FIRST occurrence of exact text in a file. No need to read the file first. Use for single-location changes."},
            {"name": "patch_file",      "func": self.patch_file,      "description": "[PREFERRED for multi-location edits] Apply multiple text replacements to a file in a SINGLE call. Pass a list of {\"old\": \"...\", \"new\": \"...\"} pairs. Much faster than calling replace_text repeatedly. No need to read the file first."},
            {"name": "insert_line",     "func": self.insert_line,     "description": "Insert a line at a specific 1-based line number (mode=\"before\"|\"after\"). No need to read the file first."},
            {"name": "delete_lines",    "func": self.delete_lines,    "description": "Delete lines start_line..end_line (1-based, inclusive). No need to read the file first."},
            {"name": "append_text",     "func": self.append_text,     "description": "Append text to the end of a file."},
            {"name": "write_file",      "func": self.write_file,      "description": "[FULL OVERWRITE] Write complete content to a file. Use ONLY for creating a new file or completely rewriting a file. For partial edits use replace_text or patch_file instead."},
            {"name": "delete_file",     "func": self.delete_file,     "description": "Delete a file (auto-backed up before deletion)."},
            {"name": "copy_file",       "func": self.copy_file,       "description": "Copy a file to a new location."},
            {"name": "move_file",       "func": self.move_file,       "description": "Move or cut a file to a new location."},
            {"name": "rename_file",     "func": self.rename_file,     "description": "Rename a file within its current directory."},
            {"name": "create_directory","func": self.create_directory,"description": "Create a directory (including all parent dirs)."},
            {"name": "get_file_info",   "func": self.get_file_info,   "description": "Get detailed metadata of a file or directory."},
            {"name": "list_directory",  "func": self.list_directory,  "description": "List directory contents with metadata."},
            {"name": "find_file",       "func": self.find_file,       "description": "Search for files by name keyword on the local system."},
            {"name": "list_backups",    "func": self.list_backups,    "description": "List all auto-created file backups."},
            {"name": "restore_backup",  "func": self.restore_backup,  "description": "Restore a file from a backup."},
        ]

    # ── Tool wrappers ──────────────────────────────────────────────────────

    def read_file(self, file_path: str, max_chars: int = 5000) -> str:
        r = self.service.read_file(file_path, max_chars=max_chars)
        if r["success"]:
            return f"[{file_path}] ({r['lines']} lines, {r['size_human'] if 'size_human' in r else r['size']} bytes, {r['encoding']}):\n{r['content']}"
        return f"Error: {r['error']}"

    def write_file(self, file_path: str, content: str) -> str:
        r = self.service.write_file(file_path, content)
        return f"Written to {r['path']} ({r['size']} bytes)" if r["success"] else f"Error: {r['error']}"

    def append_text(self, file_path: str, text: str) -> str:
        r = self.service.append_text(file_path, text)
        return f"Appended to {r['path']}" if r["success"] else f"Error: {r['error']}"

    def replace_text(self, file_path: str, old_text: str, new_text: str) -> str:
        r = self.service.replace_text(file_path, old_text, new_text)
        if r["success"]:
            return f"Replaced {r.get('replacements', '?')} occurrence(s) in {file_path}"
        return f"Error: {r['error']}"

    def patch_file(self, file_path: str, patches: List[Dict[str, str]]) -> str:
        """Apply multiple text replacements in a single file read+write."""
        r = self.service.patch_file(file_path, patches)
        if r["success"]:
            not_found = r.get("not_found", [])
            msg = f"Applied {r.get('total_replacements', 0)} replacement(s) in {file_path}"
            if not_found:
                msg += f". WARNING: {len(not_found)} pattern(s) not found: {not_found}"
            return msg
        return f"Error: {r['error']}"

    def insert_line(self, file_path: str, line_number: int, text: str, mode: str = "after") -> str:
        r = self.service.insert_line(file_path, line_number, text, mode)
        return f"Line inserted in {file_path}" if r["success"] else f"Error: {r['error']}"

    def delete_lines(self, file_path: str, start_line: int, end_line: int = None) -> str:
        r = self.service.delete_lines(file_path, start_line, end_line)
        if r["success"]:
            return f"Deleted {r.get('deleted_lines', '?')} line(s) from {file_path}"
        return f"Error: {r['error']}"

    def delete_file(self, file_path: str) -> str:
        r = self.service.delete_file(file_path)
        return r.get("message", f"Deleted {file_path}") if r["success"] else f"Error: {r['error']}"

    def copy_file(self, source: str, destination: str, overwrite: bool = False) -> str:
        r = self.service.copy_file(source, destination, overwrite)
        return r.get("message", "Copied") if r["success"] else f"Error: {r['error']}"

    def move_file(self, source: str, destination: str, overwrite: bool = False) -> str:
        r = self.service.move_file(source, destination, overwrite)
        return r.get("message", "Moved") if r["success"] else f"Error: {r['error']}"

    def rename_file(self, file_path: str, new_name: str) -> str:
        r = self.service.rename_file(file_path, new_name)
        return r.get("message", "Renamed") if r["success"] else f"Error: {r['error']}"

    def create_directory(self, dir_path: str) -> str:
        r = self.service.create_directory(dir_path)
        return r.get("message", "Created") if r["success"] else f"Error: {r['error']}"

    def get_file_info(self, file_path: str) -> str:
        r = self.service.get_file_info(file_path)
        if not r["success"]:
            return f"Error: {r['error']}"
        lines = [
            f"Path: {r['path']}",
            f"Type: {'Directory' if r['is_dir'] else 'File'}",
            f"Size: {r.get('size_human', r.get('size', ''))}",
            f"Modified: {r['modified']}",
            f"Created: {r['created']}",
        ]
        if r["is_dir"]:
            lines.append(f"Children: {r.get('children_count', '?')} ({r.get('files_count', 0)} files, {r.get('dirs_count', 0)} dirs)")
        return "\n".join(lines)

    def list_directory(self, directory: str, recursive: bool = False, max_items: int = 50) -> str:
        r = self.service.list_directory(directory, recursive=recursive, max_items=max_items)
        if not r["success"]:
            return f"Error: {r['error']}"
        lines = [f"Directory: {r['directory']} ({r['count']} items" + (" [truncated]" if r['truncated'] else "") + ")"]
        for item in r["items"]:
            icon = "📁" if item["type"] == "dir" else "📄"
            size = f" [{item['size_human']}]" if item["size_human"] else ""
            lines.append(f"  {icon} {item['name']}{size}  {item['modified']}  {item['path']}")
        return "\n".join(lines)

    def find_file(self, query: str, search_dir: str = None, file_type: str = None, max_results: int = 10) -> str:
        r = self.service.search_files(query, search_dir=search_dir, file_type=file_type, max_results=max_results)
        if not r["success"]:
            return f"Error: {r['error']}"
        if not r["results"]:
            return f"No files found matching '{query}'"
        lines = [f"Found {r['count']} result(s) for '{query}' ({r['source']}):"]
        for item in r["results"]:
            lines.append(f"  📄 {item['name']}  [{item.get('size_human', '')}]  {item.get('modified', '')}  {item['path']}")
        return "\n".join(lines)

    def list_backups(self) -> str:
        r = self.service.list_backups()
        if not r["success"]:
            return f"Error: {r['error']}"
        if not r["backups"]:
            return "No backups found."
        lines = [f"Backups ({r['count']}):"]
        for b in r["backups"]:
            lines.append(f"  {b['name']}  [{b['size_human']}]  {b['created']}  {b['path']}")
        return "\n".join(lines)

    def restore_backup(self, backup_file: str, restore_to: str = None) -> str:
        r = self.service.restore_backup(backup_file, restore_to)
        return r.get("message", "Restored") if r["success"] else f"Error: {r['error']}"
