# livehunt reference — every command, every field, every use case

One page. Read the row you need, not the page. Every statement below is the behaviour of
`livehunt 0.6.1`.

- Why the tool exists and the idea behind it: [`MANUAL.md`](MANUAL.md) §1–§2.
- Something is broken: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md).
- You have never run it: [`QUICKSTART.md`](QUICKSTART.md) — 20 minutes, once.

---

## 1. What it is, in eight lines

| It does | It does not |
|---|---|
| Subscribes to Chrome DevTools **Network** domain and stores every request/response for the hosts you name | Send, replay, fuzz or scan anything — it has no way to make the target do work |
| Turns traffic into five joined tables: `flows`, `objects`, `touches`, `hypotheses`, `tests` | Judge whether a response is a bug — that stays your decision |
| Ranks endpoints by class weight, so the highest-value test is at the top of one block | Prove authorization is broken — it only shows *where* to look |
| Remembers what you already tested and refuses to let you repeat it | Capture WebSocket frames, iframes' inner frames, or traffic for hosts outside `--hosts` |
| Gives a human 15 lines and an AI ~250 tokens of state | Give the AI raw bodies — it prints counts, paths, ids, never bodies |

**The one question it exists to answer:** *did principal A read or write an object that
belongs to B, through an endpoint that believed the client?*

---

## 2. Command reference

`<t>` = target name (also the folder under `$BOUNTY_ROOT`, default `~/bounty`).

| Command | Flags that matter | Prints | Run it when | Then do |
|---|---|---|---|---|
| `init <t>` | `--hosts h1,h2` (required) `--cdp URL` | `initialised <dir>` | first time for this target | `up <t>` |
| `up <t>` | `--hosts` `--cdp` `--max-body N` `--keep-static` `--force` | Chrome line → doctor → status → brief → next hint | **every session start** (it also opens Chrome) | surf, then `drain` |
| `start <t>` | same as `up` | `started pid … · hosts=… · out=…` | you want capture without doctor/brief | surf |
| `resume <t>` | — | `started pid …` (or `already running`) | after `stop`, or machine reboot | surf |
| `stop <t>` | — | `stopped pid …` | end of session, before `reset` | update `stash.md`, `STATE.md` |
| `status <t>` | — | target, dir, capture, hosts, flows/max/cursor, objects, hypotheses, disk, last drain | "is it even running?" | fix what FAILs |
| `brief <t>` | — | capture, flows, principals, **todo + top 5 with next test**, ledger, coverage, next | after every `drain`, and each turn | pick the top row |
| `agent <t>` | `--json` | same as brief, machine-shaped: capture, hosts, flows, principals, hypotheses{open,dead,blocked,confirmed,top[]}, coverage, next | a model needs state in one call | feed it to the model, not the DB |
| `drain <t>` | `--rebuild` | `delta.md`: class counts, top 8 candidates, up to 10 new hypotheses; **also rewrites `context.md` + `chains.md`** | every 2–3 minutes of surfing, and after any new flow | read the delta, then one `query` |
| `query <t> <view>` | views below | one table | after a drain, or when a question is object-shaped | act on the row |
| `test <t> <H> <result>` | `--action "…"` `--flow N` `--note "…"` `--force` | `recorded H4 -> dead (PUT /api/x/{id})` | **immediately after every send** | next `brief` |
| `note <t> <text>` | `--flow N` | `note recorded: …`, then `notes N · last: …` in `brief` | a fact with no hypothesis row — score < 3, confirmed finding, out-of-band observation | stops you re-deriving it in markdown |
| `doctor <t>` | — | `OK`/`FAIL` per precondition | before blaming the target | `TROUBLESHOOTING.md` |
| `export <t>` | — | `exported …/bundle.json (N B, no bodies)` | attaching state to a report/issue | share the file |
| `demo [name]` | `--force` | a seeded 20-flow shop with two accounts | you want to see all outputs with no browser | `agent demo`, `query demo collisions` |
| `chrome` | `--print` (default) `--launch` `--binary PATH` | the exact Chrome command / launches it | Chrome not on the CDP port | `up <t>` |
| `reset <t>` | `--yes` `--all` `--purge` | archived/purged item count | clean slate for this target | `up <t>` |
| `version` | — | `livehunt 0.6.1` | sanity | — |

