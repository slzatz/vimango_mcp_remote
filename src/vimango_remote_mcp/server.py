"""MCP server implementation for vimango remote (PostgreSQL backend with HTTP/SSE transport)."""

import asyncio
import json
from pathlib import Path
from typing import Any
from mcp.server import Server
from mcp.server.sse import SseServerTransport
from mcp.types import Tool, TextContent
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import Response, JSONResponse
import uvicorn

from .db import VimangoDatabase, load_config


# Initialize MCP server
app = Server("vimango-remote-mcp")

# Global database instance
db: VimangoDatabase = None

# SSE transport instance
sse_transport: SseServerTransport = None

# API key for authentication
api_key: str = None


def check_auth(request) -> JSONResponse | None:
    """Check authentication from request. Returns error response or None if OK."""
    token = None

    # Check Authorization header first
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]  # Strip "Bearer " prefix

    # Fall back to query parameter
    if not token:
        token = request.query_params.get("api_key")

    if not token:
        return JSONResponse(
            {"error": "Missing authentication (Bearer header or api_key param)"},
            status_code=401
        )

    if token != api_key:
        return JSONResponse(
            {"error": "Invalid API key"},
            status_code=401
        )

    return None


@app.list_tools()
async def list_tools() -> list[Tool]:
    """List available MCP tools."""
    return [
        Tool(
            name="create_note",
            description="Create a new note in vimango with title and markdown body",
            inputSchema={
                "type": "object",
                "properties": {
                    "title": {
                        "type": "string",
                        "description": "Note title"
                    },
                    "note": {
                        "type": "string",
                        "description": "Note body (markdown format)"
                    },
                    "context": {
                        "type": "string",
                        "description": "Context name (optional, defaults to 'none')",
                        "default": "none"
                    },
                    "folder": {
                        "type": "string",
                        "description": "Folder name (optional, defaults to 'none')",
                        "default": "none"
                    },
                    "star": {
                        "type": "boolean",
                        "description": "Star/favorite the note (optional)",
                        "default": False
                    }
                },
                "required": ["title", "note"]
            }
        ),
        Tool(
            name="list_contexts",
            description="List all available contexts for categorizing notes",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="list_folders",
            description="List all available folders for organizing notes",
            inputSchema={
                "type": "object",
                "properties": {}
            }
        ),
        Tool(
            name="search_notes",
            description="Search notes using full-text search and return matching titles",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Full-text search query (minimum 3 characters)"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results to return",
                        "default": 5,
                        "minimum": 1
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_note",
            description="Retrieve the full note content by tid",
            inputSchema={
                "type": "object",
                "properties": {
                    "tid": {
                        "type": "integer",
                        "description": "Task tid (primary key)"
                    }
                },
                "required": ["tid"]
            }
        ),
        Tool(
            name="update_note",
            description="Update metadata on an existing note (context, folder, title, star). At least one field must be provided.",
            inputSchema={
                "type": "object",
                "properties": {
                    "tid": {
                        "type": "integer",
                        "description": "Task tid of the note to update"
                    },
                    "context": {
                        "type": "string",
                        "description": "New context name (optional)"
                    },
                    "folder": {
                        "type": "string",
                        "description": "New folder name (optional)"
                    },
                    "title": {
                        "type": "string",
                        "description": "New title (optional)"
                    },
                    "star": {
                        "type": "boolean",
                        "description": "Star/favorite the note (optional)"
                    }
                },
                "required": ["tid"]
            }
        )
    ]


