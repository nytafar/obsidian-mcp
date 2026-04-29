# Obsidian MCP Server

A self-hosted [Model Context Protocol](https://modelcontextprotocol.io)
server that turns an Obsidian vault into shared cognition for you and
your AI agents. Not just a filesystem they can poke at. An indexed,
queryable, self-describing knowledge base that any MCP-compatible
client can pick up and use correctly on the first call.

Stack: Python 3.12, FastAPI, PostgreSQL with pgvector. Pluggable
embeddings (Ollama bge-m3, or OpenAI `text-embedding-3-{small,large}`).

![Dashboard](screenshots/dashboard.png)

## Why this exists

There are two things going on here, and they're more interesting
together than apart.

### 1. A shared memory layer between you and your agents

I think of my Obsidian vault as my exocortex. The "big me" that
includes notes, calendars, scripts, search, and AI assistants is
substantially more capable than the "small me" of the biological brain
alone. It's also where I do most of my thinking, because writing
something down is itself a form of thought.

The problem is that until recently, the vault was passive. I had to go
find things. Agents that wanted to help me had to be briefed from
scratch every session, and they had no way to see what I'd already
written about a topic.

This server fixes that. Now the same vault feeds my own daily writing
and any agent I plug into it. The agent reads what I read, links what
I link, follows the same wikilinks, sees the same frontmatter. When I
write a project note on Sunday, my Monday-morning briefing agent
already knows about it. When the agent leaves notes from a research
session, they show up in my normal Obsidian search.

That's the exocortex idea made concrete: one place that holds
context, and both the human and the agents reading and writing into it
on the same terms.

### 2. Agent memory that you can actually read

The other half is the inverse. If you let an agent run for a while, it
needs memory. Most setups solve this with an opaque vector store, a
SQLite blob, or a managed "memory" service that you can't see into.
That works until you want to know what the agent thinks it knows about
you, or you need to correct something, or you want to understand why
it just made a weird suggestion.

This server gives you a different deal. Agent memory lives as markdown
files in your vault. Folder structure, file names, frontmatter, all
visible. You can open the file in Obsidian and read it. You can edit
it. You can delete it. You can grep it. The agent's "memory" is a
human-auditable artifact that sits in the same place as your own
notes, with the same tools available.

The semantic search and wikilink graph still work over that material,
so retrieval is fast and conceptual. But the substrate is files you
own, not a black box.

## What's in the box

The server exposes 17 MCP tools across five concerns.

### Search and discovery
- `keyword_search(query, folder?, tags?, frontmatter?, limit=20)`,
  full-text via PostgreSQL `tsvector`
- `semantic_search(query, folder?, tags?, frontmatter?, limit=10)`,
  vector similarity via pgvector
- `list_notes(folder?, limit=50)`, sorted by modified time
- `get_recent(folder?, limit=20)`, recently changed
- `get_tags(limit=50)`, tag and count
- `get_vault_guide()`, the Obsidian primer plus this vault's
  `CLAUDE.md`, served live

### Read and write
- `read_note(path)`
- `create_note(path, content)`, atomic write, refuses overwrite
- `edit_note(path, …)` with four mutually exclusive modes: full
  replace (default), `append=True`, `find=…` (with optional
  `replace_all`), or `section=<heading>` (ATX headings, supports
  `Parent/Child` path-style disambiguation). `dry_run=True` returns a
  unified diff without writing.
- `move_note(from_path, to_path, rewrite_links=False)`, relocates and
  optionally rewrites incoming `[[Old]]`, `[[Old|alias]]`,
  `[[Old#anchor]]`, `![[Old]]`, and `[[folder/Old]]` references in
  source notes
- `delete_note(path, permanent=False)`, soft-delete to
  `.trash/<YYYYMMDD-HHMMSS>-<basename>` by default. `permanent=True`
  does a hard `os.unlink`.
- `set_frontmatter(path, updates, remove?)`, structured YAML
  mutation. Body is byte-identical when only frontmatter changes.

### Wikilink graph
- `get_backlinks(path, limit=50)`, notes linking TO `path`
- `get_links(path)`, outgoing links, both resolved and dangling
- `get_neighborhood(path, depth=1, limit=50)`, undirected BFS over the
  resolved-link graph, capped at depth ≤ 5 and limit ≤ 200
- `find_related(path, limit=10)`, semantic neighbors via averaged
  chunk embeddings and pgvector cosine distance, deduped per note
- `find_orphans(folder?, limit=50)`, notes with zero in or out
  resolved links

### Auth and ops
- API keys with the `omcp_` prefix, stored as SHA-256 hashes, with
  `read` and `readwrite` permission scopes. Write tools refuse on
  read-only keys.
- OAuth 2.0 PKCE (S256) flow for clients like Claude Desktop and
  claude.ai.
- Control panel (Jinja2, htmx, Tailwind) for keys, usage logs,
  indexer status, embedding-provider info, and a danger-zone reset.
- Every tool call is logged to `usage_logs` with name, params
  (truncated to 200 chars), duration, and response size.

All write tools route through `src/services/vault.py::write_file`,
which writes to a tmp file in the same directory and `os.replace()`s
it onto the destination. A crash mid-write cannot truncate a note.

## Control panel

The server ships with a built-in admin UI for the parts of operations
that are easier to look at than to query: minting keys, watching the
indexer, eyeballing tool-call traffic, and resetting embeddings when
you switch providers.

### Usage

Per-tool-call audit log with a 14-day request histogram. Every MCP
call is recorded with the calling key, tool name, duration, and
response size — useful for noticing a misbehaving agent burning
tokens on something it shouldn't.

![Usage](screenshots/usage.png)

### API keys and OAuth clients

Bearer keys with `read` / `readwrite` scopes for API clients, and a
separate OAuth 2.0 PKCE flow for clients like Claude Desktop and
claude.ai that expect a proper authorization-code dance.

![API keys](screenshots/api-keys.png)
![OAuth clients](screenshots/oauth-clients.png)

### Vault browser

A read-only file tree of the mounted vault, mostly for sanity-checking
that the container sees what you think it sees.

![Vault](screenshots/vault.png)

### Settings

Indexer status, current embedding provider and model, vault path, and
the danger-zone reset that drops and recreates the embeddings column
at the configured dimension. Use this when switching providers.

![Settings](screenshots/settings.png)

## Quick start

> Deploying on a VPS from scratch? See [`DEPLOYMENT.md`](./DEPLOYMENT.md)
> for the full walkthrough: Postgres setup, Caddy and TLS, vault sync
> via Nextcloud, and the gotchas that bite first-time deploys.

### Prerequisites

- Docker and Docker Compose
- A PostgreSQL 16 instance reachable from the container, with the
  `pgvector` extension installed
- Either an Ollama instance running `bge-m3`, or an OpenAI API key.
  Anything that speaks the OpenAI embeddings protocol works (Azure
  OpenAI, OpenRouter, Together, etc.).

### 1. Clone, configure, point at your vault

```bash
git clone https://github.com/maxkuminov/obsidian-mcp.git
cd obsidian-mcp
cp .env.example .env
$EDITOR .env
```

In `docker-compose.yml`, point the `/obsidian` volume at your vault:

```yaml
volumes:
  - /path/to/your/vault:/obsidian
```

### 2. Pick an embedding backend

Option A, OpenAI (zero local infra):

```env
EMBEDDING_PROVIDER=openai
OPENAI_API_KEY=sk-...
EMBEDDING_DIMENSIONS=1024
OPENAI_EMBEDDING_MODEL=text-embedding-3-small
```

The server validates `OPENAI_API_KEY` at startup and refuses to boot
if it's missing.

Option B, Ollama (self-hosted, GPU recommended):

```env
EMBEDDING_PROVIDER=ollama
OLLAMA_URL=http://your-ollama-host:11434
EMBEDDING_MODEL=bge-m3
EMBEDDING_DIMENSIONS=1024
```

This is the default. Omitting `EMBEDDING_PROVIDER` falls back to
Ollama.

### 3. Deploy

```bash
make init       # data dirs and .env from template (skip if you've already edited)
make db-init    # create database, user, and pgvector extension
make deploy     # build, push to local registry, run migrations, recreate container
```

The first deploy backfills the index, the wikilink graph, and the
embeddings. For a 2 to 3k-note vault on Ollama with a GPU this takes
a few minutes. On `text-embedding-3-small` it's seconds.

### 4. Connect a client

Mint an API key in the control panel, then point your MCP client at:

```
URL:  https://obsidian-mcp.<your-domain>/mcp
Auth: Bearer omcp_...
```

For Claude Desktop, add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "obsidian": {
      "url": "https://obsidian-mcp.<your-domain>/mcp",
      "headers": { "Authorization": "Bearer omcp_..." }
    }
  }
}
```

For Claude Code:

```bash
claude mcp add obsidian --transport http \
  --url "https://obsidian-mcp.<your-domain>/mcp" \
  --header "Authorization: Bearer omcp_..."
