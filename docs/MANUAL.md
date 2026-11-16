# The livehunt manual

For someone who has never used it. No proxy, no certificates, no prior knowledge.

Read this once (15 minutes), run the demo, and you will understand every output the tool
prints for the rest of your hunting life.

Fast lookup later: [`REFERENCE.md`](REFERENCE.md) — every command, every field, every use case.

---

## 1. The problem this solves

Manual hunting fails in three quiet ways:

1. **You forget what you already tried.** Two hours in, you re-send the same request.
2. **You lose the object.** You have 1,000 requests and no idea which endpoint touches
   `expense 1001`, or whether you saw that object as one account or two.
3. **You cannot hand it to an AI.** Pasting raw traffic into a model burns context and the
   model still cannot tell which request belongs to which user.

livehunt fixes the *data*, not the thinking. It turns your traffic into:

> **object × principal × endpoint × class × test**

Once the data has that shape, a human or an AI can reason about it.

---

## 2. The mental model (the only idea you need)

Every request in a web app does three things:

| Question | Example | Why it matters |
|---|---|---|
| **Which object?** | `expense 1001` | IDOR lives here |
| **Which principal?** | account A | authorization lives here |
| **Which action?** | read / write | business logic lives here |

If you know those three for every request, you can answer the only question that pays:

> *"Did principal A read or write an object that belongs to B, through an endpoint that
> believed the client?"*

livehunt records exactly those three for every flow, then ranks the endpoints where the
answer is likely to be **no**.

---

## 3. What the tool stores (five tables, plain words)

| Table | What it is | Why you care |
|---|---|---|
| `flows` | every request/response | the evidence |
| `objects` | every id the app uses (`expense 1001`) | the nouns of the app |
| `touches` | which flow touched which object, read or write | the joins |
| `hypotheses` | one row per (class, method, endpoint) | what to test |
| `tests` | what you already tried and what happened | memory |

Nothing else. No magic. The power is that these five are **joined**, not listed.

---

## 4. See it in 60 seconds — the demo

No browser, no target, no traffic. It seeds a fake shop with two accounts.

```bash
livehunt demo
```

Real output (trimmed):

```
new flows: 20 (ids 1..20)

| class | count |
| IDOR | 7 |
| IDOR-write | 2 |
| BusinessLogic | 2 |
| SQLi | 2 |
| Auth | 2 |
| MassAssignment | 1 |
| BAC | 1 |
| FileUpload | 1 |
| SSRF | 1 |
| InfoLeak | 1 |

## top candidates (one row per endpoint)
| class | score | n | request | why | first flow |
| IDOR-write | 10 | 1 | PUT /api/expenses/{id} | object id in path; id + write verb; money field | 8 |
| IDOR-write | 7 | 1 | DELETE /api/expenses/{id}/receipt | object id in path; id + write verb | 9 |
| BusinessLogic | 6 | 2 | POST /api/orders | money/quantity field; state-changing counter | 12 |
```

Then:

```bash
livehunt query demo collisions
```
```
kind | id | labels | endpoints
expense | 1001 | A,B | 3
invoice | 77 | A,B | 1
```

**That second table is the whole product.** The same object was touched by two different
accounts through three endpoints. Every row there is a question worth asking.

```bash
livehunt agent demo
```
```
capture stopped · hosts shop.demo · flows 20 (cursor 20, 0 unclassified)
principals A=12 · anon=5 · B=3
hypotheses 11 open · 0 dead · 0 blocked · 0 confirmed
  H4 IDOR-write PUT /api/expenses/{id} s12.0 x1
     review manually
  H2 IDOR GET /api/expenses/{id} s5.0 x2
     replay the same request as account B; then change the id — compare body, not status
coverage 15 endpoints · 3 objects
next → livehunt test demo 4 <confirmed|dead|blocked>
```

Notice `H2` has score **5**, not 3: the object was seen under **A and B**, so the collision
boosted it. That is the tool connecting dots for you.

---

## 5. From data to a test — three worked examples

### Example 1 — the collision (IDOR)

```
expense | 1001 | A,B | 3 endpoints
```
**Question:** does `GET /api/expenses/1001/approvals` as A return B's approvals?
**Test:** replay as B, then change `1001` to `1002`.
**Record:** `livehunt test demo 3 confirmed --flow 6 --note "A's approval list returned B's"`

### Example 2 — the write with no owner read

