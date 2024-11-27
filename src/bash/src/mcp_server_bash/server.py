import sys
import os
import asyncio
import tempfile
import logging
from typing import Optional, Dict
from datetime import datetime

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
    INTERNAL_ERROR,
)
from pydantic import BaseModel, Field

# Set up logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename='/tmp/mcp_bash_server.log'
)
logger = logging.getLogger('mcp_bash_server')

# Store for background processes
PROCESSES: Dict[str, asyncio.Task] = {}
LOGS_DIR = os.path.join(tempfile.gettempdir(), "mcp_bash_logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Default environment configuration
DEFAULT_ENV = {
    'DISPLAY': ':0',
    'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/1000/bus',
    'HOME': '/home/jerome',
    'PWD': '/home/jerome',
    'PYTHONPATH': '/home/jerome/.claude/venv/bin/python',
    'PATH': '/home/jerome/.claude/venv/bin:/usr/local/bin:/usr/bin:/bin'
}

class Execute(BaseModel):
    command: str = Field(..., description="Bash command to execute")
    detach: bool = Field(False, description="Run in background")
    process_id: Optional[str] = Field(None, description="Process ID for checking status")

def get_log_path(process_id: str) -> str:
    return os.path.join(LOGS_DIR, f"{process_id}.log")

def get_environment():
    env = os.environ.copy()
    env.update(DEFAULT_ENV)
    return env

async def log_output(process_id: str, content: str):
    async with aiofiles.open(get_log_path(process_id), 'a') as f:
        await f.write(f"[{datetime.now().isoformat()}] {content}\n")

async def run_background_process(command: str, process_id: str):
    try:
        await log_output(process_id, "Starting background process...")
        await log_output(process_id, f"Command: {command}")
        
        # Wrap command to set working directory
        wrapped_command = f'cd /home/jerome && {command}'
        
        process = await asyncio.create_subprocess_shell(
            wrapped_command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=get_environment(),
            shell=True
        )
        
        stdout, stderr = await process.communicate()
        
        if stdout:
            await log_output(process_id, f"Output: {stdout.decode()}")
        if stderr:
            await log_output(process_id, f"Error: {stderr.decode()}")
            
        await log_output(process_id, f"Process exited with code: {process.returncode}")
            
    except Exception as e:
        await log_output(process_id, f"Error: {str(e)}")
    finally:
        if process_id in PROCESSES:
            del PROCESSES[process_id]

async def execute_bash(command: str) -> tuple[str, str, int]:
    # Wrap command to set working directory
    wrapped_command = f'cd /home/jerome && {command}'
    
    process = await asyncio.create_subprocess_shell(
        wrapped_command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=get_environment(),
        shell=True
    )
    
    stdout, stderr = await process.communicate()
    return stdout.decode(), stderr.decode(), process.returncode

async def serve() -> None:
    logger.info("Starting MCP Bash server...")
    server = Server("mcp-bash")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        logger.debug("Listing tools")
        return [
            Tool(
                name="execute_bash",  # Changed from "execute" to "execute_bash"
                description="""Executes bash commands in my environment and returns the output. Features:

- Standard command execution with immediate output
- Background process execution with detach=True
- Process monitoring and log retrieval
- Full file system access
- Access to system tools and utilities
- Error handling and logging

Examples:
1. Regular execution: {"command": "ls -la"}
2. Background process: {"command": "sleep 10 && notify-send 'Done!'", "detach": true}
3. Check process: {"process_id": "abc123"}""",
                inputSchema=Execute.model_json_schema(),
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        logger.debug(f"Calling tool {name} with arguments {arguments}")
        command = arguments.get("command")
        detach = arguments.get("detach", False)
        process_id = arguments.get("process_id")

        if process_id:
            if process_id not in PROCESSES:
                try:
                    async with aiofiles.open(get_log_path(process_id), 'r') as f:
                        logs = await f.read()
                    return [TextContent(type="text", text=f"Process logs:\n{logs}")]
                except FileNotFoundError:
                    return [TextContent(type="text", text=f"No logs found for process {process_id}")]
            else:
                return [TextContent(type="text", text=f"Process {process_id} is still running")]

        if not command:
            raise McpError(INVALID_PARAMS, "Command is required")

        if detach:
            process_id = f"bash_{len(PROCESSES)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            task = asyncio.create_task(run_background_process(command, process_id))
            PROCESSES[process_id] = task
            return [TextContent(type="text", text=f"Started background process {process_id}")]

        stdout, stderr, returncode = await execute_bash(command)
        output = ""
        if stdout:
            output += f"Output:\n{stdout}"
        if stderr:
            output += f"Errors:\n{stderr}"
        output += f"Exit code: {returncode}"
        
        return [TextContent(type="text", text=output if output else "No output")]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        if not arguments:
            raise McpError(INVALID_PARAMS, "Arguments required")

        result = await call_tool(name, arguments)
        return GetPromptResult(
            description="Bash Command Execution Result",
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