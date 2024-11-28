"""
MCP SSH Server with improved config handling
"""
import sys
import os
import asyncio
import logging
from typing import Dict, Optional
from datetime import datetime
from enum import Enum

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
    filename='/tmp/mcp_ssh_server.log',
    filemode='a',
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger('mcp_ssh_server')

# Store for active SSH connections
SESSIONS: Dict[str, asyncssh.SSHClientConnection] = {}

class CommandType(str, Enum):
    CONNECT = "connect"
    EXEC = "exec"
    DISCONNECT = "disconnect"
    LIST = "list"

class Execute(BaseModel):
    """Single model for all SSH operations"""
    command_type: CommandType = Field(..., description="Type of SSH operation")
    host: Optional[str] = Field(None, description="SSH host to connect to (for connect)")
    username: Optional[str] = Field(None, description="SSH username (for connect)")
    port: Optional[int] = Field(None, description="SSH port (for connect)")
    keep_alive: bool = Field(True, description="Keep connection alive (for connect)")
    session_id: Optional[str] = Field(None, description="Session ID (for exec/disconnect)")
    command: Optional[str] = Field(None, description="Command to execute (for exec)")

def parse_ssh_config(config_path: str, host: str) -> dict:
    """Parse SSH config file manually to extract host config."""
    try:
        with open(config_path, 'r') as f:
            lines = f.readlines()

        config = {}
        in_host_section = False

        for line in lines:
            line = line.strip()
            if not line or line.startswith('#'):
                continue

            if line.lower().startswith('host '):
                hosts = line[5:].strip().split()
                logger.debug(f"Found Host line: {hosts}")
                in_host_section = host in hosts
                if in_host_section:
                    logger.debug(f"Matched host {host}")
            elif in_host_section:
                try:
                    key, value = [x.strip() for x in line.split(None, 1)]
                    key = key.lower()
                    config[key] = value
                    logger.debug(f"Added config: {key}={value}")
                except ValueError:
                    continue

        logger.debug(f"Final parsed config for {host}: {config}")
        return config
    except Exception as e:
        logger.error(f"Error parsing SSH config: {str(e)}")
        return {}

async def create_ssh_connection(host: str, username: Optional[str] = None, port: Optional[int] = None) -> tuple[asyncssh.SSHClientConnection, str]:
    """Create a new SSH connection using AsyncSSH."""
    try:
        # Get the user's home directory and SSH paths
        home = os.path.expanduser('~')
        config_path = os.path.join(home, '.ssh', 'config')
        key_path = os.path.join(home, '.ssh', 'id_rsa')

        logger.debug(f"Initial connection parameters: host={host}, username={username}, port={port}")

        # Parse SSH config manually if we have a config file
        if os.path.exists(config_path):
            host_config = parse_ssh_config(config_path, host)
            logger.debug(f"Found host config: {host_config}")

            # Update connection parameters from config
            if not username and 'user' in host_config:
                username = host_config['user']
                logger.debug(f"Using username from config: {username}")
            if not port and 'port' in host_config:
                try:
                    port = int(host_config['port'])
                    logger.debug(f"Using port from config: {port}")
                except ValueError:
                    pass
            if 'hostname' in host_config:
                target_host = host_config['hostname']
                logger.debug(f"Using hostname from config: {target_host}")
            else:
                target_host = host

        else:
            target_host = host

        # Create unique session ID using original host (not resolved hostname)
        session_id = f"{host}_{len(SESSIONS)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        logger.debug(f"Final connection parameters: host={target_host}, username={username}, port={port}")

        # Connect with minimal options first
        try:
            conn = await asyncssh.connect(
                target_host,
                username=username,
                port=port or 22,
                known_hosts=None,
                client_keys=[key_path] if os.path.exists(key_path) else None
            )
            logger.debug("Successfully connected!")
            return conn, session_id
        except Exception as e:
            logger.error(f"Connection attempt failed: {str(e)}")
            raise

    except Exception as e:
        logger.error(f"SSH connection error: {str(e)}")
        raise McpError(INVALID_PARAMS, f"Failed to establish SSH connection: {str(e)}")

