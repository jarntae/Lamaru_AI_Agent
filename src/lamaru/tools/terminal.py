"""Terminal execution tool: run_command."""

import asyncio
import os
from pathlib import Path
import re
from typing import Any, Dict, Optional

from lamaru.domain.tools import PermissionLevel, Tool, ToolDefinition, ToolResult

SENSITIVE_ENV_PATTERNS = [
    r".*API_KEY.*",
    r".*SECRET.*",
    r".*TOKEN.*",
    r".*PASSWORD.*",
    r".*CREDENTIAL.*",
]


def get_sanitized_env() -> Dict[str, str]:
    """Return a clean copy of environment variables stripped of API keys and secrets."""
    clean_env: Dict[str, str] = {}
    for k, v in os.environ.items():
        if any(re.search(pat, k, re.IGNORECASE) for pat in SENSITIVE_ENV_PATTERNS):
            continue
        clean_env[k] = v
    return clean_env


class RunCommandTool(Tool):
    """Tool to execute a shell command with timeout and output capture."""

    def __init__(
        self,
        workspace_root: Optional[str] = None,
        default_permission: PermissionLevel = PermissionLevel.CONFIRMATION_REQUIRED,
    ) -> None:
        self.workspace_root = Path(workspace_root).resolve() if workspace_root else None
        self._permission = default_permission

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="run_command",
            description="Run a terminal shell command and return stdout, stderr, and exit code.",
            input_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The command string to execute"},
                    "cwd": {"type": "string", "description": "Working directory (optional)"},
                    "timeout_seconds": {"type": "number", "description": "Command timeout in seconds (optional)"},
                },
                "required": ["command"],
            },
            permission=self._permission,
            timeout_seconds=60.0,
        )

    def _resolve_cwd(self, cwd_str: Optional[str]) -> Optional[str]:
        if not cwd_str:
            return str(self.workspace_root) if self.workspace_root else None
        p = Path(cwd_str)
        if not p.is_absolute() and self.workspace_root:
            p = self.workspace_root / p
        return str(p.resolve())

    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        command = input_args.get("command")
        if not command:
            return ToolResult(success=False, error="Parameter 'command' is required.")

        cwd = self._resolve_cwd(input_args.get("cwd"))
        timeout = float(input_args.get("timeout_seconds") or self.definition.timeout_seconds)

        try:
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=get_sanitized_env(),
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
                    error=f"Command timed out after {timeout} seconds.",
                    metadata={"command": command, "timeout": timeout},
                )

            stdout = stdout_data.decode("utf-8", errors="replace").strip()
            stderr = stderr_data.decode("utf-8", errors="replace").strip()
            exit_code = process.returncode or 0

            return ToolResult(
                success=(exit_code == 0),
                output=stdout,
                error=stderr if exit_code != 0 else None,
                metadata={
                    "command": command,
                    "exit_code": exit_code,
                    "cwd": cwd,
                    "stderr": stderr,
                },
            )
        except Exception as e:
            return ToolResult(
                success=False,
                error=f"Failed to execute command: {str(e)}",
                metadata={"command": command},
            )
