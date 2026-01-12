# Vimango Remote MCP Server

Remote MCP server for vimango using PostgreSQL backend. Enables Claude iOS (and other remote MCP clients) to interact with vimango notes.

## Setup

1. Copy `config.json.example` to `config.json` and configure PostgreSQL connection
2. Install: `uv sync`
3. Run: `uv run vimango-remote-mcp`

## Configuration

```json
{
  "postgres": {
    "host": "localhost",
    "port": "5432",
    "user": "slzatz",
    "password": "your_password",
    "db": "vimango",
    "ssl_mode": "disable"
  },
  "server": {
    "host": "0.0.0.0",
    "port": 8080
  }
}
```

## MCP Tools

- `create_note` - Create a new note with title and markdown body
- `list_contexts` - List available contexts
- `list_folders` - List available folders
- `search_notes` - Full-text search using PostgreSQL
- `get_note` - Retrieve full note content by tid
- `update_note` - Update note metadata (context, folder, title, star)
