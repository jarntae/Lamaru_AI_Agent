"""Git tools: git_status, git_diff, git_log, git_branch, git_commit."""

import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

from lamaru.domain.tools import PermissionLevel, Tool, ToolDefinition, ToolResult


class BaseGitTool(Tool):
    """Base class for Git tools with workspace resolution and command execution."""

    def __init__(self, workspace_root: Optional[str] = None) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None

    async def _run_git(self, args: List[str], timeout: float = 20.0) -> ToolResult:
        cwd = str(self.workspace_root) if self.workspace_root else None
        cmd = ["git"] + args
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout_data, stderr_data = await asyncio.wait_for(
                    process.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await process.wait()
                except Exception:
                    pass
                return ToolResult(
                    success=False,
                    error=f"Git command timed out after {timeout} seconds.",
                    metadata={"command": " ".join(cmd)},
                )

            stdout = stdout_data.decode("utf-8", errors="replace").strip()
            stderr = stderr_data.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0

            return ToolResult(
                success=(exit_code == 0),
                output=stdout,
                error=stderr if exit_code != 0 else None,
                metadata={"command": " ".join(cmd), "exit_code": exit_code},
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Git execution error: {str(e)}",
                metadata={"command": " ".join(cmd)},
            )


class GitStatusTool(BaseGitTool):
    """Tool to check git working tree status."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="git_status",
            description="Show the working tree status (like git status).",
            input_schema={
                "type": "object",
                "properties": {
                    "short": {"type": "boolean", "default": True, "description": "Give the output in short format"},
                },
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=15.0,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        short = input_args.get("short", True)
        args = ["status"]
        if short:
            args.append("--short")
        return await self._run_git(args)


class GitDiffTool(BaseGitTool):
    """Tool to inspect git diffs."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="git_diff",
            description="Show changes between commits, commit and working tree, etc.",
            input_schema={
                "type": "object",
                "properties": {
                    "staged": {"type": "boolean", "default": False, "description": "View staged changes (--staged)"},
                    "path": {"type": "string", "description": "Limit diff to a specific file or path"},
                },
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=20.0,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        staged = input_args.get("staged", False)
        path = input_args.get("path")
        args = ["diff"]
        if staged:
            args.append("--staged")
        if path:
            args.extend(["--", path])
        return await self._run_git(args)


class GitLogTool(BaseGitTool):
    """Tool to view recent commit logs."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="git_log",
            description="Show commit history logs.",
            input_schema={
                "type": "object",
                "properties": {
                    "max_count": {"type": "integer", "default": 10, "description": "Number of commits to show"},
                    "oneline": {"type": "boolean", "default": True, "description": "Show each commit on one line"},
                },
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=15.0,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        max_count = input_args.get("max_count", 10)
        oneline = input_args.get("oneline", True)
        args = ["log", f"-n{max_count}"]
        if oneline:
            args.append("--oneline")
        return await self._run_git(args)


class GitBranchTool(BaseGitTool):
    """Tool to list or check git branches."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="git_branch",
            description="List existing git branches or show current branch.",
            input_schema={
                "type": "object",
                "properties": {
                    "all": {"type": "boolean", "default": False, "description": "List both remote and local branches"},
                },
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=15.0,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        all_branches = input_args.get("all", False)
        args = ["branch"]
        if all_branches:
            args.append("-a")
        return await self._run_git(args)


class GitCommitTool(BaseGitTool):
    """Tool to commit changes."""

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="git_commit",
            description="Record changes to the repository with a commit message.",
            input_schema={
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The commit message"},
                },
                "required": ["message"],
            },
            permission=PermissionLevel.ALLOWED,
            timeout_seconds=20.0,
        )

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        message = input_args.get("message")
        if not message:
            return ToolResult(success=False, error="Parameter 'message' is required.")
        return await self._run_git(["commit", "-m", message])
