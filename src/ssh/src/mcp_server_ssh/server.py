import sys
import os
import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime

import asyncssh
import aiofiles
from mcp.shared.exceptions import McpError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    PromptMessage,
    TextContent,
    Tool,
    INVALID_PARAMS,
)
from pydantic import BaseModel, Field

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='/tmp/mcp_ssh_server.log'
)
logger = logging.getLogger('mcp_ssh_server')

# Store for active SSH connections
SESSIONS: Dict[str, asyncssh.SSHClientConnection] = {}
LOGS_DIR = os.path.join(os.path.expanduser('~'), ".mcp_ssh_logs")
os.makedirs(LOGS_DIR, exist_ok=True)

class SSHConnect(BaseModel):
    host: str = Field(..., description="SSH host to connect to (can be from ~/.ssh/config)")
    keep_alive: bool = Field(True, description="Keep connection alive for future commands")
    username: Optional[str] = Field(None, description="Override SSH username")
    port: Optional[int] = Field(None, description="Override SSH port")

class SSHExec(BaseModel):
    session_id: str = Field(..., description="Session ID from ssh_connect")
    command: str = Field(..., description="Command to execute")

class SSHDisconnect(BaseModel):
    session_id: str = Field(..., description="Session ID to disconnect")

async def create_ssh_connection(host: str, username: Optional[str] = None, port: Optional[int] = None) -> tuple[asyncssh.SSHClientConnection, str]:
    """Create a new SSH connection using AsyncSSH."""
    try:
        # Use default SSH config file
        ssh_config = {}
        user_config_file = os.path.expanduser('~/.ssh/config')
        if os.path.exists(user_config_file):
            ssh_config = asyncssh.read_config(user_config_file)

        # Create unique session ID
        session_id = f"{host}_{len(SESSIONS)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        # Connect using AsyncSSH
        conn = await asyncssh.connect(
            host,
            username=username,
            port=port,
            config=ssh_config,
            known_hosts=None  # TODO: Implement known_hosts checking
        )

        return conn, session_id

    except Exception as e:
        logger.error(f"SSH connection error: {str(e)}")
        raise McpError(INVALID_PARAMS, f"Failed to establish SSH connection: {str(e)}")

async def serve() -> None:
    logger.info("Starting MCP SSH server...")
    server = Server("ssh")  # Changed to match bash server pattern

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        logger.debug("Listing tools")
        return [
            Tool(
                name="ssh_connect",  # Added server prefix
                description="Create a new SSH session",
                inputSchema=SSHConnect.model_json_schema(),
            ),
            Tool(
                name="ssh_exec",  # Added server prefix
                description="Execute a command in an existing SSH session",
                inputSchema=SSHExec.model_json_schema(),
            ),
            Tool(
                name="ssh_disconnect",  # Added server prefix
                description="Close an SSH session",
                inputSchema=SSHDisconnect.model_json_schema(),
            ),
            Tool(
                name="ssh_list",  # Added server prefix
                description="List active SSH sessions",
                inputSchema={},
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        logger.debug(f"Calling tool {name} with arguments {arguments}")
        try:
            if name == "ssh_connect":
                host = arguments["host"]
                keep_alive = arguments.get("keep_alive", True)
                username = arguments.get("username")
                port = arguments.get("port")

                conn, session_id = await create_ssh_connection(host, username, port)

                if keep_alive:
                    SESSIONS[session_id] = conn
                    return [TextContent(type="text", text=f"Connected to {host}\nSession ID: {session_id}")]
                else:
                    await conn.close()
                    return [TextContent(type="text", text=f"Connected to {host} and closed connection")]

            elif name == "ssh_exec":
                session_id = arguments["session_id"]
                command = arguments["command"]

                if session_id not in SESSIONS:
                    raise McpError(INVALID_PARAMS, f"No active session found for ID: {session_id}")

                conn = SESSIONS[session_id]

                result = await conn.run(command)
                output = ""
                if result.stdout:
                    output += f"Output:\n{result.stdout}"
                if result.stderr:
                    output += f"\nErrors:\n{result.stderr}"
                output += f"\nExit code: {result.exit_status}"

                return [TextContent(type="text", text=output)]

            elif name == "ssh_disconnect":
                session_id = arguments["session_id"]

                if session_id not in SESSIONS:
                    raise McpError(INVALID_PARAMS, f"No active session found for ID: {session_id}")

                conn = SESSIONS[session_id]
                await conn.close()
                del SESSIONS[session_id]

                return [TextContent(type="text", text=f"Disconnected session: {session_id}")]

            elif name == "ssh_list":
                if not SESSIONS:
                    return [TextContent(type="text", text="No active SSH sessions")]

                output = "Active SSH sessions:\n"
                for session_id, conn in SESSIONS.items():
                    output += f"- {session_id} ({conn.get_extra_info('peername')[0]})\n"
                return [TextContent(type="text", text=output)]

            raise McpError(INVALID_PARAMS, f"Unknown tool: {name}")

        except Exception as e:
            logger.error(f"Error in call_tool: {str(e)}")
            raise McpError(INVALID_PARAMS, str(e))

    try:
        logger.info("Initializing server...")
        options = server.create_initialization_options()
        async with stdio_server() as (read_stream, write_stream):
            logger.info("Starting server main loop...")
            await server.run(read_stream, write_stream, options, raise_exceptions=True)  # Added raise_exceptions
    except Exception as e:
        logger.error(f"Server error: {str(e)}", exc_info=True)
        raise

def main():
    try:
        # Ensure stdin/stdout are in binary mode
        if sys.platform == 'win32':
            import msvcrt
            msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)

        logger.info("Starting main function...")
        asyncio.run(serve())
    except Exception as e:
        logger.error(f"Main function error: {str(e)}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
