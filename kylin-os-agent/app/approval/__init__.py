"""Approval service for confirm-class tools.

When a user request maps to a tool with execution_mode == "confirm",
the service creates a pending approval record instead of running the tool.
A human reviews and decides via /api/approvals/{id}/approve|reject.
"""