@app.call_tool()
async def call_tool(name: str, arguments: Any) -> list[TextContent]:
    """Handle tool calls."""

    if name == "create_note":
        title = arguments["title"]
        note = arguments["note"]
        context_name = arguments.get("context", "none")
        folder_name = arguments.get("folder", "none")
        star = arguments.get("star", False)

        # Resolve context name to tid and uuid
        context_result = db.get_context_by_name(context_name)
        if context_result is None:
            return [TextContent(
                type="text",
                text=f"Error: Context '{context_name}' not found. Use list_contexts to see available contexts."
            )]
        context_tid, context_uuid = context_result

        # Resolve folder name to tid and uuid
        folder_result = db.get_folder_by_name(folder_name)
        if folder_result is None:
            return [TextContent(
                type="text",
                text=f"Error: Folder '{folder_name}' not found. Use list_folders to see available folders."
            )]
        folder_tid, folder_uuid = folder_result

        # Insert the note
        try:
            task_tid = db.insert_note(
                title=title,
                note=note,
                context_tid=context_tid,
                context_uuid=context_uuid,
                folder_tid=folder_tid,
                folder_uuid=folder_uuid,
                star=star
            )
            return [TextContent(
                type="text",
                text=f"Successfully created note '{title}' with tid {task_tid} in folder '{folder_name}' and context '{context_name}'"
            )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error creating note: {str(e)}"
            )]

    elif name == "list_contexts":
        contexts = db.list_contexts()
        result = "Available contexts:\n"
        for tid, title, uuid, star in contexts:
            star_marker = " *" if star else ""
            result += f"- {title}{star_marker} (tid: {tid}, uuid: {uuid})\n"
        return [TextContent(type="text", text=result)]

    elif name == "list_folders":
        folders = db.list_folders()
        result = "Available folders:\n"
        for tid, title, uuid, star in folders:
            star_marker = " *" if star else ""
            result += f"- {title}{star_marker} (tid: {tid}, uuid: {uuid})\n"
        return [TextContent(type="text", text=result)]

    elif name == "search_notes":
        query = arguments["query"]
        limit = arguments.get("limit", 5)
        try:
            limit_value = int(limit)
        except (TypeError, ValueError):
            return [TextContent(
                type="text",
                text="Error: 'limit' must be an integer."
            )]

        try:
            matches = db.find_notes(query, limit_value)
        except ValueError as exc:
            return [TextContent(type="text", text=f"Error: {exc}")]
        except Exception as exc:
            return [TextContent(
                type="text",
                text=f"Error running search: {exc}"
            )]

        if not matches:
            return [TextContent(
                type="text",
                text=f"No notes matched '{query}'."
            )]

        lines = [f"Matches for '{query}':"]
        for match in matches:
            context_title = match.get("context_title") or "none"
            folder_title = match.get("folder_title") or "none"
            lines.append(
                f"{match['rank']}. {match['title']} "
                f"(context: {context_title}, folder: {folder_title}, "
                f"tid: {match['tid']})"
            )
        return [TextContent(type="text", text="\n".join(lines))]

    elif name == "get_note":
        tid = arguments.get("tid")

        if tid is None:
            return [TextContent(
                type="text",
                text="Error: 'tid' must be provided."
            )]

        try:
            tid = int(tid)
        except (TypeError, ValueError):
            return [TextContent(
                type="text",
                text="Error: 'tid' must be an integer."
            )]

        note_record = db.get_note_by_tid(tid)

        if not note_record:
            return [TextContent(
                type="text",
                text=f"No active note found with tid {tid}."
            )]

        context_title = note_record.get("context_title") or "none"
        folder_title = note_record.get("folder_title") or "none"
        header = (
            f"Title: {note_record['title']}\n"
            f"Context: {context_title}\n"
            f"Folder: {folder_title}\n"
            f"tid: {note_record['tid']}\n"
        )
        body = note_record.get("note", "")
        text = f"{header}\n{body}" if body else header
        return [TextContent(type="text", text=text)]

    elif name == "update_note":
        # Validate tid
        try:
            tid = int(arguments["tid"])
        except (KeyError, TypeError, ValueError):
            return [TextContent(
                type="text",
                text="Error: 'tid' must be provided as an integer."
            )]

        # Get optional update fields
        context_name = arguments.get("context")
        folder_name = arguments.get("folder")
        title = arguments.get("title")
        star = arguments.get("star")

        # Check at least one field is provided
        if context_name is None and folder_name is None and title is None and star is None:
            return [TextContent(
                type="text",
                text="Error: At least one field (context, folder, title, star) must be provided."
            )]

        # Resolve context name to tid and uuid if provided
        context_tid = None
        context_uuid = None
        if context_name is not None:
            context_result = db.get_context_by_name(context_name)
            if context_result is None:
                return [TextContent(
                    type="text",
                    text=f"Error: Context '{context_name}' not found. Use list_contexts to see available contexts."
                )]
            context_tid, context_uuid = context_result

        # Resolve folder name to tid and uuid if provided
        folder_tid = None
        folder_uuid = None
        if folder_name is not None:
            folder_result = db.get_folder_by_name(folder_name)
            if folder_result is None:
                return [TextContent(
                    type="text",
                    text=f"Error: Folder '{folder_name}' not found. Use list_folders to see available folders."
                )]
            folder_tid, folder_uuid = folder_result

        # Perform the update
        try:
            updated = db.update_note_metadata(
                tid=tid,
                context_tid=context_tid,
                context_uuid=context_uuid,
                folder_tid=folder_tid,
                folder_uuid=folder_uuid,
                title=title,
                star=star
            )
            if updated:
                # Build description of what was updated
                changes = []
                if context_name is not None:
                    changes.append(f"context='{context_name}'")
                if folder_name is not None:
                    changes.append(f"folder='{folder_name}'")
                if title is not None:
                    changes.append(f"title='{title}'")
                if star is not None:
                    changes.append(f"star={star}")
                return [TextContent(
                    type="text",
                    text=f"Successfully updated note {tid}: {', '.join(changes)}"
                )]
            else:
                return [TextContent(
                    type="text",
                    text=f"No note found with tid {tid}, or no changes were made."
                )]
        except Exception as e:
            return [TextContent(
                type="text",
                text=f"Error updating note: {str(e)}"
            )]

    else:
        return [TextContent(
            type="text",
            text=f"Unknown tool: {name}"
        )]


