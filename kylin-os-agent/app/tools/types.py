from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


@dataclass
class ToolSpec:
    name: str
    description: str
    risk_level: str
    permission: str
    read_only: bool
    handler: ToolHandler
    execution_mode: str = "auto"
    sandbox_scope: str = "app_readonly"
    allowed_commands: tuple[str, ...] = ()

    def public_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "risk_level": self.risk_level,
            "permission": self.permission,
            "read_only": self.read_only,
            "execution_mode": self.execution_mode,
            "sandbox_scope": self.sandbox_scope,
            "allowed_commands": list(self.allowed_commands),
        }
