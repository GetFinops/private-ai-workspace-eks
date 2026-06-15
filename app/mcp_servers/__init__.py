"""In-repo MCP servers (M12).

MCP servers run as sandboxed subprocesses spawned by the control-plane MCP
connection manager (app/control_plane/mcp.py). Keep servers stdlib-only and
side-effect-free unless they are an adopted, separately-reviewed integration
(NOTICE). The stub server here is a pure, no-network, no-credential validation
case.
"""