```
livehunt query demo writes
kind | id | write_eps | read_eps
expense | 1001 | 2 | 1
```
**Question:** `PUT /api/expenses/1001` takes `amount` from the body. Is the owner re-checked on
write, or only on read?
**Test:** as B, `PUT` A's expense with `{"amount": 0}`. A 403 kills it; a 200 is the bug.

### Example 3 — the money field

```
BusinessLogic | POST /api/orders | s6 x2
```
**Question:** is `total` recomputed server-side, or trusted?
**Test:** send `{"total":0.01,"coupon":"SAVE10"}` and read the created order.
**Why x2 matters:** two identical flows means the endpoint is called twice per checkout —
that is a race window.

---

## 6. Where the AI fits (and where it does not)

The tool is a **notebook**, the model is a **mind**. Neither is enough alone.

| Job | Who |
|---|---|
| capture, classify, join objects, remember tests | livehunt (deterministic) |
| decide what a result *means*, propose the next test | AI + you |
| approve, send, judge impact | you |

The AI gets one block, not raw traffic:

```bash
livehunt agent navan --json
```
`capture`, `flows`, `principals`, `hypotheses.top[]`, `coverage`, `next`. ~250 tokens.

With that, the model can say *"H4 is a write-IDOR on an object already seen under A and B;
test it as B with the amount set to 0"* — because the **data** made the object and the
principals visible. Without it, the same model sees a URL and guesses.

The division of labour is in [`WHO-DOES-WHAT.md`](WHO-DOES-WHAT.md); the prompts are in
[`AI-GUIDE.md`](AI-GUIDE.md); the agent rules are in [`AGENT.md`](AGENT.md).

---

## 7. A real session, minute by minute

```
00:00  (Chrome is opened by `up`; `livehunt chrome --launch` does it alone)
00:02  livehunt init shop --hosts app.example.com
00:03  livehunt up shop               → doctor, capture, brief
00:04  label your context in live/contexts.json, restart
00:05  surf: login → list → detail → edit → money → settings
00:20  livehunt drain shop            → classes, candidates, hypotheses
00:22  livehunt query shop collisions → the objects seen under 2 accounts
00:25  pick one, write the 6-line hypothesis review, send it
00:30  livehunt test shop 83 dead --note "403 for foreign id"
00:31  livehunt brief shop            → next hypothesis
...
01:00  livehunt drain / stop / update stash.md + STATE.md
```

Two rules make this work: **one test at a time**, and **record every result**.

---

## 8. Reading the outputs

| File | Read it when |
|---|---|
| `delta.md` | after every drain — classes + top candidates |
| `context.md` | you need the app's shape: principals, objects, features, money endpoints |
| `chains.md` | you want combinations: object read+write, secret leaks, race+money |
| `brief` / `agent` | every turn — what to do next |
| `bundle.json` | sharing state without bodies |

`flows.jsonl` is raw evidence. You rarely open it; the five tables answer almost everything.

---

## 9. Mistakes that cost sessions

| Mistake | Result | Fix |
|---|---|---|
| surf 30 minutes before draining | 1,000 flows, no focus | drain every 2–3 minutes |
| test three things at once | cannot attribute the result | one hypothesis, one request |
| ignore `collisions` | the highest-value view stays unread | check it after every drain |
| skip recording | you retest the same thing next week | `livehunt test …` after every send |
| only one account | IDOR is unprovable | open B in a second context |
| paste bodies into the AI | context gone, no signal | `agent` + one flow when needed |

---

## 10. Glossary

- **flow** — one request/response pair.
- **object** — a thing the app manages, identified by an id (`expense 1001`).
- **principal** — who made the request (`A`, `B`, `anon`).
- **touch** — a flow reading or writing an object.
- **collision** — one object touched by two principals. The IDOR signal.
- **hypothesis** — a class + endpoint pair worth testing, with its next test.
- **ledger** — the record of tests you ran and their result (`dead`, `blocked`, `confirmed`).
- **agent block** — the 15-line state the AI reads instead of traffic.

---

## 11. What "good" looks like

A good session is not a pile of findings. It is:

- every endpoint you care about seen at least once
- every important object seen under **two** principals
- every hypothesis either `dead`, `blocked` or `confirmed` — none forgotten
- a `stash.md` that tells the next session exactly where you stopped

That is the difference between hunting and clicking.
