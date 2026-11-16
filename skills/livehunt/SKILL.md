---
name: livehunt
description: Passive live-capture hunting session. Use when the user says "livehunt", "start livehunt", "let's hunt <target> with livehunt", or asks to capture live traffic, map objects/principals, rank hypotheses, or keep a test ledger. Runs the toolkit, then drives one hypothesis at a time with the human.
sources: user_method
---

# livehunt — interactive hunting session

One session = capture → state → one hypothesis → one test → one record. Repeat.

The toolkit is **read-only**: it watches the user's own Chrome and never sends a request.
The human sends; you propose and record.

## 0. Preconditions — ask once, never assume

Before running anything, confirm in one message:

1. **target name** (the folder, e.g. `navan`)
2. **in-scope hosts** (comma separated) — without this the capture refuses to start
3. **which Chrome window/context is account A**, and whether a **B** exists
4. the user's authorization statement (scope / program)

If hosts are unknown, stop. Never widen scope to "capture everything".

## 1. Start

```bash
livehunt init <target> --hosts <host>    # only if the folder does not exist yet
livehunt up <target>                     # doctor → start/resume → status → brief
livehunt agent <target>                  # the 15-line state block (or --json)
```

Read the `agent` block, not the capture. Then brief in ≤8 lines:

```
capture · hosts · flows/cursor · principals · top 3 hypotheses + next test · ledger · one action
```

If only one principal is labelled, say it: horizontal tests (IDOR/BOLA) prove nothing yet.

## 2. The loop (interactive)

Repeat until the user stops:

1. **State** — `livehunt agent <target>` (cheap, always current).
2. **Pick one** — highest score, read-only before write, never a destructive one first.
3. **Review** — before any request, write:
   - Hypothesis (falsifiable, one sentence)
   - Supporting evidence (hypothesis id + endpoint + why)
   - Alternative explanation
   - What would falsify it
   - Exact request: method, path, headers, body, and which principal sends it
   - Confidence: low / medium / high + one reason
4. **Approval gate** — if it writes, deletes, moves money, or touches recovery: stop and ask.
5. **Send** — `curl` first; browser only when curl is blocked or the page is needed.
6. **Record** — `livehunt test <target> <H> <confirmed|dead|blocked|partial> --flow <id> --note "…"`
7. **Drain** when the user surfed — `livehunt drain <target>` (classifies only new ids).

Stop a hypothesis after **two clean attempts**. `dead` = never retry without a new reason.

## 3. Hard rules

- **Direct instruction = do exactly that, then stop.** A few lines of answer. No md files, no
  extra analysis, no unrequested plan, no "here is also what I would do next". Documentation is
  written when asked, or once at session end — never as the response to a small request.
- **livehunt is the live path; proxy2model is a separate tool.** Use livehunt unless the user
  names proxy2model. Never mix their artefacts in one answer, and never make a live question
  depend on a proxy2model rebuild.

- **Never open** `live.db`, `flows.jsonl`, `graph.db`, `context.json` or any `.jsonl` at session
  start. `agent` and the docs answer first.
- **Never test twice.** The ledger refuses a repeated (hypothesis, action); that refusal is the
  duplicate guard, not an obstacle.
- **Restate principal + object + endpoint** in one line before any request. Two unknown = not a
  test yet.
- **One next action per reply.** A menu is not a plan.
- **No bodies in chat.** If one is needed, pull that single flow and quote ≤5 lines.
- **Never invent.** "Not observed" is not "secure". Cite the hypothesis id or flow id.
- **Never send without approval**, and never widen the host filter.

## 4. Commands

| Command | When |
|---|---|
| `livehunt up <t>` | session start / resume |
| `livehunt agent <t> [--json]` | every turn before planning |
| `livehunt drain <t>` | after the user surfed |
| `livehunt query <t> objects\|writes\|collisions\|money\|ids\|principals\|once\|object <k> <id>` | targeted questions |
| `livehunt test <t> <H> <result> [--flow N] [--note …]` | after every test |
| `livehunt note <t> "…" [--flow N]` | a fact with no hypothesis row (score < 3, out-of-band) |
| `livehunt brief <t>` | when the user asks "where are we" |
| `livehunt doctor <t>` | before blaming the target |
| `livehunt reset <t> --yes` | only on explicit request — archives the state |
| `livehunt stop <t>` | session end |

Column meanings, the score maths and ten worked use cases: `docs/REFERENCE.md`.

## 5. Failure handling

| Symptom | Action |
|---|---|
| `chrome cdp` FAIL | `livehunt up` launches Chrome itself; if that failed, `livehunt chrome --launch` |
| capture stopped | `livehunt resume <t>` (hosts are saved) |
| `flows 0` after surfing | wrong host filter, wrong window, or idle page — check `agent` hosts |
| `db readable` FAIL / cursor skew | `livehunt drain <t> --rebuild` |
| `database is locked` | two captures on one folder — `livehunt stop`, keep one |
| only one principal | not an error — propose read-only tests, ask for B |

Full list: `docs/TROUBLESHOOTING.md` in the livehunt repo.

## 6. End of session

```bash
livehunt drain <t>
livehunt stop <t>
```

Then update, in this order: `<target>/stash.md` (coverage), `STATE.md` (position),
`<target>.md` (facts that changed). Raw bodies stay in `live/`.

## 7. Reply style

- Brief: ≤8 lines, no preamble.
- Hypothesis review: the 6 fields above, nothing else.
- After a test: one line (what it proved / killed) + the next action.
- Every reply ends with the 4-line block: Issue / Root cause / Next step / Needs from you.
