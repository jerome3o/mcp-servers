"""Main entry point for the MCP Python server."""
from . import serve

if __name__ == "__main__":
    import asyncio
    asyncio.run(serve())