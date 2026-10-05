# mnemo

[![Version](https://img.shields.io/badge/version-0.16.0-blue.svg)](CHANGELOG.md)
[![License](https://img.shields.io/badge/code-AGPL--3.0--or--later-blue.svg)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-CC%20BY--SA%204.0-lightgrey.svg)](SPEC/LICENSE)

**English** · [Русский](README.ru.md)

**One chat-export standard — for Claude Code and Codex.**

Client conversations, briefs in `.docx`, screenshots, call transcripts go into an
archive where the origin of every piece is known, and which a linter checks
instead of your eyes.

```
/mnemo:init          create an export, or adopt an existing one
/mnemo:import        a chat dump, a capture buffer or a paste → filed whole
/mnemo:add-text      a message or transcript → RAW
/mnemo:add-files     documents → RAW, text and embedded images extracted
/mnemo:add-screens   screenshots → RAW, byte for byte
/mnemo:verify        check the archive is intact and matches the standard
```

These are **names of operations**. In Claude Code they are also session
commands; in Codex the same work is done by the `mnemo:chat-export` skill
calling the scripts directly. The archive comes out identical, reads without the
tool, and is checked by one linter.

The second half is about **the state of the work**, not the material:

```
/mnemo:req           a client requirement: verbatim, with proof
/mnemo:ask           an open question: what it blocks, who it is asked of
/mnemo:audit         is everything done the way the client wanted
/mnemo:gaps          what is missing and what is lost for good
```

The `mnemo:work-state` skill owns this half. It is raised by what a person says
— "did we do everything in the brief", "I'm stuck", "we need to ask" — in both
hosts, identically.

The third part **reconciles a new batch against the project**:

```
audiences             who is owed clear synchronisation, and from which review
review                what the new batch changed in the standing state
feedback / deliver    the exact brief, and the mark of actual delivery
decide / fact         decisions and claims with their origin
```

The `mnemo:reconcile` skill is raised when the next stream of messages arrives
and someone has to work out how it changes earlier agreements. For a manager
with no project context it prepares either a short confirmation or one
substantive clarification; by default it shows no internal references or
technical detail. Herald, Ephemeris and Kanon attach softly and are not required
for the reconciliation itself.

---

## Why

Work context gets sorted out by hand, and slightly differently every time. A
month later nobody can tell which part of the archive is the client's own words
and which is somebody's summary — while decisions get made on both alike.

mnemo fixes that as a format:

- **RAW is verbatim and immutable.** Every file is recorded with its `sha256`, so
  a substitution shows.
- **Who is who.** A people registry ties a messenger display name, a git login
  and a spoken name together; the `self` role marks whoever keeps the archive.
- **Attribution is separate from the text.** A Telegram paste signs a forwarded
  message with whoever forwarded it. On a real chat that affected 16 messages out
  of 21 — the client's requirements would have been attributed to their manager.
  The `attribution` field makes such a swap visible and forbids quoting under a
  false author.
- **Re-importing is safe.** Dump the chat again and only the new part is taken in;
  nothing is duplicated.
- **Every piece of material carries a fidelity level.** `verbatim`,
  `reconstructed`, `digest`, `placeholder`. A summary cannot be quoted as
  somebody's words — the standard forbids it, rather than leaving it to care.
- **Gaps are visible.** When the original cannot be obtained, a record is filed
  instead of silence. "Not obtained" and "lost" are different things: the first is
  a task, the second a fact.
- **`INDEX.md` is generated.** A hand-written index drifts from the content
  silently; here it is derived from the manifest and cannot drift.
- **Redactions are part of the format.** What was removed, why, whether it is
  reversible, where the original lives. Without that the archive cannot be shown.
- **Data does not leak into git.** The export directory is excluded from the host
  project's repository before the first file with data appears in it.

## Install

### Claude Code

```bash
claude plugin marketplace add ZenonEl/mnemo
claude plugin install mnemo@mnemo
```

The same from a session: `/plugin marketplace add ZenonEl/mnemo`, then
`/plugin install mnemo@mnemo`. To update — `claude plugin update mnemo@mnemo`
and restart.

### Codex

```bash
codex plugin marketplace add ZenonEl/mnemo
codex plugin add mnemo@mnemo
```

To update — `codex plugin marketplace upgrade`, then `codex plugin add` again.

Do not copy the repository into `~/.codex/skills/`: a copy is a second instance
of the standard, drifting from the original in silence.

**In both hosts the skills are named the same** — `mnemo:chat-export`,
`mnemo:work-state`, `mnemo:reconcile` — and each is read from one shared file.

No dependencies: the scripts run on the bare Python 3 standard library.

## What it looks like

```
<export>/
├── INDEX.md                      derived: chronology, participants, loose ends
├── MANIFEST.json                 source of truth about the contents
├── summaries/
│   ├── <date>_chat-summary.md    what was agreed
│   ├── attachments-summary.md    what the documents settle, what to check against
│   ├── conventions.md            working rules + who set them and when
│   ├── findings-log.md           verified facts and where they were delivered
│   ├── redactions.md             derived: what was removed and why
│   └── project-state.md          derived: the standing understanding of the project
└── raw/
    ├── messages/     YYYY-MM-DD_<author>[_<label>].md
    ├── attachments/  originals + _extracted-text/
    ├── screenshots/  originals + from-docx/<doc>/
    └── voice/
```

## Commands

| Command | What it does |
|---|---|
| `/mnemo:init` | create an export, or **adopt an existing one** without losing content |
| `/mnemo:import` | a Telegram dump, a herald capture buffer or a paste: authorship, attachments, deduplication |
| `/mnemo:add-text` | a message, a note, a transcript |
| `/mnemo:add-files` | documents; text and embedded images are pulled out of `.docx`/`.xlsx` |
| `/mnemo:add-screens` | screenshots, no re-compression |
| `/mnemo:note` | a verified fact into `findings-log` |
| `/mnemo:rule` | a working rule into `conventions` |
| `/mnemo:redact` | register a redaction |
| `/mnemo:remove` | retire a record instead of editing the manifest by hand |
| `/mnemo:sync` | rebuild the derived files from the manifest |
| `/mnemo:req` | a client requirement: verbatim, with proof |
| `/mnemo:ask` | an open question: what it blocks, who it is asked of |
| `/mnemo:review` | reconcile a new batch against the standing understanding |
| `/mnemo:feedback` | record a confirmation or a clarification for an audience |
| `/mnemo:deliver` | mark the actual delivery of a stored feedback |
| `/mnemo:decide` | a decision with its reason and origin |
| `/mnemo:fact` | a claim, or a verified fact |
| `/mnemo:audiences` | who is owed synchronisation, and from which review |
| `/mnemo:audit` | **is everything done the way the client wanted** — with proof |
| `/mnemo:upgrade` | safely bring recognised legacy cases up to date, by dry-run and hash |
| `/mnemo:verify` | the linter: 27 rules of the standard |
| `/mnemo:gaps` | what is missing and what is lost |
| `/mnemo:people` | the people registry: tie one person's names from different sources |
| `/mnemo:publish` | a public slice with no work data in it |

Under Codex there are no commands by these names — the skill performs the same
operation with a script from `scripts/`. Read the list above as a set of
capabilities, not as one host's syntax. `mnemo:reconcile` is allowed for
implicit invocation: both Codex and Claude Code may pick it themselves on a new
batch or a forwarded stream of decisions, without an explicit
`$mnemo:reconcile`.

## Skills

Commands are typed by a person. A skill the model raises itself — from what the
conversation is about. There are three, and they divide the work by intent:

| Skill | When it is raised |
|---|---|
| `mnemo:chat-export` | taking material in: save a conversation, break down attachments, adopt an existing export |
| `mnemo:work-state` | the state of the work: what is wanted of us, is it all done, what blocks, what to ask |
| `mnemo:reconcile` | a new batch: what changed, and how to synchronise a manager briefly |

They are not split for elegance. While there was a single description, all of its
triggers were about taking material in — the checking half was never raised at
all, and under Codex, where there are no slash commands, there was nothing to
call it with.

## The standard

- [`SPEC/STANDARD.md`](SPEC/STANDARD.md) — layout, the `item` contract, the rules; **the version is declared there**
- [`SPEC/PROVENANCE.md`](SPEC/PROVENANCE.md) — the fidelity model and the rules of quoting
- [`SPEC/CITATION.md`](SPEC/CITATION.md) — the `ctx:<slug>#<id>` reference format
- [`SPEC/QUERY.md`](SPEC/QUERY.md) — the read contract: how outside tools take the data
- [`SPEC/CHANGELOG.md`](SPEC/CHANGELOG.md) — versions

The standard is the source of truth. Skills, commands and scripts are its
consumers; a divergence between them and the text of the standard counts as a
defect of the tool.

## Tried on

Three real exports, made by hand before the standard existed, were adopted
without losing content, and the linter passes on all of them: a working
conversation with a project handover (documents with embedded screenshots), a
task board out of a group chat, and a reconstruction of a deleted conversation
from session logs, with four fidelity levels inside one file.

Adoption found two gaps in the standard that design had missed — both closed and
described in [`SPEC/CHANGELOG.md`](SPEC/CHANGELOG.md).

## The set

mnemo is one of three tools around work context. **Three published formats** are
shared: the `ctx:<slug>#<id>` reference, the manifest itself, and the
[read contract](SPEC/QUERY.md) — all described under [`SPEC/`](SPEC/CITATION.md)
and versioned. Nothing executable is shared: no library, no process, no
database. A consumer calls a command and gets JSON, the same way it would call
`gh`; the dependency is on a published output format, and mnemo knows nothing
about the consumer.

| Project | Role |
|---|---|
| **mnemo** | the archive of material with provenance; facts, decisions, questions |
| `ephemeris` | dailies: the state of the day, synced into GitHub issues |
| `herald` | the channel out, and capture of work chats into a buffer the import reads |

## Next

Search over the archive, an MCP server, voice transcription, a Telegram Desktop
dump parser, automatic depersonalisation. All of it sits on top of the standard
and requires no rewrite of it.

## How this project is worked on

[`CONTRIBUTING.md`](CONTRIBUTING.md) — including the main rule: **examples are
depersonalised, always.** Real names of projects, people and organisations do
not enter the repository in any form.

## Licences

The repository is licensed in parts:

| Path | Licence |
|---|---|
| `SPEC/` — the text of the standard | [CC BY-SA 4.0](SPEC/LICENSE) |
| everything else — skills, commands, scripts | [AGPL-3.0-or-later](LICENSE) |

The standard is text, and the copyleft is needed on its derivative editions. The
code is under AGPL because the sensible way this develops — an MCP server over
the archive — would otherwise let someone raise it as a closed service.
