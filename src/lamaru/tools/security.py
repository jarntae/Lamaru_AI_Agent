"""Security enforcement: permissions, workspace boundary containment, and command blacklists."""

import os
from pathlib import Path
import re
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from lamaru.domain.tools import PermissionLevel, ToolDefinition, ToolResult


class SecurityViolationError(Exception):
    """Raised when an action violates security boundaries or policies."""
    pass


class WorkspaceGuard:
    """Enforces that file paths remain strictly inside the authorized workspace boundary."""

    def __init__(self, workspace_root: str) -> None:
        self.workspace_root = Path(workspace_root).resolve()

    def validate_path(self, target_path: str) -> Path:
        """Validate that target_path resolves strictly within workspace_root.

        Raises SecurityViolationError if the path attempts traversal or lies outside.
        """
        p = Path(target_path)
        if not p.is_absolute():
            resolved = (self.workspace_root / p).resolve()
        else:
            resolved = p.resolve()

        try:
            # Check relative_to
            resolved.relative_to(self.workspace_root)
        except ValueError:
            raise SecurityViolationError(
                f"Path traversal detected or path outside workspace boundary: {target_path} -> {resolved}"
            )

        return resolved


class CommandGuard:
    """Inspects shell commands to block catastrophic or destructive operations."""

    DESTRUCTIVE_PATTERNS = [
        r"\brm\s+-[rRfF]+\s+[/~]",              # rm -rf / or ~
        r"\brmdir\s+/[sS]\s+/[qQ]\s+[cC]:",     # rmdir /s /q c:
        r"\bmkfs\b",                            # format drives in unix
        r"\bformat\s+[a-zA-Z]:",                # format drive in windows
        r"\bdd\s+if=.*of=/dev/[sh]d",           # raw drive wipe
        r":\(\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", # fork bomb
        r"\b(shutdown|reboot|poweroff)\b",      # system shutdown
        r"\bdel\s+/[fFqQsS]+\s+[cC]:\\",        # del /f /s /q c:\
    ]

    def __init__(self, blocked_patterns: Optional[List[str]] = None) -> None:
        self.blocked_patterns = (blocked_patterns or []) + self.DESTRUCTIVE_PATTERNS

    def check_command(self, command: str) -> Optional[str]:
        """Check if command matches blocked destructive patterns.

        Returns matching reason if blocked, None if clean.
        """
        for pat in self.blocked_patterns:
            if re.search(pat, command, re.IGNORECASE):
                return f"Command matches dangerous pattern: '{pat}'"
        return None


class ToolSecurityPolicy:
    """Configurable security policy for agent tools."""

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        tool_permissions: Optional[Dict[str, PermissionLevel]] = None,
        allow_destructive_commands: bool = False,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self.workspace_guard = WorkspaceGuard(str(self.workspace_root)) if self.workspace_root else None
        self.command_guard = CommandGuard()
        self.allow_destructive_commands = allow_destructive_commands

        # Tool permission overrides
        self.tool_permissions: Dict[str, PermissionLevel] = {
            "read_file": PermissionLevel.ALLOWED,
            "list_directory": PermissionLevel.ALLOWED,
            "search_files": PermissionLevel.ALLOWED,
            "search_text": PermissionLevel.ALLOWED,
            "git_status": PermissionLevel.ALLOWED,
            "git_diff": PermissionLevel.ALLOWED,
            "git_log": PermissionLevel.ALLOWED,
            "git_branch": PermissionLevel.ALLOWED,
            "write_file": PermissionLevel.ALLOWED,
            "edit_file": PermissionLevel.ALLOWED,
            "git_commit": PermissionLevel.ALLOWED,
            "delete_file": PermissionLevel.CONFIRMATION_REQUIRED,
            "run_command": PermissionLevel.CONFIRMATION_REQUIRED,
        }
        if tool_permissions:
            self.tool_permissions.update(tool_permissions)

    def get_permission(self, tool_name: str) -> PermissionLevel:
        """Get enforced permission level for tool (model cannot override)."""
        return self.tool_permissions.get(tool_name, PermissionLevel.CONFIRMATION_REQUIRED)

    def set_permission(self, tool_name: str, level: PermissionLevel) -> None:
        """Explicitly set policy permission level for a tool."""
        self.tool_permissions[tool_name] = level

    def authorize_execution(
        self,
        tool_definition: ToolDefinition,
        arguments: Dict[str, Any],
        is_user_confirmed: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """Authorize tool execution based on permission tier and argument validation.

        Returns (is_authorized, rejection_reason).
        """
        # 1. Enforced permission check
        permission = self.get_permission(tool_definition.name)

        if permission == PermissionLevel.BLOCKED:
            return False, f"Tool '{tool_definition.name}' is BLOCKED by security policy."

        if permission == PermissionLevel.CONFIRMATION_REQUIRED and not is_user_confirmed:
            return False, f"Tool '{tool_definition.name}' requires user confirmation before execution."

        # 2. Filesystem boundary check
        if self.workspace_guard and "path" in arguments and isinstance(arguments["path"], str):
            try:
                self.workspace_guard.validate_path(arguments["path"])
            except SecurityViolationError as sve:
                return False, str(sve)

        # 3. Terminal destructive command check
        if tool_definition.name == "run_command" and "command" in arguments:
            cmd = arguments["command"]
            blocked_reason = self.command_guard.check_command(cmd)
            if blocked_reason and not self.allow_destructive_commands:
                return False, f"Execution BLOCKED: {blocked_reason}"

        return True, None

