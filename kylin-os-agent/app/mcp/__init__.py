"""MCP (Model Context Protocol) server module.

Exposes a JSON-RPC 2.0 interface for listing and calling tools.
All tool invocations route through the existing ToolRegistry so that
the application-layer Safety Guard and sandbox rules still apply.
"""