```

The first thing any agent should do in a new session is call
`get_vault_guide()`. That's how it learns your folder structure,
naming conventions, and YAML schema before it writes anything.

## The self-describing vault

This is the part most "MCP for Obsidian" projects miss. They stop at
read, write, and list. The interesting question isn't "can the agent
reach the files," it's "does the agent know the rules?"

If you have an opinionated vault — task placement logic, folder
conventions, required frontmatter, tag taxonomy — an agent with write
access can do real damage without that context. Tasks land in the
wrong folder. Bare-date filenames collide with templates. Wrong tags
break Dataview queries. The data layer works fine; the context layer
is where the failures show up.

The fix is small. Keep a machine-readable instruction file
(`CLAUDE.md` at the vault root) that describes the system's own rules.
Expose it as a dedicated tool. Every connecting agent calls it once at
the start of a session and immediately knows how the vault works.
Update the file, every agent sees the change on the next call. No
client-side config. No system-prompt injection. The vault is
authoritative about its own rules.

`get_vault_guide()` does exactly this. It returns a generic Obsidian
primer (wikilink syntax, embed syntax, tag conventions, common plugin
literals) plus the vault's `CLAUDE.md` live. The hint to call it first
is baked into the write-tool descriptions so the agent gets pulled
into the right behavior even without prompting.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `DATABASE_URL` | — | `postgresql+asyncpg://user:pass@host/db` |
| `VAULT_PATH` | `/obsidian` | In-container vault mount |
| `SECRET_KEY` | — | itsdangerous signer key |
| `INDEX_INTERVAL_SECONDS` | `300` | Periodic reindex cadence |
| `EMBEDDING_PROVIDER` | `ollama` | `ollama` or `openai` |
| `EMBEDDING_DIMENSIONS` | `1024` | pgvector column width |
| `OLLAMA_URL` | — | Used when provider is Ollama |
| `EMBEDDING_MODEL` | `bge-m3` | Ollama model name |
| `OPENAI_API_KEY` | — | Required when provider is OpenAI |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | Override for Azure or proxies |
| `OPENAI_EMBEDDING_MODEL` | `text-embedding-3-small` | OpenAI model |
| `CHUNK_SIZE` | `1500` | Approx chars per embedding chunk |
| `CHUNK_OVERLAP` | `50` | Char overlap between chunks |