`up` = `chrome ensure` + `doctor` + `start|resume` + `status` + `brief` + next hint. It is the
only command you need at session start.

### `test` results — pick the honest word

| Result | Meaning | Hypothesis status after |
|---|---|---|
| `confirmed` | reproduced with impact | `CONFIRMED` |
| `dead` | tried cleanly, not a bug | `FALSE-POSITIVE` (never retested) |
| `blocked` | cannot test yet — missing principal/object | `BLOCKED` (names what is missing) |
| `partial` | some signal, not yet a finding | unchanged |
| `observed` | seen in traffic, no test sent | unchanged |

The ledger keys on `(hypothesis, action)`. Recording the same action twice is **refused** —
that refusal is the duplicate guard. `--force` only when the target itself changed.

---

## 3. Output dictionary — every field

### `status` / `brief` / `agent`

| Field | Means | Decision it drives |
|---|---|---|
| `capture running pid …` | the CDP listener is alive | stopped → `resume` |
| `hosts` | the only hosts recorded | empty → nothing will be captured; fix `--hosts` |
| `flows N (max id M)` | request/response pairs stored | 0 after surfing = wrong window or wrong host filter |
| `cursor C` | last flow id already classified | `C < M` → `drain` |
| `unclassified` | flows not yet classified | >0 → `drain` |
| `principals A=12 · anon=5` | flows per label | **one label only → IDOR is unprovable** |
| `! only one labelled principal` | warning, not an error | add account B in a second context |
| `H4 <class> <METHOD> <path> s7 x2` | hypothesis, score, times seen | score = priority; `x2` = called twice per flow = race window |
| `test: …` | the canned next test for that class | do exactly that, nothing wider |
| `ledger 2 confirmed · 5 dead · 1 blocked` | what you already learned | never re-test `dead` |
| `coverage 41 endpoints · 12 objects` | surface seen | low vs your map = keep surfing, do not hunt yet |
| `next → livehunt test …` | the tool's suggestion | a suggestion, not an order |

### `drain` output (`delta.md`)

| Line | Means | Use |
|---|---|---|
| `new flows: 30 (ids 83..112)` | only the delta was classified | read the delta, never the whole DB |
| `\| class \| count \|` | how many flows landed in each tagged class, then one `untagged N` line | tells you what kind of surface you just exercised; `untagged` is surface, not a failure |
| `## top candidates` — `score`, `n`, `request`, `why`, `first flow` | one row per endpoint, ranked by score | your shortlist; `why` says which rule fired |
| `## new hypotheses` — `class`, `request`, `next test` | endpoints worth testing, with the exact first move | pick one, write the 6-line review |

### How the score is computed (so you can trust or discount it)

`score` = sum of the weights of every class tag on that flow:

| Tag | Weight | Fires when |
|---|---|---|
| `IDOR-write` | 4 | id in path **and** `PUT`/`PATCH`/`DELETE` |
| `BAC` | 4 | privileged path prefix (`/admin`, `/internal`, …) |
| `IDOR` | 3 | id in path |
| `BusinessLogic` | 3 | money/quantity key in body or query |
| `MassAssignment` | 3 | `role`/`isAdmin`/`ownerId`-like key in body |
| `SSRF` | 3 | url-like param (`url`, `next`, `webhook`, `imageUrl`, …) |
| `Race` | 3 | `POST` with a counter-like key |
| `SQLi` | 2 | `q`/`sort`/`filter`-like param |
| `XSS` | 2 | text param (`q`, `name`, `comment`, …) |
| `FileUpload` | 2 | multipart or upload path |
| `Auth` | 2 | auth path (`/login`, `/reset`, `/token`, …) |
| `CSRF` | 1 | state change + cookie auth, no csrf header |
| `InfoLeak` | 1 | config/debug path |

Rules that follow: a hypothesis is created only when score ≥ 3. The same object seen under
**two labels** adds **+2** to every `IDOR*` hypothesis whose endpoint touches it — that is the
collision boost, and it is the single most valuable number in the tool. `x2` counts calls, not
principals.

