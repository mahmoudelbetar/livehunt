# Working with an AI on top of livehunt

The tool is built so an AI can reason about a session **without reading traffic**. It emits
compact, stable, body-free state; the AI proposes one test at a time; you approve and send it.

This guide is the interaction contract. The machine-readable version is in
[`AGENT.md`](AGENT.md).

## The one call

```bash
livehunt agent <target>          # 15 lines, human + AI readable
livehunt agent <target> --json   # same data, stable keys
```

```json
{
  "target": "navan",
  "capture": { "running": true, "hosts": "app.navan.com", "since": "..." },
  "flows": { "flows": 1200, "cursor": 1188, "unclassified": 12 },
  "principals": { "A": 1200 },
  "hypotheses": { "open": 10, "dead": 0, "blocked": 0, "confirmed": 0,
                  "top": [ { "id": 91, "class": "IDOR-write",
                             "method": "DELETE", "path_template": "/api/.../{uuid}",
                             "score": 7, "count": 2, "next_test": "..." } ] },
  "coverage": { "endpoints": 249, "objects": 47, "seen_once": 0 },
  "next": "livehunt test navan 91 <confirmed|dead|blocked>"
}
```

One call replaces "read the DB, read the docs, read the flows". That is the whole point.

## Session rhythm — five prompts

### 1. Start
> `livehunt agent navan` — tell me where we are, the top 3 open hypotheses with their next
> test, and the single next action. Do not open any capture file.

The AI should answer in ≤8 lines and name the principal(s). If only one principal is labelled,
it must say that horizontal tests prove nothing yet.

### 2. Triage after a drain
> `livehunt drain navan` finished. Give me the 2 best candidates and why, ranked by damage ×
> evidence. One paragraph each, cite the hypothesis id and the endpoint.

### 3. Hypothesis review (before any request)
> Review H83. State the hypothesis, the supporting evidence, the alternative explanation,
> what would falsify it, and the exact request to send (method, path, headers, body).
> Confidence: low/medium/high with one reason.

This forces the AI to commit to a falsifiable claim before you spend a request.

### 4. Test request (approval gate)
> The test writes/deletes/moves money. Restate principal + object + endpoint, expected result,
> stop condition, and the risk. Wait for my approval.

The AI never sends. It proposes; you approve; you send (curl or Repeater).

### 5. Record
> Record it: `livehunt test navan 83 dead --flow 577 --note "403 for a foreign uuid"`.

`dead` and `blocked` leave the TODO list. A dead hypothesis is not retried without a new reason.

### 6. End of session
> `livehunt drain navan`, then update `stash.md` (coverage), `STATE.md` (position), and the
> target doc (facts changed). Then stop capture.

## Rules the AI must follow

1. **`agent` first, always.** No capture artefact at session start.
2. **One hypothesis, one test.** Never batch three tests — attribution dies.
3. **Restate principal + object + endpoint** before any request. Two unknown = not a test.
4. **No bodies into the chat.** If a body is needed, pull one flow by id and quote ≤5 lines.
5. **Never invent.** "Not observed" is not "secure". Cite the hypothesis id or flow id.
6. **Respect the ledger.** `dead` is dead. `blocked` names the missing account/object.
7. **Propose, don't send.** The approval gate is the human's.
8. **One next action per reply.** A menu is not a plan.

## Anti-patterns

| Don't | Why it fails |
|---|---|
| "Find all the bugs in my capture" | no object, no principal, no falsifiable claim |
| paste a 200 KB JSON body into chat | burns context, adds no signal |
| test 5 hypotheses at once | you cannot tell which one produced the result |
| ask the AI to re-read `flows.jsonl` | the delta and the object graph already answer it |
| re-run a dead hypothesis | the ledger exists to stop this |
| let the AI send the request | approval and scope are yours |

## Token budget (why this stays cheap)

| Action | Cost |
|---|---|
| `livehunt agent` | ~250 tokens |
| `livehunt drain` delta | ~300 tokens |
| `livehunt query collisions` | ~100–400 tokens |
| one flow body (only when needed) | ~1–3k tokens |
| whole `flows.jsonl` | **never** |

A full session — start, three drains, three tests, write-up — stays under ~5k tokens of tool
output. That is the difference between an AI that helps and an AI that drowns.

## Multi-session, multi-target

- One target = one folder = one DB. The AI should ask which target before running anything.
- Two AI sessions on the same target: run `agent` before each action; the ledger is the shared
  memory, so a test recorded by one session is visible to the other.
- If a session is lost, `livehunt up <target>` restores the state in one command.

## A good exchange looks like this

> **You:** `livehunt agent navan`
> **AI:** capture running, 1200 flows, principal A only. Top: H83 IDOR `GET /api/expenses/{uuid}`
> (x6), H84 SQLi `GET /api/spending/trips` (x6). Next: test H83 as B — but B is missing, so I
> can only test H84 read-only.
> **You:** review H84.
> **AI:** hypothesis: `sort`/`filter` reach SQL unsafely… falsified if `'` returns the same body
> and timing. Request: `GET /api/liquid/user/spending/trips?sort='` …
> **You:** approved, sending.
> **AI:** record it and pick the next.

That is the whole method: **state → one claim → one request → one record.**
