"""Tests for Phase 14 Tool System: filesystem, search, terminal, git, and registry."""

import asyncio
from pathlib import Path
import tempfile
import pytest

from lamaru.domain.tools import PermissionLevel, Tool, ToolDefinition, ToolResult
from lamaru.tools.filesystem import (
    DeleteFileTool,
    EditFileTool,
    ListDirectoryTool,
    ReadFileTool,
    WriteFileTool,
)
from lamaru.tools.git import (
    GitBranchTool,
    GitCommitTool,
    GitDiffTool,
    GitLogTool,
    GitStatusTool,
)
from lamaru.tools.registry import ToolRegistry, create_default_tool_registry
from lamaru.tools.search import SearchFilesTool, SearchTextTool
from lamaru.tools.terminal import RunCommandTool


def test_filesystem_read_write_edit_delete():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            write_tool = WriteFileTool(workspace_root=tmpdir)
            read_tool = ReadFileTool(workspace_root=tmpdir)
            edit_tool = EditFileTool(workspace_root=tmpdir)
            delete_tool = DeleteFileTool(workspace_root=tmpdir)

            # 1. Write file
            res_write = await write_tool.execute(
                {"path": "sample.txt", "content": "Line 1\nLine 2\nLine 3\n"}
            )
            assert res_write.success is True
            assert (tmp_path / "sample.txt").exists()

            # Overwrite=False when exists should fail
            res_no_overwrite = await write_tool.execute(
                {"path": "sample.txt", "content": "New content", "overwrite": False}
            )
            assert res_no_overwrite.success is False
            assert "already exists" in res_no_overwrite.error

            # 2. Read full file
            res_read = await read_tool.execute({"path": "sample.txt"})
            assert res_read.success is True
            assert res_read.output == "Line 1\nLine 2\nLine 3\n"

            # Read line range
            res_slice = await read_tool.execute(
                {"path": "sample.txt", "start_line": 2, "end_line": 2}
            )
            assert res_slice.success is True
            assert res_slice.output == "Line 2\n"

            # 3. Edit file
            res_edit = await edit_tool.execute(
                {
                    "path": "sample.txt",
                    "target_content": "Line 2",
                    "replacement_content": "Line 2 MODIFIED",
                }
            )
            assert res_edit.success is True
            res_read_after_edit = await read_tool.execute({"path": "sample.txt"})
            assert "Line 2 MODIFIED" in res_read_after_edit.output

            # 4. Delete file
            res_del = await delete_tool.execute({"path": "sample.txt"})
            assert res_del.success is True
            assert not (tmp_path / "sample.txt").exists()

            # Delete missing file
            res_del_missing = await delete_tool.execute({"path": "sample.txt"})
            assert res_del_missing.success is False
            assert "File not found" in res_del_missing.error

    asyncio.run(_test())


def test_list_directory_tool():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "sub").mkdir()
            (tmp_path / "file1.txt").write_text("Hello", encoding="utf-8")
            (tmp_path / "sub" / "file2.txt").write_text("World", encoding="utf-8")

            list_tool = ListDirectoryTool(workspace_root=tmpdir)

            # Shallow
            res_shallow = await list_tool.execute({"path": "."})
            assert res_shallow.success is True
            names = [entry["name"] for entry in res_shallow.output]
            assert "file1.txt" in names
            assert "sub" in names

            # Recursive
            res_rec = await list_tool.execute({"path": ".", "recursive": True})
            assert res_rec.success is True
            rec_names = [entry["name"] for entry in res_rec.output]
            assert any("file2.txt" in n for n in rec_names)

    asyncio.run(_test())


def test_search_tools():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            (tmp_path / "src").mkdir()
            (tmp_path / "src" / "main.py").write_text(
                "def solve():\n    magic_number = 42\n    return magic_number\n",
                encoding="utf-8",
            )
            (tmp_path / "docs.txt").write_text("Reference to MAGIC_NUMBER here.", encoding="utf-8")

            search_files = SearchFilesTool(workspace_root=tmpdir)
            search_text = SearchTextTool(workspace_root=tmpdir)

            # Search files glob
            res_files = await search_files.execute({"pattern": "**/*.py"})
            assert res_files.success is True
            assert any("main.py" in m for m in res_files.output)

            # Search text case-insensitive
            res_text = await search_text.execute({"query": "magic_number", "case_sensitive": False})
            assert res_text.success is True
            assert len(res_text.output) >= 2

            # Search text case-sensitive
            res_text_cs = await search_text.execute({"query": "MAGIC_NUMBER", "case_sensitive": True})
            assert res_text_cs.success is True
            assert len(res_text_cs.output) == 1
            assert "docs.txt" in res_text_cs.output[0]["file"]

    asyncio.run(_test())


def test_terminal_run_command():
    async def _test():
        tool = RunCommandTool()

        # Echo command
        res = await tool.execute({"command": "python -c \"print('LAMARU_CLI_TEST')\""})
        assert res.success is True
        assert "LAMARU_CLI_TEST" in res.output
        assert res.metadata["exit_code"] == 0

        # Failing command
        res_fail = await tool.execute({"command": "python -c \"import sys; sys.exit(42)\""})
        assert res_fail.success is False
        assert res_fail.metadata["exit_code"] == 42

        # Timeout enforcement
        res_timeout = await tool.execute(
            {"command": "python -c \"import time; time.sleep(2)\"", "timeout_seconds": 0.2}
        )
        assert res_timeout.success is False
        assert "timed out" in res_timeout.error

    asyncio.run(_test())


def test_git_tools_in_repo():
    async def _test():
        # Current workspace is a real git repository
        git_status = GitStatusTool()
        git_log = GitLogTool()
        git_branch = GitBranchTool()
        git_diff = GitDiffTool()

        res_status = await git_status.execute({})
        assert res_status.success is True

        res_log = await git_log.execute({"max_count": 3})
        assert res_log.success is True
        assert len(res_log.output) > 0

        res_branch = await git_branch.execute({})
        assert res_branch.success is True
        assert "feat/phase-14-tool-system" in res_branch.output or "*" in res_branch.output

        res_diff = await git_diff.execute({})
        assert res_diff.success is True

    asyncio.run(_test())


def test_tool_registry():
    async def _test():
        registry = create_default_tool_registry()

        # Check default tools count (5 fs + 2 search + 1 terminal + 5 git = 13 tools)
        tools = registry.list_tools()
        assert len(tools) == 13

        defs = registry.get_definitions()
        assert len(defs) == 13
        names = [d.name for d in defs]
        assert "read_file" in names
        assert "write_file" in names
        assert "edit_file" in names
        assert "delete_file" in names
        assert "list_directory" in names
        assert "search_files" in names
        assert "search_text" in names
        assert "run_command" in names
        assert "git_status" in names

        # Execute existing tool via registry
        res = await registry.execute("git_status", {})
        assert res.success is True

        # Execute non-existent tool
        res_none = await registry.execute("unknown_tool", {})
        assert res_none.success is False
        assert "is not registered" in res_none.error

        # Test timeout via custom tool
        class SlowTool(Tool):
            @property
            def definition(self) -> ToolDefinition:
                return ToolDefinition(
                    name="slow_tool",
                    description="Simulates slow operation",
                    timeout_seconds=0.1,
                )

            async def execute(self, input_args):
                await asyncio.sleep(0.5)
                return ToolResult(success=True)

        registry.register(SlowTool())
        res_slow = await registry.execute("slow_tool")
        assert res_slow.success is False
        assert "timed out" in res_slow.error

    asyncio.run(_test())
