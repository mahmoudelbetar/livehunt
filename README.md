# livehunt

**Passive live-capture toolkit for web bug hunters.** Watch your own browser, keep every
request/response, and get a ranked list of hypotheses tied to real objects and real
principals — while you surf.

Built for the gap between "I clicked around for an hour" and "I know what to test next".

**No AI required.** Classification is deterministic (rules, no model, no API key, no telemetry).
An AI assistant is optional — it can read the brief and delta and propose the next test
(see [`docs/AGENT.md`](docs/AGENT.md)); the tool works alone.

## What it does

1. **Captures** full requests and responses from your own Chrome via the DevTools protocol
   (no proxy, no CA certificate). It never sends a request to the target.
2. **Classifies** every flow into a bug class (IDOR, BAC, business logic, mass assignment,
   SQLi, XSS, SSRF, upload, CSRF, auth, race, info leak) and scores it.
3. **Builds an object graph** — every id in path/query/body/response, with which endpoint and
   which principal touched it. This is what turns 1,000 flows into `object × endpoint × principal`.
4. **Attributes** each flow to a principal: a browser-context label (`A`/`B`/`C`) plus a
   hashed JWT subject. Open account B in a second Chrome context and horizontal tests become
   a SQL query.
5. **Keeps a ledger** — hypotheses, tests you already ran, dead ends. Nothing is tested twice.

## Install

```bash
git clone https://github.com/mahmoudelbetar/livehunt.git
cd livehunt
./install.sh                 # pipx if available, else pip --user
# or: pipx install .
# or: pip install --user .
```

Requirements: Python 3.9+, Google Chrome, `requests`, `websocket-client` (installed for you).

## 60-second start

No target yet? See what the tool does first:

```bash
livehunt demo        # 20 synthetic flows, 11 hypotheses, real outputs
livehunt query demo collisions
```


```bash
livehunt chrome --launch     # opens Chrome with the DevTools port (done for you by `up`)
livehunt init shop --hosts app.example.com
livehunt up shop             # doctor -> Chrome -> start -> status -> brief
# surf the app: auth -> core object -> money -> roles -> settings
livehunt drain shop          # ranked delta + context.md + chains.md
livehunt query shop collisions
livehunt stop shop
```

Data lives in `~/bounty/<target>/live/` (`BOUNTY_ROOT` overrides).

## Commands

| Command | Purpose |
|---|---|
| `livehunt demo` | seed a synthetic target — see every output with no browser |
| `livehunt chrome` | print/launch the Chrome command with `--remote-debugging-port=9222` |
| `livehunt init <t> --hosts h` | create the target folder + configs + stash/STATE stubs |
| `livehunt up <t>` | doctor → start/resume → status → brief |
| `livehunt drain <t>` | classify new flows, rebuild `context.md` / `chains.md` |
| `livehunt query <t> <view>` | `objects` `writes` `collisions` `money` `ids` `principals` `once` `object <kind> <id>` |
| `livehunt test <t> <H> <result>` | record a test; refuses the same action twice |
| `livehunt brief <t>` | what to do next, in 15 lines |
| `livehunt agent <t> [--json]` | one compact block an AI can ingest in a single call |
| `livehunt doctor <t>` | health checks |
| `livehunt export <t>` | portable state bundle (no bodies) |
| `livehunt reset <t> --yes` | archive the capture state and start the target fresh (`--purge` deletes, `--all` also resets config) |

## Safety and scope

- **Read-only by design.** The tool subscribes to Chrome's Network domain; it cannot and does
  not send requests to the target. Active testing stays your decision.
- **Host filter is mandatory** (`--hosts`). Anything else in the browser is ignored. Entries
  may be a hostname, `host:port`, a full URL, or `*.domain` — they are normalised on start.
- `Authorization`, `Cookie`, `Set-Cookie` and `X-Api-Key` are masked in the stored headers.
- Bodies are stored on disk (they are your evidence) and are **never printed** by the
  classifier — only counts, paths and flow ids.
- Only capture traffic you are authorized to test. The tool does not grant permission.

## Use it as an agent skill

`install.sh` also copies `skills/livehunt/` into `~/.agents/skills/`. Then say **"livehunt"**
in your AI session: it starts the toolkit, reads the state block, and drives one hypothesis at
a time with you. The skill follows [`docs/AI-GUIDE.md`](docs/AI-GUIDE.md).

## Docs

- [`docs/MANUAL.md`](docs/MANUAL.md) — **start here**: the idea, the demo, three worked examples
- [`docs/FIRST-LOOK.md`](docs/FIRST-LOOK.md) — 5-minute guide for a first-time hunter
- [`docs/QUICKSTART.md`](docs/QUICKSTART.md) — install → first hypothesis, step by step
- [`docs/WORKFLOW.md`](docs/WORKFLOW.md) — the daily loop
- [`docs/TROUBLESHOOTING.md`](docs/TROUBLESHOOTING.md) — every failure we have hit, and the fix
- [`docs/WHO-DOES-WHAT.md`](docs/WHO-DOES-WHAT.md) — tool vs model vs human, and the handoff between them
- [`docs/AI-GUIDE.md`](docs/AI-GUIDE.md) — how to work with an AI assistant, prompt by prompt
- [`docs/AGENT.md`](docs/AGENT.md) — wiring it into an AI agent (AGENTS.md / hunt.md rules)
- [`docs/FAQ.md`](docs/FAQ.md) — legal, privacy, disk, multiple targets

## Test it

```bash
python3 tests/selftest.py    # 17 checks, no browser and no target needed
```

## Status

v0.1 — used daily on real engagements. Roadmap: response-shape diffing across principals,
state-machine chains, export to report drafts. Issues and PRs welcome.

MIT licensed.
