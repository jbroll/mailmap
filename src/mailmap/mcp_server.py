"""MCP server for email classification tools."""

import asyncio
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent

from .config import Config
from .database import Database, Folder
from .llm import OllamaClient


def create_mcp_server(config: Config, db: Database) -> Server:
    """Create and configure the MCP server with classification tools."""
    server = Server("mailmap")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name="classify_email",
                description="Classify an email into the most appropriate folder based on its content",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "message_id": {
                            "type": "string",
                            "description": "Unique identifier for the email",
                        },
                        "subject": {
                            "type": "string",
                            "description": "Email subject line",
                        },
                        "from_addr": {
                            "type": "string",
                            "description": "Sender email address",
                        },
                        "body": {
                            "type": "string",
                            "description": "Email body text",
                        },
                    },
                    "required": ["message_id", "subject", "from_addr", "body"],
                },
            ),
            Tool(
                name="update_folder_descriptions",
                description="Generate or update descriptions for email folders based on sample emails",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "folder_id": {
                            "type": "string",
                            "description": "Folder identifier to update",
                        },
                        "sample_emails": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "subject": {"type": "string"},
                                    "from_addr": {"type": "string"},
                                    "body": {"type": "string"},
                                },
                            },
                            "description": "Sample emails from the folder",
                        },
                    },
                    "required": ["folder_id", "sample_emails"],
                },
            ),
            Tool(
                name="get_folder_descriptions",
                description="Get current descriptions for all folders",
                inputSchema={
                    "type": "object",
                    "properties": {},
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "classify_email":
            return await _classify_email(config, db, arguments)
        elif name == "update_folder_descriptions":
            return await _update_folder_descriptions(config, db, arguments)
        elif name == "get_folder_descriptions":
            return await _get_folder_descriptions(db)
        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    return server


async def _classify_email(
    config: Config, db: Database, arguments: dict
) -> list[TextContent]:
    """Classify an email using the LLM."""
    message_id = arguments["message_id"]
    subject = arguments["subject"]
    from_addr = arguments["from_addr"]
    body = arguments["body"]

    folder_descriptions = db.get_folder_descriptions()

    if not folder_descriptions:
        return [
            TextContent(
                type="text",
                text='{"error": "No folder descriptions available. Run update_folder_descriptions first."}',
            )
        ]

    async with OllamaClient(config.ollama) as llm:
        result = await llm.classify_email(subject, from_addr, body, folder_descriptions)

    db.update_classification(message_id, result.predicted_folder, result.confidence)

    import json

    response = {
        "message_id": message_id,
        "predicted_folder": result.predicted_folder,
        "secondary_labels": result.secondary_labels,
        "confidence": result.confidence,
    }
    return [TextContent(type="text", text=json.dumps(response))]


async def _update_folder_descriptions(
    config: Config, db: Database, arguments: dict
) -> list[TextContent]:
    """Update folder description using the LLM."""
    folder_id = arguments["folder_id"]
    sample_emails = arguments["sample_emails"]

    async with OllamaClient(config.ollama) as llm:
        result = await llm.generate_folder_description(folder_id, sample_emails)

    folder = Folder(
        folder_id=folder_id,
        name=folder_id,
        description=result.description,
        last_updated=datetime.now(),
    )
    db.upsert_folder(folder)

    import json

    response = {
        "folder_id": folder_id,
        "description": result.description,
    }
    return [TextContent(type="text", text=json.dumps(response))]


async def _get_folder_descriptions(db: Database) -> list[TextContent]:
    """Get all folder descriptions."""
    import json

    descriptions = db.get_folder_descriptions()
    return [TextContent(type="text", text=json.dumps(descriptions))]


async def run_mcp_server(config: Config, db: Database) -> None:
    """Run the MCP server over stdio."""
    server = create_mcp_server(config, db)
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
