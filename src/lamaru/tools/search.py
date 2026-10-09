"""Search tools: search_files (glob) and search_text (grep) with sensitive file exclusion."""

import os
from pathlib import Path
import re
from typing import Any, Dict, List, Optional

from lamaru.domain.tools import PermissionLevel, Tool, ToolDefinition, ToolResult
from lamaru.tools.security import DEFAULT_PROTECTED_FILE_PATTERNS

EXCLUDED_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", "node_modules", ".idea", ".vscode"}


def is_sensitive_file(filename: str, rel_path: Optional[str] = None) -> bool:
    """Check if file matches sensitive protected file patterns."""
    for pattern in DEFAULT_PROTECTED_FILE_PATTERNS:
        if re.search(pattern, filename, re.IGNORECASE):
            return True
        if rel_path and re.search(pattern, rel_path, re.IGNORECASE):
            return True
    return False


class SearchFilesTool(Tool):
    """Tool to search for files matching a glob pattern."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_files",
            description="Search for files matching a glob pattern (e.g. '*.py', '**/*.json'). Excludes sensitive files.",
            input_schema={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern to match"},
                    "path": {"type": "string", "default": ".", "description": "Base directory to search from"},
                    "max_results": {"type": "integer", "default": 100, "description": "Maximum results to return"},
                },
                "required": ["pattern"],
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
        pattern = input_args.get("pattern")
        if not pattern:
            return ToolResult(success=False, error="Parameter 'pattern' is required.")

        path_str = input_args.get("path", ".")
        max_results = input_args.get("max_results", 100)

        try:
            target_path = self._resolve_path(path_str)
            if not target_path.exists() or not target_path.is_dir():
                return ToolResult(success=False, error=f"Directory not found: {path_str}")

            matches: List[str] = []
            for item in target_path.glob(pattern):
                # Filter out excluded dirs in path
                parts = set(item.parts)
                if parts.intersection(EXCLUDED_DIRS):
                    continue

                try:
                    rel_p = item.relative_to(target_path)
                    rel_str = str(rel_p).replace("\\", "/")
                except ValueError:
                    rel_p = item
                    rel_str = str(item).replace("\\", "/")

                # Filter out sensitive protected files (.env, *.key, *.pem, *.db, etc.)
                if is_sensitive_file(item.name, rel_str):
                    continue

                matches.append(str(rel_p))

                if len(matches) >= max_results:
                    break

            return ToolResult(
                success=True,
                output=matches,
                metadata={"pattern": pattern, "count": len(matches), "base_path": str(target_path)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error searching files: {str(e)}")


class SearchTextTool(Tool):
    """Tool to search for text patterns across files in a directory."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="search_text",
            description="Search for text across files in a directory (like grep). Excludes sensitive files.",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Text query to search for"},
                    "path": {"type": "string", "default": ".", "description": "Directory to search within"},
                    "case_sensitive": {"type": "boolean", "default": False},
                    "max_results": {"type": "integer", "default": 50},
                },
                "required": ["query"],
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=25.0,
        )

    def _resolve_path(self, path_str: str) -> Path:
        p = Path(path_str)
        if not p.is_absolute() and self.workspace_root:
            p = self.workspace_root / p
        return p.resolve()

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        query = input_args.get("query")
        if not query:
            return ToolResult(success=False, error="Parameter 'query' is required.")

        path_str = input_args.get("path", ".")
        case_sensitive = input_args.get("case_sensitive", False)
        max_results = input_args.get("max_results", 50)

        search_query = query if case_sensitive else query.lower()

        try:
            target_path = self._resolve_path(path_str)
            if not target_path.exists() or not target_path.is_dir():
                return ToolResult(success=False, error=f"Directory not found: {path_str}")

            results: List[Dict[str, Any]] = []

            for root, dirs, files in os.walk(target_path):
                dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRS]
                for filename in files:
                    file_path = Path(root) / filename

                    try:
                        rel_p = str(file_path.relative_to(target_path)).replace("\\", "/")
                    except ValueError:
                        rel_p = filename

                    # Exclude sensitive files (.env, *.key, *.pem, *.db, etc.)
                    if is_sensitive_file(filename, rel_p):
                        continue

                    # Skip common binary/huge files
                    if file_path.suffix.lower() in {
                        ".pyc", ".png", ".jpg", ".jpeg", ".zip", ".tar", ".gz", ".exe", ".bin"
                    }:
                        continue

                    try:
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            for line_idx, line in enumerate(f, start=1):
                                line_to_check = line if case_sensitive else line.lower()
                                if search_query in line_to_check:
                                    results.append(
                                        {
                                            "file": rel_p,
                                            "line": line_idx,
                                            "content": line.strip(),
                                        }
                                    )
                                    if len(results) >= max_results:
                                        break
                    except Exception:
                        continue

                    if len(results) >= max_results:
                        break
                if len(results) >= max_results:
                    break

            return ToolResult(
                success=True,
                output=results,
                metadata={"query": query, "count": len(results), "base_path": str(target_path)},
            )
        except Exception as e:
            return ToolResult(success=False, error=f"Error searching text: {str(e)}")
