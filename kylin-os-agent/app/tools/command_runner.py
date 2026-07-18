from __future__ import annotations

import shutil
import subprocess
from typing import Any


class CommandRunner:
    """Runs explicitly whitelisted read-only commands without invoking a shell."""

    DEFAULT_ALLOWED_COMMANDS = {
        "df",
        "du",
        "ss",
        "netstat",
        "lsof",
        "ps",
        "journalctl",
        "systemctl",
        "tasklist",
    }

    BLOCKED_TOKENS = {
        "rm",
        "del",
        "erase",
        "rmdir",
        "rd",
        "format",
        "mkfs",
        "dd",
        "chmod",
        "chown",
        "shutdown",
        "reboot",
        "poweroff",
        "kill",
        "taskkill",
        "start",
        "stop",
        "restart",
        "reload",
        "enable",
        "disable",
        "sudo",
        "su",
        "cmd",
        "powershell",
        "pwsh",
        "bash",
        "sh",
    }

    def __init__(self, allowed_commands: set[str] | None = None) -> None:
        self.allowed_commands = allowed_commands or self.DEFAULT_ALLOWED_COMMANDS

    def run(self, args: list[str], timeout: int = 5) -> dict[str, Any]:
        if not args:
            return {"status": "error", "error": "empty command"}

        command = args[0].lower()
        if command not in self.allowed_commands:
            return {
                "status": "blocked",
                "command": args,
                "error": f"command is outside sandbox whitelist: {args[0]}",
            }

        blocked = self._blocked_tokens(args)
        if blocked:
            return {
                "status": "blocked",
                "command": args,
                "error": f"blocked dangerous token(s): {', '.join(blocked)}",
            }

        executable = shutil.which(args[0])
        if not executable:
            return {
                "status": "unavailable",
                "command": args,
                "error": f"command not found: {args[0]}",
            }

        try:
            completed = subprocess.run(
                [executable, *args[1:]],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "command": args, "error": "command timed out"}

        return {
            "status": "ok" if completed.returncode == 0 else "error",
            "command": args,
            "returncode": completed.returncode,
            "stdout": completed.stdout.strip(),
            "stderr": completed.stderr.strip(),
        }

    def _blocked_tokens(self, args: list[str]) -> list[str]:
        tokens = []
        for arg in args:
            normalized = arg.strip().lower()
            if normalized in self.BLOCKED_TOKENS:
                tokens.append(normalized)
        return tokens
