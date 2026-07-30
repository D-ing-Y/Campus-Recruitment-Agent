from mcp.server.fastmcp import FastMCP


mcp = FastMCP("campus-job-agent-test-math")


@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""

    return a + b


@mcp.tool()
def subtract(a: int, b: int) -> int:
    """Subtract two integers."""

    return a - b


if __name__ == "__main__":
    mcp.run(transport="stdio")