See `.env.example` for the full set with comments.

### Cost expectations (OpenAI)

Rough first-index spend, assuming an average note around 800 tokens
(three chunks of about 500 tokens). Pricing is OpenAI's published
rate at time of writing.

| Model | $/1M tokens | 1k notes | 10k notes | 100k notes |
| --- | --- | --- | --- | --- |
| `text-embedding-3-small` | $0.02 | ~$0.05 | ~$0.50 | ~$5.00 |
| `text-embedding-3-large` | $0.13 | ~$0.30 | ~$3.00 | ~$30.00 |

After the first index, only changed notes are re-embedded. Ongoing
cost is proportional to edits.

### Switching providers

Different models produce non-comparable vectors, so a provider switch
requires reindexing.

1. `make down`
2. Update `.env`. Change `EMBEDDING_PROVIDER`, set credentials,
   optionally change `EMBEDDING_DIMENSIONS`.
3. `make reset-embeddings`
4. `make up`. The next indexer pass re-embeds the vault.

You can also use Settings → Danger zone → Reset embeddings in the
control panel, which performs the same SQL while the server is running
(pauses the indexer, runs the SQL, resumes).

If you change `EMBEDDING_DIMENSIONS` without running the reset, the
server detects the mismatch at startup and exits non-zero with a
pointer to the reset target.

## Architecture

```
┌──────────────┐                       ┌──────────────────────┐
│ MCP clients  │   HTTP + Bearer key   │   FastAPI app        │
│  Claude Desk │ ────────────────────▶ │  ┌────────────────┐  │
│  Claude Code │                       │  │  MCP server    │  │
│  n8n agents  │                       │  │  (17 tools)    │  │
│  OpenWebUI   │                       │  └─────┬──────────┘  │
└──────────────┘                       │        ▼             │
                                       │  ┌────────────────┐  │
                                       │  │  Services:     │  │
                                       │  │  - vault       │  │
                                       │  │  - search      │  │
                                       │  │  - embeddings  │  │
                                       │  │  - links       │  │
                                       │  │  - indexer     │  │
                                       │  └─────┬──────────┘  │
                                       │        ▼             │
                                       │  ┌────────────────┐  │
                                       │  │ Postgres +     │  │
                                       │  │ pgvector       │  │
                                       │  └────────────────┘  │
                                       └──────────┬───────────┘
                                                  ▼
                                       ┌────────────────────┐
                                       │  Embedding         │
                                       │  provider          │
                                       │  (Ollama / OpenAI) │
                                       └────────────────────┘
```

