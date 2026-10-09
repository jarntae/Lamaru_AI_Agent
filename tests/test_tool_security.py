"""Tests for Phase 15 Tool Security: permissions, workspace containment, protected files, and command blacklists."""

import asyncio
from pathlib import Path
import tempfile
import pytest

from lamaru.domain.tools import PermissionLevel
from lamaru.tools.registry import create_default_tool_registry
from lamaru.tools.search import SearchFilesTool, SearchTextTool
from lamaru.tools.security import (
    CommandGuard,
    SecretSanitizer,
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


def test_protected_sensitive_files_blocked():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Create sensitive files in workspace root
            (tmp_path / ".env").write_text("GOOGLE_API_KEY=AIzaSySecret12345\n", encoding="utf-8")
            (tmp_path / ".env.local").write_text("SECRET=mysecret\n", encoding="utf-8")
            (tmp_path / "private.key").write_text("PRIVATE KEY DATA", encoding="utf-8")
            (tmp_path / "app.db").write_text("SQLITE DATA", encoding="utf-8")

            policy = ToolSecurityPolicy(workspace_root=tmpdir)
            registry = create_default_tool_registry(workspace_root=tmpdir, security_policy=policy)

            # 1. read_file on .env must be BLOCKED
            res_env = await registry.execute("read_file", {"path": ".env"})
            assert res_env.success is False
            assert res_env.metadata.get("blocked_by_security") is True
            assert "sensitive protected file is BLOCKED" in res_env.error

            # 2. read_file on .env.local must be BLOCKED
            res_env_local = await registry.execute("read_file", {"path": ".env.local"})
            assert res_env_local.success is False
            assert "sensitive protected file is BLOCKED" in res_env_local.error

            # 3. read_file on private.key must be BLOCKED
            res_key = await registry.execute("read_file", {"path": "private.key"})
            assert res_key.success is False
            assert "sensitive protected file is BLOCKED" in res_key.error

            # 4. read_file on app.db must be BLOCKED
            res_db = await registry.execute("read_file", {"path": "app.db"})
            assert res_db.success is False
            assert "sensitive protected file is BLOCKED" in res_db.error

            # 5. write_file / edit_file targeting .env must be BLOCKED
            res_write = await registry.execute("write_file", {"path": ".env", "content": "HACK"})
            assert res_write.success is False
            assert "sensitive protected file is BLOCKED" in res_write.error

    asyncio.run(_test())


def test_search_tools_exclude_sensitive_files():
    async def _test():
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            # Sensitive files
            (tmp_path / ".env").write_text("SECRET_TOKEN=MY_PASSWORD_1234\n", encoding="utf-8")
            (tmp_path / "secret.key").write_text("MY_PASSWORD_1234\n", encoding="utf-8")
            (tmp_path / "database.db").write_text("MY_PASSWORD_1234\n", encoding="utf-8")
            # Normal files
            (tmp_path / "app.py").write_text("MY_PASSWORD_1234 is not here, safe code\n", encoding="utf-8")

            search_files = SearchFilesTool(workspace_root=tmpdir)
            search_text = SearchTextTool(workspace_root=tmpdir)

            # 1. search_files must NOT return .env, secret.key, or database.db
            res_files = await search_files.execute({"pattern": "*"})
            assert res_files.success is True
            file_names = res_files.output
            assert "app.py" in file_names
            assert ".env" not in file_names
            assert "secret.key" not in file_names
            assert "database.db" not in file_names

            # 2. search_text must NOT match inside .env or secret.key
            res_text = await search_text.execute({"query": "MY_PASSWORD_1234"})
            assert res_text.success is True
            matching_files = [m["file"] for m in res_text.output]
            assert "app.py" in matching_files
            assert ".env" not in matching_files
            assert "secret.key" not in matching_files
            assert "database.db" not in matching_files

    asyncio.run(_test())


def test_secret_sanitizer_and_registry_masking():
    async def _test():
        # Test sanitizer directly
        text_with_google = "Connecting using key AIzaSyA123456789012345678901234567890 securely."
        sanitized = SecretSanitizer.sanitize_text(text_with_google)
        assert "AIzaSy" not in sanitized
        assert "[REDACTED_GOOGLE_API_KEY]" in sanitized

        text_with_openai = "OpenAI key sk-123456789012345678901234567890123456789012345678 loaded."
        sanitized_oa = SecretSanitizer.sanitize_text(text_with_openai)
        assert "sk-1234" not in sanitized_oa
        assert "[REDACTED_OPENAI_API_KEY]" in sanitized_oa

        # Test tool registry automatic masking on command output
        policy = ToolSecurityPolicy(tool_permissions={"run_command": PermissionLevel.ALLOWED})
        registry = create_default_tool_registry(security_policy=policy)

        res = await registry.execute(
            "run_command",
            {"command": "python -c \"print('Secret: AIzaSyA123456789012345678901234567890')\""},
        )
        assert res.success is True
        assert "AIzaSy" not in res.output
        assert "[REDACTED_GOOGLE_API_KEY]" in res.output

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
