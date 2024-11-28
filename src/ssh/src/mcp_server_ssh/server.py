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
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='/tmp/mcp_ssh_server.log'
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

async def create_ssh_connection(host: str, username: Optional[str] = None, port: Optional[int] = None) -> tuple[asyncssh.SSHClientConnection, str]:
    """Create a new SSH connection using AsyncSSH."""
    try:
        # Get the user's home directory and SSH config/known_hosts paths
        home = os.path.expanduser('~')
        known_hosts_path = os.path.join(home, '.ssh', 'known_hosts')
        config_path = os.path.join(home, '.ssh', 'config')
        key_path = os.path.join(home, '.ssh', 'id_rsa')

        # Prepare connection options
        options = {
            'known_hosts': known_hosts_path if os.path.exists(known_hosts_path) else None,
            'client_keys': [key_path] if os.path.exists(key_path) else None,
        }

        # Add config file if it exists
        if os.path.exists(config_path):
            options['config'] = [config_path]

        # Create unique session ID
        session_id = f"{host}_{len(SESSIONS)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        logger.debug(f"Attempting SSH connection to {host} with options: {options}")
        
        # Connect using AsyncSSH
        conn = await asyncssh.connect(
            host,
            username=username,
            port=port,
            **options
        )
        
        logger.debug(f"Successfully connected to {host}")
        return conn, session_id
        
    except asyncssh.DisconnectError as e:
        logger.error(f"SSH disconnect error: {str(e)}")
        raise McpError(INVALID_PARAMS, f"SSH connection failed: {str(e)}")
    except asyncssh.ProcessError as e:
        logger.error(f"SSH process error: {str(e)}")
        raise McpError(INVALID_PARAMS, f"SSH process error: {str(e)}")
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
                    await conn.close()
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
                    
                conn = SESSIONS[session_id]
                await conn.close()
                del SESSIONS[session_id]
                
                return [TextContent(type="text", text=f"Disconnected session: {session_id}")]
                
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