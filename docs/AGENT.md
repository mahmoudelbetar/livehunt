# Agent integration (AGENTS.md / hunt.md)

This file is the contract between an AI hunting agent and `livehunt`. Paste the snippets at the
bottom into your agent config, or install the packaged skill (`skills/livehunt/SKILL.md`, copied
to `~/.agents/skills/` by `install.sh`) and just say **livehunt**. The rules exist to stop the two failure modes that waste a
session: **re-reading captures** and **re-testing hypotheses**.

Roles and the handoff: [`WHO-DOES-WHAT.md`](WHO-DOES-WHAT.md).

## Tool contract — what the agent calls, and when

| Moment | Agent action | Why |
|---|---|---|
| user says *"start/continue `<target>`"* | `livehunt up <target>` then `livehunt agent <target>` | one command prepares capture, one block gives the state |
| every turn after that | `livehunt agent <target> --json` | replaces reading DB/docs entirely |
| before any question about captured traffic | `livehunt brief <target>` or one `livehunt query <target> <view>` | answers come from aggregates, never from bodies |
| after the user surfed a flow | `livehunt drain <target>` | classifies only `id > cursor` |
| choosing what to test | `livehunt brief <target>` → top `TODO` row | already ranked, already carries its next test |
| after a test | `livehunt test <target> <H> <result> [--flow N] [--note …]` | the ledger refuses duplicates |
| a fact with no hypothesis row (score < 3, out-of-band, confirmed finding) | `livehunt note <target> "…" [--flow N]` | shows as `notes N` in `brief`; never write a new md file for it |
| session end | `livehunt drain`, `livehunt stop`, update `stash.md` → `STATE.md` → `<target>.md` | resumable next time |
| something looks broken | `livehunt doctor <target>`, then `docs/TROUBLESHOOTING.md` | never guess at infrastructure |

## Non-negotiable agent rules

1. **Never open a capture artefact on start/continue.** `live.db`, `flows.jsonl`, `graph.db`,
   `context.json`, `model.json` are not session-start reading. The brief is.
2. **Cheapest source first.** brief → one doc section → one SQL/query view → sliced JSON.
   Never load a whole JSON model.
3. **One hypothesis, one test.** Restate *principal + object + endpoint* before any request.
   If two of the three are unknown, it is not a test yet.
4. **Record every test.** `dead` and `blocked` leave the TODO list. A `FALSE-POSITIVE` is never
   retried without a new reason. A `BLOCKED` one names the missing principal/object.
5. **Approval gate.** The agent may propose a request; the user approves anything that writes,
   deletes, moves money, or changes recovery.
6. **Read-only by default.** `livehunt` itself never sends requests. The agent must not either.
7. **State gaps as gaps.** "Not observed" is not "secure". Cite the flow id or the doc section.
8. **One next action per reply.** Not a menu.

## Failure handling (agent)

| Doctor result | Agent says |
|---|---|
| `chrome cdp` FAIL | "`livehunt up` opens Chrome automatically — run `livehunt chrome --launch` if the port is still closed." |
| `capture process` stopped | "Capture stopped; run `livehunt resume <target>` (hosts are saved)." |
| `hosts filter` missing | "No host filter — I can't start. Which host is in scope?" |
| `cursor sane` FAIL | "DB was replaced; run `livehunt drain <target> --rebuild`." |
| `db readable` FAIL | "DB is unreadable; check `capture.log` and `docs/TROUBLESHOOTING.md`." |

Never start capture with an empty host filter, and never silently widen it.

---

## Snippet 1 — AGENTS.md (user rules)

```markdown
N. **"let's start `<target>`" / "continue `<target>`" = prepare the toolkit, then brief.**
   Run `livehunt up <target>` (doctor → start/resume → status → brief). Read `<target>/stash.md`
   and `STATE.md` first; never open a capture artefact on start. Reply with: capture state,
   principal labels (warn if only one — IDOR proves nothing), top 3 open hypotheses with their
   next test, the ledger (confirmed / dead / blocked), and exactly one next action.
   Record every test with `livehunt test <target> <H> <result>`; the ledger refuses a repeated
   action, so nothing is tested twice.
N+1. **Every finished analysis ends with `<target>/stash.md`** — coverage, not facts: run ledger,
   area → examined? → where the result lives → done/open/blocked, skip list, open queue,
   `Last exchange id`. Facts stay in `<target>.md`, position in `STATE.md`.
N+2. **Cheapest source first.** stash → one doc section → one `livehunt query`/SQL → sliced JSON.
   Never load whole `context.json` / `openapi.json` / `model.json`.
```

## Snippet 2 — hunt.md (playbook section)

```markdown
## Session start — "start <target>" / "continue <target>"

1. `livehunt up <target>` (manual: no systemd, no background start unless this runs it).
2. Check and say out loud: hosts filter set? labelled principals (one = horizontal tests are
   meaningless)? unclassified flows (cursor vs max)?
3. Brief in ≤8 lines: capture · principals · top 3 TODO with next test · ledger · one action.
4. Never re-test: `FALSE-POSITIVE` is dead, `BLOCKED` names the missing principal/object.
5. Record each test: `livehunt test <target> <H> <confirmed|dead|blocked|partial|observed>`.
6. End: `livehunt drain`, `livehunt stop`, then stash → STATE → target doc.

## Misunderstanding guard

Before any request, restate one line: *which principal, which object, which endpoint*.
Two of three unknown = not a test yet.
```

## The one-call contract

`livehunt agent <target> --json` returns `target`, `capture`, `flows`, `principals`,
`hypotheses.{open,dead,blocked,confirmed,top[]}`, `coverage`, `tests_recent`, `next`.
That is all an agent needs to plan a turn. See [`AI-GUIDE.md`](AI-GUIDE.md) for the prompts.

## Why this makes an agent better, not just faster

- The object graph gives the agent a **vocabulary** (`expense 4f2c`, `principal A`) instead of
  raw URLs, so hypotheses cite real objects and can be checked.
- The ledger gives it **memory** across sessions: what was tested, what died, what is blocked.
- The cursor gives it **incrementality**: new traffic is the only thing it has to think about.
- The host filter and read-only design give it **safety**: it cannot wander off-scope, and it
  cannot send a request by itself.
