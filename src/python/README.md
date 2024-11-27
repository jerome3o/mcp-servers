# MCP Python Server

An MCP server that allows executing Python code.

## Features

- Execute Python code snippets
- Capture stdout and stderr
- Safe execution in isolated namespace

## Usage

The server exposes a single tool called `execute` that accepts Python code as input and returns the execution output.

Example:

```python
code = """
print('Hello, World!')
x = 5 + 3
print(f'Result: {x}')
"""

# Using the tool
result = await call_tool("execute", {"code": code})

# Using the prompt
result = await get_prompt("execute", {"code": code})
```

## Security

Code is executed in an isolated namespace to prevent access to the server's environment.