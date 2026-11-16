# Quickstart — from zero to your first hypothesis

Ten minutes, once per machine. Five minutes per target after that.

## 1. Install (2 min)

```bash
git clone https://github.com/your-org/livehunt.git
cd livehunt && ./install.sh
livehunt version
```

If `livehunt: command not found`, add `~/.local/bin` to your `PATH`.

## 2. Start Chrome with the DevTools port (1 min)

```bash
livehunt chrome --print
```

Copy the printed command into a terminal. It starts Chrome with a **separate profile**
(`~/.livehunt-chrome`) and `--remote-debugging-port=9222`.

Why a separate profile: Chrome 136+ refuses the debugging port on your default profile, and
you do not want your daily browsing mixed into a target capture.

Keep that Chrome window open. Log into the target **in that window**.

Verify:

```bash
curl http://127.0.0.1:9222/json/version
```

You should see a JSON blob with `"Browser": "Chrome/..."`.

## 3. Create the target (30 s)

```bash
livehunt init shop --hosts app.example.com
```

This creates `~/bounty/shop/` with:

```
shop/
  stash.md          coverage ledger (what you already examined)
  STATE.md          where you are, what is blocked
  live/
    contexts.json   browserContextId -> A/B/C
    rules.json      path excludes
    run.json        hosts + cdp (written on first start)
```

`--hosts` is the in-scope host list, comma separated. Nothing else is captured.

Each entry is normalised before matching, so all of these work:

| You type | Kept traffic |
|---|---|
| `app.example.com` | that host, any port |
| `app.example.com:8443` | that host, port 8443 only |
| `https://app.example.com/login` | same as `app.example.com` (scheme and path dropped) |
| `*.example.com` | `example.com` and every subdomain |
| `juice.local:3000, *.example.com` | both rules |

The canonical form is saved in `run.json`, and `capture.log` prints
`hosts normalised: '<what you typed>' -> <canonical>` whenever they differ.

## 4. Start and check (30 s)

```bash
livehunt up shop
```

You get: 8 doctor checks, the capture state, then a brief:

```
principals A=0
           ! only one labelled principal — horizontal tests (IDOR/BOLA) prove nothing yet
```

## 5. Label your browser context (30 s, once per session)

The `up` output (or `live/capture.log`) prints a line like:

```
contexts: E4BC584B…=unlabelled, CEB55E67…=unlabelled
```

Open `~/bounty/shop/live/contexts.json` and map the context your target tab lives in:

```json
{ "E4BC584B…": "A" }
```

Restart capture: `livehunt stop shop && livehunt up shop`.
Now every flow carries `label=A`.

## 6. Surf with intent (5–10 min)

Do these in order, slowly, one flow at a time:

1. log in, open your profile
2. create the core object, list it, open its detail, edit it
3. do the money action (add to cart, checkout, coupon)
4. open settings, upload something
5. if you have an admin or a second role, do the same screens

Do not hunt yet. You are producing coverage.

## 7. Drain

```bash
livehunt drain shop
```

You get a delta: flows added, class counts, top candidates per endpoint, new hypotheses.

## 8. Ask the object questions

```bash
livehunt query shop objects      # what the app manages, how many endpoints touch it
livehunt query shop writes       # object kinds with a write path
livehunt query shop collisions   # same object under two principals  <- IDOR money query
livehunt query shop object expense 4f2c-...
```

## 9. Test exactly one thing

Pick the top hypothesis. It already carries its next test. Restate it before sending:

> principal A · object expense/4f2c · GET /api/expenses/{uuid}/approvalData · change the uuid

Then record the outcome:

```bash
livehunt test shop 83 dead --flow 577 --note "403 for foreign uuid"
livehunt test shop 84 blocked --note "needs account B"
```

`dead` and `blocked` leave the TODO list. The ledger refuses the same action twice.

## 10. Stop and write it down

```bash
livehunt stop shop
```

Update `stash.md` (what you covered), `STATE.md` (next step). Next session starts with
`livehunt up shop` and the brief tells you where you stopped.

## Adding account B (the day IDOR starts paying)

1. In the same Chrome window, open a **new isolated context** (a second profile window) and
   log in as account B.
2. `livehunt up shop` → note the new context id.
3. Add it to `contexts.json` as `"B"`, restart.
4. Repeat the same five flows as B.
5. `livehunt query shop collisions` now lists objects seen under both labels.

## Next

- daily loop: [`WORKFLOW.md`](WORKFLOW.md)
- failures: [`TROUBLESHOOTING.md`](TROUBLESHOOTING.md)
- agent rules: [`AGENT.md`](AGENT.md)
