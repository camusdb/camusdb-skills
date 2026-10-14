# CamusDB MCP server reference

Sources: https://camusdb.github.io/docs/mcp-server and the `camusdb-mcp` repository README
(https://github.com/camusdb/camusdb-mcp).

The MCP server exposes a CamusDB node or cluster to an AI agent through typed MCP tools. It
speaks MCP over stdio. The MCP client starts the process; you do not run it by hand. It talks
to CamusDB through the `CamusDB.Client` package. With no `Protocol=` key, that client uses REST.

## Prerequisites

- A running CamusDB server that the client machine can reach.
- The .NET 10 SDK.
- A clone of `https://github.com/camusdb/camusdb-mcp`.

## Build

```bash
git clone https://github.com/camusdb/camusdb-mcp
cd camusdb-mcp
dotnet build camusdb-mcp.sln
```

Binary: `CamusDB.Mcp/bin/Debug/net10.0/CamusDB.Mcp`. The repository README also shows
`dotnet run --project /path/to/camusdb-mcp/CamusDB.Mcp` as the command, without a prebuild.

## Environment variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `CAMUS_MCP_CONNECTION_STRING` | none | Full connection string, e.g. `Endpoint=http://localhost:5095;Database=mydb;Timeout=30`. Wins over `CAMUS_MCP_ENDPOINT`. |
| `CAMUS_MCP_ENDPOINT` | `http://localhost:7141` | Endpoint, or a comma-separated list used in turn. |
| `CAMUS_MCP_DEFAULT_DATABASE` | none | Database for a tool call without a `database` argument. Also read from `Database=`. |
| `CAMUS_MCP_TIMEOUT_SECONDS` | client default, `10` | Command timeout when built from `CAMUS_MCP_ENDPOINT`. |
| `CAMUS_MCP_MAX_ROWS` | `1000` | Hard cap on `select_query` rows. |
| `CAMUS_MCP_USER` | none | CamusDB user. |
| `CAMUS_MCP_PASSWORD` | none | Its password. Exchanged once for a bearer token. |
| `CAMUS_MCP_ACCESS_TOKEN` | none | A bearer token from elsewhere. Used verbatim; never renewed. |
| `CAMUS_MCP_TOKEN_LIFETIME_SECONDS` | client default, `600` | Fallback token lifetime when the server reports none. |

Endpoint warning: the default `http://localhost:7141` is the HTTPS port number. A default node
serves plain HTTP on `5095` and has no HTTPS listener without `https_certificate`. For a local
node, set `CAMUS_MCP_ENDPOINT=http://localhost:5095`.

Credential precedence: a dedicated `CAMUS_MCP_*` credential variable wins over `User=`,
`Password=`, `AccessToken=`, `TokenLifetime=` and their aliases `Uid`, `UserId`, `Username`,
`Pwd` in the connection string. An explicit tool `database` argument wins over
`CAMUS_MCP_DEFAULT_DATABASE`.

## Client configuration

All clients need the same two things: the `command` (the built binary) and the `env` block.

Generic `mcpServers` JSON. Claude Code reads it from `.mcp.json` in the project root
(shown in the camusdb-mcp README). Claude Desktop uses the same shape in
`claude_desktop_config.json`.

```json
{
  "mcpServers": {
    "camusdb": {
      "command": "/path/to/camusdb-mcp/CamusDB.Mcp/bin/Debug/net10.0/CamusDB.Mcp",
      "env": {
        "CAMUS_MCP_ENDPOINT": "http://localhost:5095",
        "CAMUS_MCP_DEFAULT_DATABASE": "app_agent"
      }
    }
  }
}
```

Without a prebuild (from the camusdb-mcp README):

```json
{
  "mcpServers": {
    "camusdb": {
      "command": "dotnet",
      "args": ["run", "--project", "/path/to/camusdb-mcp/CamusDB.Mcp"],
      "env": {
        "CAMUS_MCP_ENDPOINT": "http://localhost:5095",
        "CAMUS_MCP_DEFAULT_DATABASE": "app_agent"
      }
    }
  }
}
```

The CamusDB docs do not show Codex or Cursor files. These are those clients' own formats.
Check the client's MCP documentation if a field is rejected.

- Cursor: the same `mcpServers` JSON, in `.cursor/mcp.json` (project) or `~/.cursor/mcp.json`.
- Codex: a TOML table in `~/.codex/config.toml`:

```toml
[mcp_servers.camusdb]
command = "/path/to/camusdb-mcp/CamusDB.Mcp/bin/Debug/net10.0/CamusDB.Mcp"

[mcp_servers.camusdb.env]
CAMUS_MCP_ENDPOINT = "http://localhost:5095"
CAMUS_MCP_DEFAULT_DATABASE = "app_agent"
```

## Authenticated server

```json
{
  "mcpServers": {
    "camusdb": {
      "command": "/path/to/camusdb-mcp/CamusDB.Mcp/bin/Debug/net10.0/CamusDB.Mcp",
      "env": {
        "CAMUS_MCP_ENDPOINT": "https://camus.internal:5095",
        "CAMUS_MCP_DEFAULT_DATABASE": "analytics",
        "CAMUS_MCP_USER": "mcp",
        "CAMUS_MCP_PASSWORD": "app-secret"
      }
    }
  }
}
```

Create the user from a superuser session. User and grant management is not an MCP tool.

```sql
CREATE USER mcp IDENTIFIED BY 'app-secret';
GRANT SELECT ON analytics.* TO mcp;
```

- Over `http://localhost`, loopback is exempt from the TLS rule. Elsewhere, use `https://`.
- Prefer user and password for a long session; the client renews the token.
- An access token fails with `CADB0516` after it expires or is revoked.
- Treat the client config file as a secret when it holds a password or token.

| Code | Meaning |
| --- | --- |
| `CADB0516` | Credentials absent, wrong, expired, or revoked. Fix the env and restart the client. |
| `CADB0517` | Missing privilege. Grant access to the database or table. |
| `CADB0518` | Too many login attempts. Wait. |
| `CADB0519` | Credentials over plaintext. Use `https://`. |

## Tools

The docs list ten tools. The camusdb-mcp source and README list fifteen. Newer builds have all
of these:

| Tool | Kind | Notes |
| --- | --- | --- |
| `list_databases` | read | |
| `list_tables` | read | |
| `list_views` | read | source/README only |
| `list_sequences` | read | source/README only |
| `list_branches` | read | `SHOW BRANCHES FROM` |
| `list_ancestors` | read | source/README only |
| `list_indexes` | read | Omits indexes still backfilling |
| `get_table_schema` | read | `SHOW COLUMNS FROM` |
| `show_create` | read | source/README only |
| `get_table_statistics` | read | source/README only |
| `select_query` | query | First keyword must be `SELECT` or `SHOW` (README adds `DESCRIBE`). Supports `@name` parameters. |
| `explain_query` | query | `EXPLAIN`; prefixes a bare `SELECT`. `mode`: `plan`, `logical`, `physical`. |
| `create_database` | write | Supports `IF NOT EXISTS` |
| `create_table` | write | Typed column list |
| `insert_rows` | write | Parameterized; batches of 500 rows |

The guard strips SQL comments before it checks the first keyword. Mutating SQL sent to
`select_query` fails before execution.

## Rules for an agent workflow

1. Point the server at a branch the agent may change: `CREATE DATABASE app_agent BRANCH FROM app;`.
2. Set `CAMUS_MCP_DEFAULT_DATABASE` to that branch.
3. Give the MCP user the narrowest grants. Server grants are the durable boundary.
4. With auth off, anyone who can start the binary has full access to the endpoint.
5. Drop the branch when the session ends. Confirm with the user first.
