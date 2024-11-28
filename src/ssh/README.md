# MCP SSH Server

This is an MCP server implementation that allows managing persistent SSH connections to remote machines.

## Features

- Create and manage persistent SSH connections
- Execute commands on remote machines
- Interactive SSH sessions
- Session management (list active connections, terminate connections)
- Full SSH config support (.ssh/config)
- Key-based authentication
- Error handling and logging

## Installation

```bash
pip install -e .
```

## Configuration

Add to your Claude Desktop config:

```json
{
    "ssh": {
        "command": "uvx",
        "args": ["mcp-server-ssh"]
    }
}
```

## Usage

The server exposes several tools:

1. ssh_connect: Create a new SSH session
2. ssh_exec: Execute a command in an existing session
3. ssh_list: List active SSH sessions
4. ssh_disconnect: Close an SSH session

### Examples

1. Connect to a host:
```json
{
    "host": "rpi1",
    "keep_alive": true
}
```

2. Execute command in session:
```json
{
    "session_id": "rpi1_123",
    "command": "ls -la"
}
```

3. List active sessions:
```json
{}
```

4. Disconnect session:
```json
{
    "session_id": "rpi1_123"
}
```

## Security Notes

- Uses standard SSH security mechanisms (keys, known_hosts)
- Sessions are isolated per connection
- Credentials are never stored, only SSH keys are used
- Consider implementing connection whitelisting for production use