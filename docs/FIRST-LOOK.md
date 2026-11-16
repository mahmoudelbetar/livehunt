# First look — 5 minutes, no experience needed

## What it is

A recorder for your own browser. You surf a target you are allowed to test; it keeps every
request and response, then tells you **which objects the app manages, which endpoints touch
them, and what to test next**.

It does not hack anything. It does not send requests. You still do the testing.

## What it needs

| Needs | Does not need |
|---|---|
| Python 3.9+ | an AI model |
| Google Chrome | an API key |
| permission to test the target | a proxy or CA certificate |
| | Burp, an account on any service, internet after install |

**No AI required.** Every classification is a deterministic rule (regex + path/param names).
An AI assistant is optional: it can read the brief and delta and propose the next test, but
the tool works alone. See `docs/AGENT.md` if you want that.

## Install (once)

```bash
git clone https://github.com/mahmoudelbetar/livehunt.git
cd livehunt && ./install.sh
livehunt version
```

## Use (every session)

```bash
# 1. start Chrome with the debugging port (separate profile, safe)
livehunt chrome --print        # copy-paste the printed command

# 2. create a folder for the target
livehunt init shop --hosts app.example.com

# 3. start watching + see the state
livehunt up shop

# 4. surf the app in that Chrome window (log in, open a list, open a detail, edit something)

# 5. ask what you have
livehunt drain shop
livehunt query shop objects
livehunt query shop collisions

# 6. stop
livehunt stop shop
```

## What you see after step 5

```
new flows: 120
| class | count |
| IDOR | 12 |
| BusinessLogic | 4 |
| SQLi | 3 |

## top candidates (one row per endpoint)
| class | score | n | request | first flow |
| IDOR | 3 | 6 | GET /api/expenses/{uuid} | 577 |
```

Read it like this:

- **class** — the bug family the traffic looks like. A tag is a *lead*, not a finding.
- **score** — how many signals matched (id in path, write verb, money field…).
- **n** — how many times that endpoint appeared.
- **first flow** — the request id, if you want to look at it.

Then:

```bash
livehunt query shop objects      # what the app manages
livehunt query shop writes       # objects with a write path (check the owner check)
livehunt query shop collisions   # the same object seen under two accounts  <- IDOR
```

## The one rule

**One test at a time, and write down the result.**

```bash
livehunt test shop 83 dead --note "403 for a foreign id"
```

`dead` = do not try again. `blocked` = needs another account/object. `confirmed` = keep the
flow id as evidence.

## When it helps / when it does not

| Helps | Does not help |
|---|---|
| finding which object each endpoint touches | anything you never clicked |
| spotting endpoints that touch the same object | encrypted or binary traffic |
| remembering what you already tested | proving impact (that is your job) |
| keeping A and B sessions apart | scanning or fuzzing (use Burp) |

## If something breaks

```bash
livehunt doctor shop
```

It prints OK/FAIL for Python, Chrome, capture, hosts, DB and disk. Then read
`docs/TROUBLESHOOTING.md` — it lists every failure we have hit and the fix.

## The honest summary

It is a notebook that never forgets an object or a session. The hunting is still yours.
