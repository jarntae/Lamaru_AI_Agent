"""ToolRegistry: Registry and execution dispatcher for Lamaru agent tools."""

import asyncio
import logging
from typing import Any, Dict, Iterable, List, Optional

from lamaru.domain.tools import Tool, ToolDefinition, ToolResult
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
from lamaru.tools.search import SearchFilesTool, SearchTextTool
from lamaru.tools.security import ToolSecurityPolicy
from lamaru.tools.terminal import RunCommandTool

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Registry managing available tools and executing them with security and timeout protection."""

    def __init__(self, security_policy: Optional[ToolSecurityPolicy] = None) -> None:
        self._tools: Dict[str, Tool] = {}
        self.security_policy = security_policy

    def register(self, tool: Tool) -> None:
        """Register a new tool instance."""
        name = tool.definition.name
        if name in self._tools:
            logger.warning("Overwriting existing tool registration: %s", name)
        self._tools[name] = tool

    def register_all(self, tools: Iterable[Tool]) -> None:
        """Register multiple tools at once."""
        for t in tools:
            self.register(t)

    def get(self, name: str) -> Optional[Tool]:
        """Retrieve a registered tool by its identifier."""
        return self._tools.get(name)

    def list_tools(self) -> List[Tool]:
        """Return list of all registered tool instances."""
        return list(self._tools.values())

    def get_definitions(self) -> List[ToolDefinition]:
        """Return definitions of all registered tools."""
        return [tool.definition for tool in self._tools.values()]

    async def execute(
        self,
        name: str,
        arguments: Optional[Dict[str, Any]] = None,
        is_user_confirmed: bool = False,
    ) -> ToolResult:
        """Execute a tool by name with security authorization and timeout protection."""
        tool = self.get(name)
        if not tool:
            return ToolResult(
                success=False,
                error=f"Tool '{name}' is not registered.",
                metadata={"tool_name": name},
            )

        args = arguments or {}

        # Tool security policy check
        if self.security_policy:
            authorized, reason = self.security_policy.authorize_execution(
                tool.definition, args, is_user_confirmed=is_user_confirmed
            )
            if not authorized:
                return ToolResult(
                    success=False,
                    error=reason,
                    metadata={"tool_name": name, "blocked_by_security": True},
                )

        timeout = tool.definition.timeout_seconds

        try:
            result = await asyncio.wait_for(tool.execute(args), timeout=timeout)
            return result
        except asyncio.TimeoutError:
            return ToolResult(
                success=False,
                error=f"Execution of tool '{name}' timed out after {timeout} seconds.",
                metadata={"tool_name": name, "timeout": timeout},
            )
        except Exception as e:
            logger.exception("Unexpected error executing tool %s: %s", name, e)
            return ToolResult(
                success=False,
                error=f"Unexpected error executing tool '{name}': {str(e)}",
                metadata={"tool_name": name},
            )


def create_default_tool_registry(
    workspace_root: Optional[str] = None,
    security_policy: Optional[ToolSecurityPolicy] = None,
) -> ToolRegistry:
    """Create and return a ToolRegistry populated with all standard native tools."""
    policy = security_policy
    if policy is None and workspace_root is not None:
        policy = ToolSecurityPolicy(workspace_root=workspace_root)

    registry = ToolRegistry(security_policy=policy)
    tools: List[Tool] = [
        # Filesystem
        ReadFileTool(workspace_root=workspace_root),
        WriteFileTool(workspace_root=workspace_root),
        EditFileTool(workspace_root=workspace_root),
        DeleteFileTool(workspace_root=workspace_root),
        ListDirectoryTool(workspace_root=workspace_root),
        # Search
        SearchFilesTool(workspace_root=workspace_root),
        SearchTextTool(workspace_root=workspace_root),
        # Terminal
        RunCommandTool(workspace_root=workspace_root),
        # Git
        GitStatusTool(workspace_root=workspace_root),
        GitDiffTool(workspace_root=workspace_root),
        GitLogTool(workspace_root=workspace_root),
        GitBranchTool(workspace_root=workspace_root),
        GitCommitTool(workspace_root=workspace_root),
    ]
    registry.register_all(tools)
    return registry
