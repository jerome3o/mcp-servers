import sys
import io
import os
import asyncio
import contextlib
import tempfile
from typing import Optional, Dict
from datetime import datetime

import aiofiles
from mcp.shared.exceptions import McpError
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    GetPromptResult,
    Prompt,
    PromptArgument,
    PromptMessage,
    TextContent,
    Tool,
    INVALID_PARAMS,
    INTERNAL_ERROR,
)
from pydantic import BaseModel, Field

# Store for background processes
PROCESSES: Dict[str, asyncio.Task] = {}
LOGS_DIR = os.path.join(tempfile.gettempdir(), "mcp_python_logs")
os.makedirs(LOGS_DIR, exist_ok=True)

class Execute(BaseModel):
    code: str = Field(..., description="Python code to execute")
    detach: bool = Field(False, description="Run in background")
    process_id: Optional[str] = Field(None, description="Process ID for checking status")

def get_log_path(process_id: str) -> str:
    return os.path.join(LOGS_DIR, f"{process_id}.log")

async def log_output(process_id: str, content: str):
    async with aiofiles.open(get_log_path(process_id), 'a') as f:
        await f.write(f"[{datetime.now().isoformat()}] {content}\n")

async def run_background_process(code: str, process_id: str):
    try:
        # Indent the code to make it a function body
        indented_code = "\n".join(f"    {line}" for line in code.splitlines())
        wrapper_code = f"""import asyncio
import os
os.environ['DISPLAY'] = ':0'
os.environ['DBUS_SESSION_BUS_ADDRESS'] = os.environ.get('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')

async def _task():
{indented_code}

asyncio.run(_task())
"""
        await log_output(process_id, "Starting background process...")
        await log_output(process_id, f"Code:\n{wrapper_code}")

        # Execute in separate process
        process = await asyncio.create_subprocess_exec(
            sys.executable, '-c', wrapper_code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if stdout:
            await log_output(process_id, f"Output: {stdout.decode()}")
        if stderr:
            await log_output(process_id, f"Error: {stderr.decode()}")

    except Exception as e:
        await log_output(process_id, f"Error: {str(e)}")
    finally:
        if process_id in PROCESSES:
            del PROCESSES[process_id]

def execute_python(code: str) -> tuple[str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()

    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exec(code, {}, {})
        except Exception as e:
            print(f"Error: {str(e)}", file=sys.stderr)

    return stdout.getvalue(), stderr.getvalue()

async def serve() -> None:
    server = Server("mcp-python")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="execute_python",
                description="""Executes Python code in my environment and returns the output. Features:

- Standard code execution with immediate output
- Background process execution with detach=True (runs in separate process)
- Process monitoring and log retrieval
- Full file I/O support
- Access to notify-send and other system tools
- Error handling and logging

Examples:
1. Regular execution: {"code": "print('hello')"}
2. Background notification: {"code": "import subprocess\\nsubprocess.run(['notify-send', 'Test', 'Hello!'])", "detach": true}
3. Check process: {"process_id": "abc123"}""",
                inputSchema=Execute.model_json_schema(),
            )
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        code = arguments.get("code")
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

        if not code:
            raise McpError(INVALID_PARAMS, "Code is required")

        if detach:
            process_id = f"py_{len(PROCESSES)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            task = asyncio.create_task(run_background_process(code, process_id))
            PROCESSES[process_id] = task
            return [TextContent(type="text", text=f"Started background process {process_id}")]

        stdout, stderr = execute_python(code)
        output = ""
        if stdout:
            output += f"Output:\n{stdout}"
        if stderr:
            output += f"Errors:\n{stderr}"

        return [TextContent(type="text", text=output if output else "No output")]

    @server.get_prompt()
    async def get_prompt(name: str, arguments: dict | None) -> GetPromptResult:
        if not arguments:
            raise McpError(INVALID_PARAMS, "Arguments required")

        result = await call_tool(name, arguments)
        return GetPromptResult(
            description="Python Code Execution Result",
            messages=[PromptMessage(role="user", content=result[0])]
        )

    options = server.create_initialization_options()
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, options, raise_exceptions=True)
