"""Tool representations, permissions, and tool abstract interfaces."""

from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field


class PermissionLevel(str, Enum):
    """Permission level governing tool execution safety."""

    ALLOWED = "ALLOWED"
    CONFIRMATION_REQUIRED = "CONFIRMATION_REQUIRED"
    BLOCKED = "BLOCKED"


class ToolDefinition(BaseModel):
    """Specification of a tool available to the Agent and models."""

    name: str = Field(..., description="Unique tool identifier")
    description: str = Field(..., description="Description of tool functionality")
    input_schema: Dict[str, Any] = Field(
        default_factory=dict, description="JSON Schema for tool input arguments"
    )
    permission: PermissionLevel = Field(
        default=PermissionLevel.ALLOWED,
        description="Security permission tier for this tool",
    )
    timeout_seconds: float = Field(
        default=30.0, gt=0, description="Execution timeout in seconds"
    )


class ToolResult(BaseModel):
    """Structured result returned by tool execution."""

    success: bool = Field(..., description="Whether tool execution succeeded")
    output: Any = Field(default=None, description="Output payload or return value")
    error: Optional[str] = Field(default=None, description="Error message if execution failed")
    metadata: Dict[str, Any] = Field(
        default_factory=dict, description="Execution metadata e.g. latency, command, exit_code"
    )


class Tool(ABC):
    """Abstract base class for all Lamaru tools."""

    @property
    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Returns the metadata and schema definition of the tool."""
        pass

    @property
    def name(self) -> str:
        return self.definition.name

    @abstractmethod
    async def execute(self, input_args: Dict[str, Any]) -> ToolResult:
        """Execute the tool with validated arguments."""
        pass