async def handle_sse(request):
    """Handle SSE connection for MCP."""
    # Check authentication if api_key is configured
    if api_key:
        auth_error = check_auth(request)
        if auth_error:
            return auth_error

    async with sse_transport.connect_sse(
        request.scope, request.receive, request._send
    ) as streams:
        await app.run(
            streams[0], streams[1], app.create_initialization_options()
        )
    # Return empty response to avoid NoneType error when client disconnects
    return Response()


def create_starlette_app() -> Starlette:
    """Create Starlette app with MCP routes."""
    global sse_transport
    sse_transport = SseServerTransport("/messages/")

    routes = [
        Route("/sse", endpoint=handle_sse),
        Mount("/messages/", app=sse_transport.handle_post_message),
    ]

    return Starlette(routes=routes)


def main():
    """Entry point for the MCP server."""
    global db, api_key

    # Load configuration from project root
    config_path = Path(__file__).parent.parent.parent / "config.json"
    config = load_config(str(config_path))

    pg_config = config["postgres"]
    server_config = config.get("server", {})
    api_key = config.get("api_key")

    # Initialize database
    db = VimangoDatabase(
        host=pg_config["host"],
        port=pg_config["port"],
        user=pg_config["user"],
        password=pg_config["password"],
        dbname=pg_config["db"],
        ssl_mode=pg_config.get("ssl_mode", "disable"),
        ssl_ca_cert=pg_config.get("ssl_ca_cert")
    )
    db.connect()

    try:
        # Create and run the Starlette app
        starlette_app = create_starlette_app()

        host = server_config.get("host", "0.0.0.0")
        port = server_config.get("port", 8080)

        auth_status = "enabled" if api_key else "DISABLED (no api_key in config)"
        print(f"Starting vimango-remote-mcp server on {host}:{port}")
        print(f"Authentication: {auth_status}")
        uvicorn.run(starlette_app, host=host, port=port)
    finally:
        db.close()


if __name__ == "__main__":
    main()