Quirk worth knowing: `IDOR-write` has no canned next-test string, so it prints `review
manually`. Do that — it is the highest-weight class.

### `query` views — exact columns

| View | Columns | Answers |
|---|---|---|
| `objects` | kind, ids, endpoints, touches, ops | what nouns the app has, and how much surface touches each |
| `collisions` | kind, id, labels, endpoints | **same object under two principals — the IDOR shortlist** |
| `writes` | kind, id, write_eps, read_eps | objects written but barely read = write-path authorization gap |
| `ids` | request, class, n, principals | every id-in-path endpoint and who reached it |
| `money` | request, class, n | state-changing money/business-logic endpoints |
| `principals` | principal, flows, endpoints, first, last | which account covered which surface, and when |
| `once` | request, class, n | endpoints seen exactly once = thin coverage, go surf them again |
| `object <kind> <id>` | flow id, method+path, status, principal, class, op, source | the full history of one object — the best single view for a report |

---

## 4. Files it writes (and the only one you should not open)

| Path | Content | Open it? |
|---|---|---|
| `live/delta.md` | last drain: counts, candidates, new hypotheses | yes, after every drain |
| `live/context.md` | app shape: principals, objects, features, auth surface, money endpoints | when you need the map |
| `live/chains.md` | combination hints (read+write, secret leaks, race+money) | when one primitive is confirmed |
| `live/bundle.json` | portable state, no bodies | to share |
| `live/contexts.json` | `{browserContextId: "A"}` — the label map | once per session |
| `live/rules.json` | `{"exclude": ["^/cdn-cgi/"], "keep_static": false}` | rare |
| `live/flows.jsonl` | raw evidence, append-only | only to quote one flow |
| `live/live.db` | the five tables | **only through `query`** — raw dumps are where tokens die |
| `stash.md`, `STATE.md` | created by `init`; you maintain them | every session end |

---

## 5. Use cases — goal → surf → command → what proves it

Each block is copy-paste. Replace `shop` with your target name.

### U1 — "Is there an IDOR?" (the collision hunt)
- **Surf:** log in as A, open the core object (invoice/expense/booking), then repeat the same screens as B.
- **Run:** `livehunt drain shop` → `livehunt query shop collisions`
- **Look for:** one row where `labels` = `A,B`.
- **Proves:** that the object exists in two accounts and three endpoints touch it. Not yet a bug.
- **Test:** as A, request B's id. Bug when the **body** is B's data, not when the status is 200.
- **Record:** `livehunt test shop 3 confirmed --flow 61 --note "A read B's invoice body"`

### U2 — "The write path skips the owner check"
- **Surf:** edit and delete your own objects; watch that each object also has a read endpoint.
- **Run:** `livehunt query shop writes`
- **Look for:** `write_eps > read_eps`, or `read_eps = 0`.
- **Test:** as B, `PUT`/`DELETE` A's id. 403 kills it; 200 + state change is the bug.
- **Why it pays:** read and write are different code paths; teams routinely guard one.

### U3 — "Money is trusted from the client"
- **Surf:** add to cart, apply a coupon, check out, retry the payment step.
- **Run:** `livehunt query shop money` then `livehunt agent shop` (look for `x2`/`x3`)
- **Look for:** `BusinessLogic` rows; `x2` = the endpoint is hit twice per checkout = race window.
- **Test:** send `total:0.01` / `quantity:-1` / the same coupon twice concurrently. Compare what the server stored, not what the UI said.
- **Record:** `livehunt test shop 7 confirmed --flow 12 --note "order stored total 0.01"`

### U4 — "Vertical: low role reaching a high-role function"
- **Surf:** as A, open your settings; as C (admin), open the admin area.
- **Run:** `livehunt query shop ids` then filter `BAC` rows in `delta.md`
- **Test:** replay the admin request with A's token, then with no token. Compare status **and** body.
- **Note:** a hidden button is not a control; the request is.

