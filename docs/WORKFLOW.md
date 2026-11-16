# Daily workflow — live capture → hypothesis → test

One loop, ~10 minutes of setup, then it runs while you surf.

## 0. Before you start (30 s)

```bash
livehunt up navan            # doctor -> start/resume -> status -> brief
```
On a fresh target: `livehunt up navan --hosts app.navan.com` (the hosts filter is required).
- It prints `contexts: <id>=A, <id>=unlabelled`. Any `unlabelled` context is a profile
  whose traffic you will not be able to attribute — open `live/contexts.json`, add
  `"<id>": "B"`, restart.
- `live/rules.json` (optional): `{"exclude": "^/cdn-cgi/|/sentry"}` for per-app noise.

## 1. Plan the surf, not the clicking

Before touching the app, write the 3–5 flows you intend to exercise, in this order:

1. auth (login, logout, reset, 2FA)
2. the core object (create → list → detail → edit)
3. money (price, coupon, checkout, refund)
4. sharing / roles / admin
5. settings + upload

You are not exploring. You are producing traffic that covers *both sides* of each pair
(list vs detail, read vs write, A vs B).

## 2. Surf one flow, then drain

```bash
livehunt drain navan                        # classify + context.md + chains.md
```
Drain after **each** flow, not once at the end. The delta is ~15 lines.

## 3. Read the delta, not the database

```bash
livehunt query navan objects
livehunt query navan writes
livehunt query navan collisions
```
- `objects` — what the app actually manages, and how many endpoints touch each.
- `writes` — object kinds with a write path: check the owner check the read assumes.
- `collisions` — the same object under two labels. **This is the IDOR money query.**

Then read `live/context.md` (features, principals, money endpoints) and `live/chains.md`
(heuristic chains). They are 1-page reads, regenerated every drain.

## 4. One hypothesis at a time

Pick the top row. It already carries its class and its next test. Then:

- state the test (method + path + the field you change)
- say what proves it and what kills it
- ask for approval if it writes, deletes, moves money, or changes recovery
- send it, then record it: `livehunt test navan <hyp_id> <confirmed|dead|blocked|partial> --flow <id>`
  (the ledger refuses the same action twice — that is the duplicate guard)

Never batch three tests. A drain that produces five hypotheses is normal; testing five at
once destroys attribution.

## 5. Second principal (the only way IDOR becomes real)

Open account B in a separate Chrome context, label it in `contexts.json`, restart capture.
Then repeat the same 5 flows as B. `query.py collisions` becomes meaningful the moment
the same `kind/id` appears under `A` and `B`.

## 6. End of session (2 min)

```bash
livehunt stop navan
```
Then update, in this order:
1. `<target>/stash.md` — run ledger row, coverage cells, `Last exchange id`
2. `<target>/STATE.md` — what is confirmed, what is blocked, next step
3. `<target>/<target>.md` — only facts that changed (version bump)

`live/flows.jsonl` stays as the raw evidence. `live/live.db` is the queryable store.
Do not copy bodies into markdown.

## What this is good at / bad at

| Good | Weak |
|---|---|
| object/principal collisions (IDOR/BOLA) | anything you never clicked |
| sibling-endpoint coverage (6 reads on one object) | encrypted or binary bodies |
| money/state-change inventory | logic that needs two orders over days |
| finding the endpoint you did not know existed | proving impact — that is still your job |

## The habit that makes it pay

Drain → read 3 lines → test **one** thing → write the result down. The tool's value is
that it never forgets an object or a principal; yours is that you never test two things
at once.
