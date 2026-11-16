# FAQ

**Does it send requests to the target?**
No. It subscribes to Chrome's DevTools `Network` domain and reads bodies Chrome already fetched.
It cannot replay, fuzz, or crawl. Every active test is yours, with your approval.

**Is this legal / allowed?**
It is a recorder for traffic your own browser generates in a session you are authorized to run.
Whether you are authorized is decided by the program's scope, not by the tool. Keep `--hosts`
tight, respect rate limits and test-account rules.

**Does it store my passwords or tokens?**
- `Authorization`, `Cookie`, `Set-Cookie`, `X-Api-Key` are masked in the stored headers.
- Bodies are stored verbatim (they are evidence) — a login response can contain a token.
  Treat `~/bounty/<target>/live/` as sensitive. `livehunt export` produces a bundle **without
  bodies**, safe to share in an issue.
- The principal id is `sha256(subject)[:12]`, never the token or email.

**How much disk?**
~50 KB per flow at the default 256 KB body cap, less for JSON. Heavy pollers dominate; lower
`--max-body` or add paths to `rules.json` → `exclude`. `livehunt status` shows the total.

**Does it work with Firefox / Safari?**
No. The capture layer is Chrome DevTools Protocol. Firefox has a different remote protocol.

**Can I use a proxy instead of CDP?**
Yes, but then you need a CA certificate and the browser must route through it. CDP avoids both
and works with the browser you already use. The trade-off: only that Chrome instance is seen.

**Why do some flows have `principal=unknown`?**
They were captured before a context was labelled or before the attribution column existed.
Old rows are not rewritten; new rows are labelled. Nothing is lost — the object graph still
covers them.

**Why is `objects` showing `obj` as a kind?**
The id had no nameable parent (`/api/items/{uuid}` with no body key). It is still a valid
object key for joins; the kind is just a label.

**Multiple targets at once?**
Yes: one capture process per target, each with its own `--out` and `--hosts`. Never point two
captures at the same folder (`database is locked`).

**Does it need an AI model / an API key?**
No. It is plain Python + Chrome DevTools. Classification is deterministic rules, there is no
model, no network call and no telemetry. An AI assistant is optional: point it at
`docs/AGENT.md` and it can read `brief`/`delta` and suggest the next test — but a human with a
terminal gets the same output.

**Does it replace Burp?**
No. It replaces the *flat history problem*: which object, which principal, what did I already
test. Keep Burp for Repeater, scanning and precise payload work.

**Can I share my capture with a teammate?**
Share `livehunt export` (no bodies) and the docs. Never share `flows.jsonl` — it contains
session tokens and third-party data.

**Why does Chrome 136+ need `--user-data-dir`?**
Chrome refuses `--remote-debugging-port` on the default profile (security change). The printed
command uses a dedicated profile; your normal browsing stays untouched.

**Where do I report a bug?**
Open an issue with `livehunt doctor`, the last 40 lines of `capture.log`, and
`livehunt export <target>`. Those three cover almost every report.
