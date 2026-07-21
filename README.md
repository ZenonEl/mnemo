# mnemo

**Your personal context OS.** Chats, dailies, and knowledge — woven into one searchable memory you can talk to.

> Status: 🌱 **scaffold** — spec-first. The vision and architecture are written down; implementation is staged (see [`docs/ROADMAP.md`](docs/ROADMAP.md)).

---

## What it is

mnemo ingests the scattered context of your work — chat threads, daily logs, documents, past AI conversations — and turns it into a **single, structured, searchable memory** with a clean rule:

- **RAW is sacred.** Every source is kept verbatim, in original format, so there is always something real to point back to.
- **Summaries on top.** Human-readable digests and indexes for fast navigation.
- **One brain, many doors.** A retrieval layer over everything, reachable through a Telegram bot, a CLI, or an MCP server your AI assistant can query.

## Why

Context lives in ten places — Telegram, GitHub issues, `.docx` files, AI chat logs — and none of them talk to each other. mnemo is the layer that remembers, so you (and your assistant) can ask *"what did we decide about X?"* and get a cited answer instead of scrolling.

## Components

| Component | What it does | Status |
|---|---|---|
| **chat-export** | Capture chat threads → RAW + summaries + index | 🧪 prototyped |
| **dailies** | Integrate per-day/per-project work logs | 📋 planned |
| **tg-adapter** | Telegram bot: talk to the assistant, query memory, grep | 📋 planned |
| **chat-sync** | Sync AI chat transcripts, encrypted, via git | 📋 planned |
| **retrieval** | Vector search + skill/MCP over everything | 📋 planned |

Detailed specs per component: [`docs/components/`](docs/components/).

## Docs

- [`docs/VISION.md`](docs/VISION.md) — the full idea, in depth
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — components, data model, data flow
- [`docs/ROADMAP.md`](docs/ROADMAP.md) — phased plan

## Design principles

1. **RAW never lost** — originals stored untouched; derived data is disposable and rebuildable.
2. **Local-first, sync-optional** — works on one machine; git sync is an add-on, encrypted.
3. **Adapters, not lock-in** — TG / CLI / MCP are interchangeable front doors to one core.
4. **Presentable by default** — clean structure, no secrets in the repo, ready to show.

## License

GPL-3.0 — see [`LICENSE`](LICENSE).