### U5 — "Anonymous drift" (the cheapest high-value sweep)
- **Surf:** log out, then reload the same pages you saw logged in (list, detail, profile).
- **Run:** `livehunt query shop principals` → confirm `anon` has flows
- **Test:** replay the logged-in request with no cookie/token. Any 200 with real data is a finding.
- **Why:** the same endpoint then shows up under `anon` in `query principals`; the `BAC`/`InfoLeak` tags only tell you it is privileged-looking — the 200 from anon is the evidence.

### U6 — Mass assignment
- **Surf:** submit every profile/account/registration form once.
- **Run:** `livehunt drain shop` → rows with `MassAssignment`
- **Test:** add `role`, `isAdmin`, `verified`, `ownerId`, `balance` to the body, then re-read the object.
- **Proves:** server applied a field the UI never offered.

### U7 — Upload surface
- **Surf:** upload an avatar, an attachment, an import file.
- **Run:** `livehunt drain shop` → rows with `FileUpload`; `livehunt query shop object file <id>` if a file id appears
- **Test:** `.svg` with script, `.php` with `image/*` mime, then request the stored file. Execution is the bug; storage is not.

### U8 — Not testing the same thing twice (the memory case)
- **Run:** `livehunt brief shop` before you start, `livehunt test …` after every send.
- **Look for:** `ledger … dead` — those are closed forever.
- **Why:** duplication is the most common way a paid finding is earned twice and paid once.

### U9 — "Nothing looks interesting" (coverage, not guessing)
- **Run:** `livehunt query shop once` → endpoints you barely saw; `livehunt query shop objects` → families with one endpoint; `livehunt query shop principals` → endpoints anon never touched.
- **Do:** surf the thin areas with intent (the list/detail/edit triple for every object family).
- **Why:** most missed bugs are on a page the hunter opened once.

### U10 — Handing a confirmed finding to a report
- **Run:** `livehunt query shop object invoice 1001` (all flows touching it) → `livehunt export shop`
- **Gives:** the flow ids that are your evidence, plus a body-free state bundle.

---

## 6. Lookup tables

**"I want to know …" → run this**

| Question | Command |
|---|---|
| Where am I? | `livehunt brief shop` |
| Same thing, machine-readable | `livehunt agent shop --json` |
| What did I just capture? | `livehunt drain shop` |
| Which objects do two accounts share? | `livehunt query shop collisions` |
| Which objects are written but not read? | `livehunt query shop writes` |
| Which endpoints take an id? | `livehunt query shop ids` |
| Where is money handled? | `livehunt query shop money` |
| Which account covered what? | `livehunt query shop principals` |
| Where is my coverage thin? | `livehunt query shop once` |
| Everything about one object | `livehunt query shop object <kind> <id>` |
| Did I already try this? | `livehunt brief shop` → ledger, or `livehunt test` will refuse |

**"The output says …" → it means → do this**

| Output | Means | Do |
|---|---|---|
| `flows 0` after surfing | wrong window, wrong host, or idle page | check `contexts:` line in `live/capture.log`; re-check `--hosts` |
| `unclassified 26` | new traffic not yet classified | `livehunt drain shop` |
| `! only one labelled principal` | no B → no horizontal test | open B in a second Chrome context, label it, restart |
| `dead 5` | five hypotheses closed | never retest; pick from `brief` top |
| `blocked 1` | missing principal/object | read the note; supply the thing or park it |
| `H4 … review manually` | `IDOR-write`, no canned test | replay as B with a body change |
| `score 3` on a `SQLi` row | one weak tag | low priority unless the param is used in a real query |
| `x2` | endpoint called twice per flow | race/TOCTOU candidate |
| `no new flows (cursor 112, 112 recorded)` | you drained nothing new | surf, don't drain again |

---

## 7. Token ladder — cheapest source first

Climb and stop at the first rung that answers the question.

