# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Vimango Remote MCP Server - a remote Model Context Protocol (MCP) server that provides Claude iOS and other remote MCP clients access to a vimango notes system backed by PostgreSQL.

## Commands

```bash
# Install dependencies
uv sync

# Run the server
uv run vimango-remote-mcp

# Run tests
pytest
```

## Running as a Systemd User Service

On a remote server, the MCP server runs as a systemd user service (not root) at `~/.config/systemd/user/vimango-remote-mcp.service`.

**Service management:**
```bash
systemctl --user start vimango-remote-mcp
systemctl --user stop vimango-remote-mcp
systemctl --user restart vimango-remote-mcp
systemctl --user status vimango-remote-mcp
journalctl --user -u vimango-remote-mcp -f  # follow logs
```

**Note:** Requires `sudo loginctl enable-linger $USER` for the service to start at boot without login.

## Architecture

```
Client (Claude iOS/MCP) ──HTTP/SSE──> Starlette Server ──> VimangoDatabase ──> PostgreSQL
                                      (server.py)          (db.py)
```

**Endpoints:**
- `/sse` - SSE streaming connection for MCP protocol
- `/messages/` - POST endpoint for client messages

**Authentication:**
- Bearer token in Authorization header (preferred)
- Query parameter fallback: `?api_key=...`
- Route-level auth (not middleware, due to SSE streaming limitations)

## Key Files

- `src/vimango_remote_mcp/server.py` - MCP server with 6 tools (create_note, list_contexts, list_folders, search_notes, get_note, update_note)
- `src/vimango_remote_mcp/db.py` - PostgreSQL database abstraction with full-text search

## Database Schema Notes

- Notes stored in `task` table with `tsvector` column for full-text search
- Context/folder references use both `tid` (integer) and `uuid` (string)
- Default "none" context UUID: `00000000-0000-0000-0000-000000000001`
- Default "none" folder UUID: `00000000-0000-0000-0000-000000000002`
- Queries filter on `deleted = false` and `archived = false`

## Configuration

Copy `config.json.example` to `config.json` with PostgreSQL credentials and server settings. The config.json file is gitignored.