async def serve() -> None:
    logger.info("Starting MCP SSH server...")
    server = Server("mcp-ssh")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        logger.debug("Listing tools")
        return [
            Tool(
                name="execute_ssh",
                description="""Executes SSH operations including connecting to hosts, running commands, and managing sessions. Features:

- Create and manage persistent SSH connections
- Execute commands on remote machines
- List active sessions
- Disconnect from sessions
- Full SSH config support

Examples:
1. Connect: {"command_type": "connect", "host": "rpi1"}
2. Execute: {"command_type": "exec", "session_id": "rpi1_123", "command": "ls -la"}
3. List: {"command_type": "list"}
4. Disconnect: {"command_type": "disconnect", "session_id": "rpi1_123"}""",
                inputSchema=Execute.model_json_schema(),
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        logger.debug(f"Calling tool {name} with arguments {arguments}")

        command_type = arguments.get("command_type")
        if not command_type:
            raise McpError(INVALID_PARAMS, "command_type is required")

        try:
            if command_type == CommandType.CONNECT:
                host = arguments.get("host")
                if not host:
                    raise McpError(INVALID_PARAMS, "host is required for connect")

                keep_alive = arguments.get("keep_alive", True)
                username = arguments.get("username")
                port = arguments.get("port")

                conn, session_id = await create_ssh_connection(host, username, port)

                if keep_alive:
                    SESSIONS[session_id] = conn
                    return [TextContent(type="text", text=f"Connected to {host}\nSession ID: {session_id}")]
                else:
                    conn.close()  # Non-awaited close for non-kept connections
                    return [TextContent(type="text", text=f"Connected to {host} and closed connection")]

            elif command_type == CommandType.EXEC:
                session_id = arguments.get("session_id")
                command = arguments.get("command")

                if not session_id or not command:
                    raise McpError(INVALID_PARAMS, "session_id and command are required for exec")

                if session_id not in SESSIONS:
                    raise McpError(INVALID_PARAMS, f"No active session found for ID: {session_id}")

                conn = SESSIONS[session_id]

                try:
                    result = await conn.run(command)
                    output = ""
                    if result.stdout:
                        output += f"Output:\n{result.stdout}"
                    if result.stderr:
                        output += f"\nErrors:\n{result.stderr}"
                    output += f"\nExit code: {result.exit_status}"
                    return [TextContent(type="text", text=output)]
                except asyncssh.ProcessError as e:
                    return [TextContent(type="text", text=f"Command failed: {str(e)}")]

            elif command_type == CommandType.DISCONNECT:
                session_id = arguments.get("session_id")
                if not session_id:
                    raise McpError(INVALID_PARAMS, "session_id is required for disconnect")

                if session_id not in SESSIONS:
                    raise McpError(INVALID_PARAMS, f"No active session found for ID: {session_id}")

                try:
                    conn = SESSIONS[session_id]
                    # First remove from sessions dict to prevent any new commands
                    del SESSIONS[session_id]
                    # Then close the connection without awaiting
                    conn.close()
                    logger.debug(f"Successfully closed session {session_id}")
                    return [TextContent(type="text", text=f"Disconnected session: {session_id}")]
                except Exception as e:
                    logger.error(f"Error closing session {session_id}: {str(e)}")
                    # If we failed to close properly, don't keep it in active sessions
                    if session_id in SESSIONS:
                        del SESSIONS[session_id]
                    raise McpError(INVALID_PARAMS, f"Error closing session: {str(e)}")

            elif command_type == CommandType.LIST:
                if not SESSIONS:
                    return [TextContent(type="text", text="No active SSH sessions")]

                output = "Active SSH sessions:\n"
                for session_id, conn in SESSIONS.items():
                    try:
                        peername = conn.get_extra_info('peername')[0]
                        output += f"- {session_id} ({peername})\n"
                    except:
                        output += f"- {session_id} (connection info unavailable)\n"
                return [TextContent(type="text", text=output)]

            raise McpError(INVALID_PARAMS, f"Invalid command_type: {command_type}")

        except Exception as e:
            logger.error(f"Error in call_tool: {str(e)}")
            raise McpError(INVALID_PARAMS, str(e))

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        if not arguments:
            raise McpError(INVALID_PARAMS, "Arguments required")

        result = await call_tool(name, arguments)
        return GetPromptResult(
            description="SSH Operation Result",
            messages=[PromptMessage(role="user", content=result[0])]
        )

    try:
        logger.info("Initializing server...")
        options = server.create_initialization_options()
        async with stdio_server() as (read_stream, write_stream):
            logger.info("Starting server main loop...")
            await server.run(read_stream, write_stream, options, raise_exceptions=True)
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