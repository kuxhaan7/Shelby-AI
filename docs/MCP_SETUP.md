# Connecting Shelby to external services via MCP

Shelby can use **hosted (remote) MCP servers** to act on real accounts — send
email, look up leads, manage calendar events, etc. Claude connects to those
servers itself (server-side, via Anthropic's MCP connector), so there is nothing
to install or run inside Shelby's container. You only supply, through
environment variables, **which servers to attach and a token for each**.

No URLs or tokens are stored in the repo. If nothing is configured, the
connector stays inactive and Shelby behaves exactly as before.

## The fastest way: just give Shelby the link

You don't have to touch env vars at all. Paste an MCP URL into the chat — exactly
like adding a connector in Claude:

> **You:** connect https://mcp.notion.com/mcp — here's my token: ntn_xxx
> **Shelby:** ✅ Connected 'notion'. Its tools are now available…

Shelby calls its `connect_mcp` tool, stores the server in a registry under
`SHELBY_DATA_DIR` (so it survives redeploys **if** you've mounted a volume), and
starts using that server's tools on its next reply. Ask it to `list_mcp` to see
what's connected, or "disconnect notion" to remove one.

The env-var methods below are for servers you want **baked into the deployment**
(always present, managed by infra). Runtime-connected servers can't override an
env-defined one of the same name.

You can also drive the same registry over HTTP:

```
GET    /mcp                      # list connected servers (no tokens)
POST   /mcp  {name,url,token?}   # connect one
DELETE /mcp/{name}               # disconnect one
```

## What "remote MCP server" means here

The connector attaches servers that expose a public MCP endpoint over HTTP
(SSE or streamable HTTP) and authenticate with a bearer token. Examples:
Apollo's hosted MCP, and any Gmail / Google Calendar / Notion / Linear MCP
server you host or subscribe to. Local `stdio` MCP servers (launched with
`npx …`) are **not** used — a deployed service should talk to hosted endpoints,
not spawn subprocesses.

## Option 1 — one variable pair per server (simplest)

```
SHELBY_MCP_<NAME>_URL      # required: the server's MCP endpoint
SHELBY_MCP_<NAME>_TOKEN    # optional: bearer token for that server
```

`<NAME>` becomes the server's display name (lower-cased). Example — Apollo:

```
SHELBY_MCP_APOLLO_URL=https://mcp.apollo.io/mcp
SHELBY_MCP_APOLLO_TOKEN=<your apollo token>
```

Add as many as you like: `SHELBY_MCP_GMAIL_URL`, `SHELBY_MCP_CALENDAR_URL`, …

## Option 2 — one JSON variable for everything (full control)

Set `SHELBY_MCP_SERVERS` to a JSON array. Use this when you want to restrict
which tools a server exposes, or keep the token in a separate secret var:

```json
[
  {
    "name": "apollo",
    "url": "https://mcp.apollo.io/mcp",
    "token_env": "APOLLO_MCP_TOKEN",
    "allowed_tools": ["apollo_contacts_search", "apollo_people_match"]
  },
  {
    "name": "gmail",
    "url": "https://your-gmail-mcp.example.com/sse",
    "token_env": "GMAIL_MCP_TOKEN"
  }
]
```

Per entry:

| Field | Required | Meaning |
|-------|----------|---------|
| `name` | yes | Display name Shelby shows the user |
| `url` | yes | Remote MCP endpoint (SSE or streamable HTTP) |
| `token_env` | no | Name of **another** env var holding the bearer token (preferred) |
| `authorization_token` | no | The literal token (works, but prefer `token_env`) |
| `allowed_tools` | no | Restrict to these tool names (omit = allow all) |

`token_env` is preferred so the actual secret lives in its own variable, not
inside this JSON blob.

## Setting the variables on Railway

Railway → your service → **Variables** → add the keys above → redeploy.
(Same place you set `ANTHROPIC_API_KEY`, `TELEGRAM_BOT_TOKEN`, etc.)

## Verify it's connected

Hit `/health` — it lists the attached servers (names only, never tokens):

```json
{ "status": "ok", "rag_docs": 12,
  "mcp_servers": [ { "name": "apollo", "authenticated": true } ] }
```

Once attached, the server's tools appear to Shelby automatically. Just ask —
e.g. "find the CEO of Acme in Apollo" or "what's on my calendar tomorrow" — and
Shelby calls the tool directly.

## A note on OAuth services (Gmail, Google Calendar)

Some providers require OAuth rather than a static bearer token. The connector
sends whatever token you give it as the `Authorization: Bearer` header. If a
provider's MCP endpoint needs OAuth, you must obtain an access token out of band
(through that provider's flow) and put it in the `_TOKEN` / `token_env`
variable. Providers that issue a long-lived API key (like Apollo) work with no
extra steps.

## Security

- Tokens are read only from environment variables and are never logged, never
  written to the repo, and never shown in Shelby's replies or `/health`.
- Use `allowed_tools` to expose only the tools you actually want reachable.
- Shelby is instructed to confirm before anything destructive or externally
  visible (sending an email, deleting an event).
