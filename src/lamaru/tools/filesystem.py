"""Filesystem tools: read, write, edit, delete, list."""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from lamaru.domain.tools import PermissionLevel, Tool, ToolDefinition, ToolResult


class ReadFileTool(Tool):
    """Tool to read content from a file."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_file",
            description="Read the contents of a file with optional line range.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to the file"},
                    "start_line": {"type": "integer", "description": "1-based start line (optional)"},
                    "end_line": {"type": "integer", "description": "1-based end line (optional)"},
                },
                "required": ["path"],
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=15.0,
        )

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute() and self.workspace_root:
            p = self.workspace_root / p
        return p.resolve()

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        path_str = input_args.get("path")
        if not path_str:
            return ToolResult(success=False, error="Parameter 'path' is required.")

        try:
            target_path = self._resolve_path(path_str)
            if not target_path.exists():
                return ToolResult(success=False, error=f"File not found: {path_str}")
            if not target_path.is_file():
                return ToolResult(success=False, error=f"Path is not a file: {path_str}")

            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            start_line = input_args.get("start_line")
            end_line = input_args.get("end_line")

            start_idx = max(0, (start_line - 1)) if start_line else 0
            end_idx = end_line if end_line else len(lines)

            content = "".join(lines[start_idx:end_idx])
            return ToolResult(
                success=True,
                output=content,
                metadata={
                    "path": str(target_path),
                    "total_lines": len(lines),
                    "start_line": start_idx + 1,
                    "end_line": min(end_idx, len(lines)),
                },
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error reading file: {str(e)}")


class WriteFileTool(Tool):
    """Tool to create or overwrite a file."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="write_file",
            description="Write content to a file. Overwriting requires explicit overwrite=True.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path"},
                    "content": {"type": "string", "description": "File text content"},
                    "overwrite": {"type": "boolean", "default": False},
                },
                "required": ["path", "content"],
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=20.0,
        )

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute() and self.workspace_root:
            p = self.workspace_root / p
        return p.resolve()

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        path_str = input_args.get("path")
        content = input_args.get("content", "")
        overwrite = input_args.get("overwrite", False)

        if not path_str:
            return ToolResult(success=False, error="Parameter 'path' is required.")

        try:
            target_path = self._resolve_path(path_str)
            if target_path.exists() and not overwrite:
                return ToolResult(
                    success=False,
                    error=f"File already exists: {path_str}. Specify overwrite=True to replace.",
                )

            target_path.parent.mkdir(parents=True, exist_ok=True)
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(content)

            return ToolResult(
                success=True,
                output=f"Successfully wrote {len(content)} characters to {path_str}",
                metadata={"path": str(target_path), "bytes_written": len(content.encode("utf-8"))},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error writing file: {str(e)}")


class EditFileTool(Tool):
    """Tool to edit an existing file by replacing specific target text."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="edit_file",
            description="Replace target string in an existing file with replacement string.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Target file path"},
                    "target_content": {"type": "string", "description": "Exact text to find and replace"},
                    "replacement_content": {"type": "string", "description": "New replacement text"},
                },
                "required": ["path", "target_content", "replacement_content"],
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=20.0,
        )

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute() and self.workspace_root:
            p = self.workspace_root / p
        return p.resolve()

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        path_str = input_args.get("path")
        target_content = input_args.get("target_content")
        replacement_content = input_args.get("replacement_content")

        if not path_str or target_content is None or replacement_content is None:
            return ToolResult(
                success=False,
                error="Parameters 'path', 'target_content', and 'replacement_content' are required.",
            )

        try:
            target_path = self._resolve_path(path_str)
            if not target_path.exists() or not target_path.is_file():
                return ToolResult(success=False, error=f"File not found: {path_str}")

            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()

            if target_content not in content:
                return ToolResult(
                    success=False,
                    error=f"Target content not found in file: {path_str}",
                )

            occurrences = content.count(target_content)
            new_content = content.replace(target_content, replacement_content, 1)

            with open(target_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return ToolResult(
                success=True,
                output=f"Successfully edited {path_str} (1 of {occurrences} occurrence replaced)",
                metadata={"path": str(target_path), "total_occurrences": occurrences},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error editing file: {str(e)}")


class DeleteFileTool(Tool):
    """Tool to delete a file. Requires confirmation."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="delete_file",
            description="Delete a file from the workspace. Requires confirmation.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file to delete"},
                },
                "required": ["path"],
            },
            permission=PermissionLevel.CONFIRMATION_REQUIRED,
            timeout_seconds=15.0,
        )

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute() and self.workspace_root:
            p = self.workspace_root / p
        return p.resolve()

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        path_str = input_args.get("path")
        if not path_str:
            return ToolResult(success=False, error="Parameter 'path' is required.")

        try:
            target_path = self._resolve_path(path_str)
            if not target_path.exists():
                return ToolResult(success=False, error=f"File not found: {path_str}")
            if not target_path.is_file():
                return ToolResult(success=False, error=f"Path is not a regular file: {path_str}")

            target_path.unlink()
            return ToolResult(
                success=True,
                output=f"Successfully deleted {path_str}",
                metadata={"path": str(target_path)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error deleting file: {str(e)}")


class ListDirectoryTool(Tool):
    """Tool to list contents of a directory."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="list_directory",
            description="List files and subdirectories within a given path.",
            input_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "default": ".", "description": "Directory path"},
                    "recursive": {"type": "boolean", "default": False},
                },
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=20.0,
        )

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute() and self.workspace_root:
            p = self.workspace_root / p
        return p.resolve()

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        path_str = input_args.get("path", ".")
        recursive = input_args.get("recursive", False)

        try:
            target_path = self._resolve_path(path_str)
            if not target_path.exists() or not target_path.is_dir():
                return ToolResult(success=False, error=f"Directory not found: {path_str}")

            entries = []
            if recursive:
                for root, dirs, files in os.walk(target_path):
                    # Skip common noisy directories
                    dirs[:] = [d for d in dirs if d not in {".git", ".venv", "__pycache__", ".pytest_cache"}]
                    rel_root = Path(root).relative_to(target_path)
                    for d in dirs:
                        entries.append({"name": str(rel_root / d), "type": "directory"})
                    for f in files:
                        entries.append({"name": str(rel_root / f), "type": "file"})
            else:
                for item in sorted(target_path.iterdir()):
                    entries.append(
                        {
                            "name": item.name,
                            "type": "directory" if item.is_dir() else "file",
                            "size": item.stat().st_size if item.is_file() else None,
                        }
                    )

            return ToolResult(
                success=True,
                output=entries,
                metadata={"path": str(target_path), "count": len(entries)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error listing directory: {str(e)}")
