import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tools import (
    add_source_to_wiki,
    add_text_document,
    get_wiki_status,
    lint_wiki,
    list_knowledge_bases,
    list_answer_feedback,
    list_retrieval_strategies,
    list_wiki_pages,
    query_wiki,
    read_wiki_page,
    search_knowledge_base,
    search_with_strategy,
    synthesize_knowledge,
)


mcp = FastMCP("AgentKB MCP")


mcp.tool()(search_knowledge_base)
mcp.tool()(list_knowledge_bases)
mcp.tool()(add_text_document)
mcp.tool()(add_source_to_wiki)
mcp.tool()(get_wiki_status)
mcp.tool()(list_wiki_pages)
mcp.tool()(read_wiki_page)
mcp.tool()(query_wiki)
mcp.tool()(search_with_strategy)
mcp.tool()(list_retrieval_strategies)
mcp.tool()(list_answer_feedback)
mcp.tool()(synthesize_knowledge)
mcp.tool()(lint_wiki)


if __name__ == "__main__":
    mcp.run()
