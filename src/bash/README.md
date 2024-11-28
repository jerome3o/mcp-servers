# MCP Bash Server

This is an MCP server implementation that allows executing bash commands in both synchronous and asynchronous modes.

## Features

- Execute bash commands and get output
- Run commands in background with process monitoring
- Process logs stored in /tmp/mcp_bash_logs
- System integration (display, dbus support)
- Full error handling and logging
- Environment variable management

## Installation

```bash
pip install -e .
```

## Configuration

Add to your Claude Desktop config:

```json
{
    "bash": {
        "command": "uvx",
        "args": ["mcp-server-bash"]
    }
}
```

## Usage

The server exposes an "execute" tool that accepts:

- `command`: Bash command to execute
- `detach`: Boolean for background execution
- `process_id`: For checking status of background tasks

### Examples

1. Regular command execution:
```json
{
    "command": "ls -la"
}
```

2. Background process:
```json
{
    "command": "sleep 10 && notify-send 'Done!'",
    "detach": true
}
```

3. Check process status:
```json
{
    "process_id": "bash_0_20240327_123456"
}
```

## Security Notes

- Commands are executed with the same permissions as the Claude Desktop process
- Be careful with destructive commands or commands that may expose sensitive information
- Consider implementing command whitelisting for production use