### Indexing pipeline

```
.md files in vault
    ↓ skip dot-dirs
parse frontmatter, extract tags (YAML + inline #hashtags)
    ↓ SHA-256 hash
skip if unchanged
    ↓
UPSERT notes_metadata (path, title, tags[], frontmatter JSONB,
                       content_hash, tsvector, modified_at)
    ↓
extract wikilinks/embeds/markdown-links → resolve targets →
note_links (source_id, target_id or NULL for dangling)
    ↓
chunk content (≈500 tokens, 50 token overlap) → embed via provider →
note_embeddings (note_id, chunk_index, chunk_text, embedding[N])
    ↓
set embedded_content_hash = content_hash
```

The indexer runs on startup and every `INDEX_INTERVAL_SECONDS` (5
minutes by default). Hashes are content-only, so the change detector
ignores mtime jitter. Stale embeddings are caught by the
`embedded_content_hash != content_hash` mismatch.

### Database schema

| Table | Purpose |
| --- | --- |
| `notes_metadata` | Path, title, tags, frontmatter, content hash, embedded hash, tsvector, modified time |
| `note_embeddings` | One row per chunk. `embedding` is `vector(EMBEDDING_DIMENSIONS)`. |
| `note_links` | Wikilink graph: source/target IDs, target_path, kind (`link`, `embed`, `markdown`) |
| `api_keys` | Hashed bearer tokens, prefix for display, permission, expiry |
| `usage_logs` | Per-tool-call audit |
| `oauth_clients`, `oauth_codes`, `oauth_tokens` | OAuth 2.0 PKCE state |

GIN indexes on `content_tsvector` and `tags[]`. B-tree indexes on the
hot foreign keys. pgvector exact NN by default, which works fine up to
~100k chunks. Switch to IVFFlat or HNSW above that.

## Project layout

```
src/
  main.py             FastAPI app, lifespan, MCP mount
  config.py           pydantic-settings
  database.py         async SQLAlchemy engine/session
  models/db.py        ORM models
  mcp_server/         MCP server, tools, auth middleware
  services/           vault ops, search, embeddings, links, indexer
  api/                control-panel REST endpoints
  control_panel/      Jinja2 templates and static assets
  oauth/              OAuth 2.0 authorization-code flow
alembic/              database migrations
scripts/              one-off ops scripts (e.g. reset_embeddings.py)
tests/                pytest suite + smoke-test docs
openspec/             change proposals (spec-driven workflow)
```

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

The unit-test suite covers the embedding-provider abstraction, OpenAI
batching and retry behavior, config validation, and the
dimension-mismatch startup check. Network-bound tests use `respx` to
mock httpx, so no real network access is required.

To run the server outside Docker:

```bash
DATABASE_URL=... SECRET_KEY=... VAULT_PATH=... uvicorn src.main:app --reload
```

## Make targets

```
make init             First-time setup (data dirs, .env)
make build            Build Docker image (no cache)
make deploy           Build, push, backup, recreate container
make db-init          Create database, user, and pgvector extension
make db-migrate       Run alembic migrations
make db-backup        Dump database to backups dir
make logs             Tail container logs
make reindex          Trigger a reindex via the API
make reset-embeddings Drop and recreate embedding column at configured dim
make status           Show container and health status
```

## Security notes

- API keys use the `omcp_` prefix and are stored as SHA-256 hashes.
  The raw key is shown exactly once at creation.
- The control panel is intended to sit behind an external auth
  gateway. The included `docker-compose.yml` uses Traefik with an
  OAuth chain. Don't expose `/admin` directly to the internet.
- The OpenAI key is rendered on the settings page as
  `key[:8] + "..." + key[-4:]` and never appears in full in HTML or
  JS sources.
- Path traversal is blocked at the service layer. All write paths
  resolve through `Path.resolve().relative_to(vault_root)`.
- Parameterized queries everywhere. No string interpolation into SQL.
- Response headers include HSTS, `X-Content-Type-Options: nosniff`,
  and `X-Frame-Options: DENY`.

## Status

Single-author, in active use as the maintainer's personal exocortex
(2,500+ notes, multiple connecting agents). Public for anyone who
wants to fork it. Issues and PRs welcome but expect opinionated review.
This is a working system, not a generic platform.

## License

MIT. See `LICENSE`.
