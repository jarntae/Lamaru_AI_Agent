"""Tests for Phase 15 Tool Security: permissions, workspace containment, and command blacklists."""

import asyncio
from pathlib import Path
import tempfile
import pytest

from lamaru.domain.tools import PermissionLevel
from lamaru.tools.registry import create_default_tool_registry
from lamaru.tools.security import (
    CommandGuard,
    SecurityViolationError,
    ToolSecurityPolicy,
    WorkspaceGuard,
)


def test_permission_tiers_enforcement():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = ToolSecurityPolicy(
                workspace_root=tmpdir,
                tool_permissions={
                    "read_file": PermissionLevel.ALLOWED,
                    "delete_file": PermissionLevel.CONFIRMATION_REQUIRED,
                    "run_command": PermissionLevel.BLOCKED,
                },
            )
            registry = create_default_tool_registry(workspace_root=tmpdir, security_policy=policy)

            # 1. ALLOWED tool (read_file on existing file)
            test_file = Path(tmpdir) / "test.txt"
            test_file.write_text("secure data", encoding="utf-8")

            res_read = await registry.execute("read_file", {"path": "test.txt"})
            assert res_read.success is True
            assert res_read.output == "secure data"

            # 2. CONFIRMATION_REQUIRED tool without confirmation
            res_delete_unconfirmed = await registry.execute(
                "delete_file", {"path": "test.txt"}, is_user_confirmed=False
            )
            assert res_delete_unconfirmed.success is False
            assert "requires user confirmation" in res_delete_unconfirmed.error
            assert test_file.exists()  # Not deleted

            # With confirmation -> succeeds
            res_delete_confirmed = await registry.execute(
                "delete_file", {"path": "test.txt"}, is_user_confirmed=True
            )
            assert res_delete_confirmed.success is True
            assert not test_file.exists()  # Now deleted

            # 3. BLOCKED tool fails regardless of confirmation
            res_blocked = await registry.execute(
                "run_command", {"command": "echo hi"}, is_user_confirmed=True
            )
            assert res_blocked.success is False
            assert "BLOCKED by security policy" in res_blocked.error

    asyncio.run(_test())


def test_workspace_boundary_guard():
    with tempfile.TemporaryDirectory() as tmpdir:
        guard = WorkspaceGuard(tmpdir)

        # Valid relative path
        valid_path = guard.validate_path("subdir/file.txt")
        assert valid_path.is_relative_to(Path(tmpdir).resolve())

        # Path traversal escape attempt
        with pytest.raises(SecurityViolationError) as exc_info:
            guard.validate_path("../../etc/passwd")
        assert "Path traversal detected" in str(exc_info.value)

        # External absolute path
        outside_path = "C:\\Windows\\System32" if Path("C:\\").exists() else "/etc"
        with pytest.raises(SecurityViolationError):
            guard.validate_path(outside_path)


def test_workspace_boundary_in_registry_execution():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = ToolSecurityPolicy(workspace_root=tmpdir)
            registry = create_default_tool_registry(workspace_root=tmpdir, security_policy=policy)

            # Try to read outside workspace
            res_escape = await registry.execute("read_file", {"path": "../../secret.txt"})
            assert res_escape.success is False
            assert res_escape.metadata.get("blocked_by_security") is True
            assert "Path traversal detected" in res_escape.error

            # Try to write outside workspace
            res_write_escape = await registry.execute(
                "write_file", {"path": "../outside.txt", "content": "malicious"}
            )
            assert res_write_escape.success is False
            assert "Path traversal detected" in res_write_escape.error

    asyncio.run(_test())


def test_destructive_command_blocking():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            policy = ToolSecurityPolicy(
                workspace_root=tmpdir,
                tool_permissions={"run_command": PermissionLevel.ALLOWED},
            )
            registry = create_default_tool_registry(workspace_root=tmpdir, security_policy=policy)

            # Benign command passes
            res_clean = await registry.execute(
                "run_command", {"command": "python -c \"print('safe')\""}
            )
            assert res_clean.success is True
            assert "safe" in res_clean.output

            # Dangerous destructive commands blocked
            dangerous_cmds = [
                "rm -rf /",
                "rm -rf ~",
                "rmdir /s /q c:\\",
                "format c:",
                ":(){ :|:& };:",
                "shutdown -s -t 0",
            ]

            for dangerous_cmd in dangerous_cmds:
                res_danger = await registry.execute("run_command", {"command": dangerous_cmd})
                assert res_danger.success is False
                assert "Execution BLOCKED: Command matches dangerous pattern" in res_danger.error

    asyncio.run(_test())


def test_model_cannot_override_policy():
    policy = ToolSecurityPolicy()
    # Even if model attempts to claim ALLOWED on delete_file, the policy default is preserved
    assert policy.get_permission("delete_file") == PermissionLevel.CONFIRMATION_REQUIRED
    assert policy.get_permission("run_command") == PermissionLevel.CONFIRMATION_REQUIRED