| Rung | Command | Cost | Answers |
|---|---|---|---|
| 1 | `livehunt brief shop` | ~120–150 tok | what to do next |
| 2 | `livehunt agent shop` | ~200–250 tok | the same, structured, for a model |
| 3 | `livehunt status shop` | ~80 tok | is capture alive, is the cursor sane |
| 4 | `livehunt drain shop` (delta only) | ~250–500 tok | what the new traffic contains |
| 5 | `livehunt query shop <view>` | ~100–300 tok | one object-shaped question |
| 6 | `livehunt query shop object <k> <id>` | ~150–400 tok | one object's full history |
| 7 | one flow's body | up to `--max-body` (256 KB) | the exact evidence for a report |

Rules that keep this true:

1. **Never open `live.db`, `flows.jsonl` or `context.json` equivalents** to "check" something the
   brief already answers.
2. **One question = one call.** Narrow the view; never page through a dump.
3. **No bodies in chat.** Pull a single flow, quote ≤5 lines, cite the flow id.
4. **Persist, don't print.** Big evidence goes to `live/` and gets cited by id.
5. `agent --json` replaces a paragraph of state in a prompt.

---

## 8. Asking an AI on top of it (so the answer is cheap *and* correct)

Give the model the `agent` block, not the traffic. Then use these six shapes:

| You say | Model should reply with |
|---|---|
| `brief` | ≤12 lines: capture, principals, top 5 + their one test, ledger, one next action |
| `run H4` | principal + object + endpoint in one line, the exact request, expected vs actual, stop condition |
| `what does x2 mean` | one line (called twice per flow → race window) |
| `compare A and B on GET /api/x/{id}` | body diff summary, not both bodies |
| `record it` | one `livehunt test …` line with a note that names the observable |
| `where are we` | the ledger: confirmed / dead / blocked / open — no new analysis |

Never ask it to "analyze the DB" or "look at all the traffic" — that is the expensive way to
get a worse answer than `brief`.

---

## 9. Recipes

**First 20 minutes (new target)**
```bash
livehunt init shop --hosts app.example.com
livehunt up shop                          # opens Chrome, doctor, capture, brief
# label the context in live/contexts.json, then: livehunt stop shop && livehunt up shop
# surf: login → profile → create → list → detail → edit → money → settings
livehunt drain shop
livehunt query shop collisions
livehunt test shop <H> <result> --note "…"
```

**Daily 90 minutes**
`up` → surf one flow → `drain` → `query collisions` → one hypothesis → `test` → `brief` → repeat.
Two clean attempts per hypothesis, then `dead`.

**Resume after a week**
`livehunt up shop` (brief is the state) → read the ledger → continue at `next`. Never re-derive.

**Two accounts (the day IDOR starts paying)**
Second Chrome context → surf the same screens as B → `drain` → `query collisions` should now
have rows. Until it does, horizontal testing is guesswork.

---

## 10. Limits — say these out loud before trusting a quiet result

- It captures **only** what Chrome's Network domain reports, for hosts in `--hosts`.
- Images, fonts, CSS, media, `.map` files are skipped unless `--keep-static`.
- `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key`, `Proxy-Authorization` are stored as
  `[REDACTED]` — replay needs a fresh login, by design.
- Bodies are capped at `--max-body` (default 262,144 bytes).
- WebSocket frames, service-worker traffic outside the Network domain, and traffic from another
  browser or CLI tool are **not** captured.
- Principals come from `contexts.json` labels; unlabelled sessions are `anon` or `sub:<hash>`.
- Classification is regex/heuristic: a clean class list is not a clean app, and a `dead` is only
  as good as the test you ran.
- It never sends a request. Approval, scope and impact stay with you.

---

## 11. Where everything else lives

| Doc | Use it for |
|---|---|
| `MANUAL.md` | the idea, the demo, three worked examples — read once |
| `QUICKSTART.md` | install → first hypothesis, click by click |
| `WORKFLOW.md` | the daily loop, habits, what it is good/bad at |
| `WHO-DOES-WHAT.md` | tool vs model vs human, and the handoff |
| `AI-GUIDE.md` | prompts, anti-patterns, token budget |
| `AGENT.md` | wiring the rules into `AGENTS.md` / `hunt.md` |
| `TROUBLESHOOTING.md` | every failure we have hit, and the fix |
| `FAQ.md` | legal, privacy, disk, multiple targets |
| `FIRST-LOOK.md` | 5-minute no-install demo |
