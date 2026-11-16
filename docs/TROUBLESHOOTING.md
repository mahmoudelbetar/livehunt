# Troubleshooting

Every failure we have actually hit. Find the symptom, apply the fix, re-run `livehunt doctor`.

## Doctor first

```bash
livehunt doctor <target>
```

It checks: python version, deps, live dir, root writable, Chrome CDP, capture process,
hosts filter, context labels, DB readability, cursor sanity, disk.

| Check | FAIL means |
|---|---|
| `chrome cdp` | Chrome is not running with `--remote-debugging-port=9222`, or another process owns 9222 |
| `capture process` | the background process died — read `live/capture.log` |
| `hosts filter` | `run.json` has no usable hosts; start with `--hosts` |
| `hosts filter narrow` | the filter is `*` — every host in the browser is captured; name the real host |
| `db readable` | `live.db` is corrupt or half-copied — see "database" below |
| `cursor sane` | DB was replaced but the cursor was not — see "database" below |

## Symptom → fix

### `chrome cdp` FAIL, connection refused

- Chrome was started normally (no debugging flag). `livehunt up` launches it for you; to do
  it by hand use `livehunt chrome --launch`.
- **Chrome 136+**: the debugging port is ignored on the default profile. The launched command
  always includes `--user-data-dir`; keep it.
- Port 9222 already used: `ss -ltnp | grep 9222` (Linux) / `lsof -i :9222` (macOS).
  Kill the old instance or start Chrome on `--remote-debugging-port=9333` and run
  `LIVE_CDP=http://127.0.0.1:9333 livehunt up <target>`.

### `flows 0` after surfing

1. **Wrong host filter.** The capture only keeps hosts in `--hosts`. Check the exact host in
   the address bar (`app.example.com` vs `www.app.example.com`). A port is allowed
   (`juice.local:3000`); a scheme or path is stripped, and `capture.log` shows the normalised
   list (`hosts=['app.example.com']`).
2. **Wrong window.** Traffic from another Chrome instance is invisible. Use the window the
   printed command opened.
3. **Page idle.** A static page produces no API calls. Click into the app.
4. **SPA served from cache.** Hard reload (Ctrl/Cmd-Shift-R) once.
5. **Contexts.** If `contexts:` lists your tab as `unlabelled`, flows are captured but carry
   no `A/B` label — add the id to `contexts.json` and restart.

### Flows arrive but `resp_body` is empty

- Redirects and 304s have no body — expected.
- Preflight `OPTIONS` — expected.
- Bodies served by a service worker can miss `getResponseBody`. Reload with DevTools open.
- PDFs are rendered by Chrome's internal viewer; the stored body is the viewer HTML, not the
  PDF bytes. Use curl for PDF evidence.

### `websocket closed` / capture died

The capture reconnects up to 5 times, 2 s apart. If Chrome was closed, restart Chrome and:

```bash
livehunt resume <target>
```

`run.json` remembers hosts and CDP, so no arguments are needed.

### `database is locked`

Two captures are writing the same `live.db`. Only one is allowed:

```bash
livehunt status <target>     # see the pid
livehunt stop <target>
```

### `no such column: context` (old DB)

Schema migration runs automatically on start. If you see it from an old copy, run:

```bash
livehunt drain <target> --rebuild
```

### I want a clean slate for this target

```bash
livehunt reset <target> --yes            # archives to live/archive/<ts>/
livehunt reset <target> --yes --purge    # delete, no archive
```
Do not `rm -rf` the live folder while capture runs — reset stops it first.

### Cursor ahead of max id (DB replaced / restored)

`cursor sane` FAIL. Reset the cursor by rebuilding:

```bash
livehunt drain <target> --rebuild
```

This re-classifies every flow, clears `objects`/`touches` and rebuilds hypotheses. Tests you
already recorded stay in `tests`.

### `only one labelled principal` warning

Not an error. It means IDOR/BOLA tests cannot prove anything yet. Open account B in a second
Chrome context, label it `B`, repeat the flows.

### Hypothesis keeps reappearing after a test

You recorded it as `partial`/`observed`, which leaves it TODO. Record `dead` (killed) or
`blocked` (needs a principal/object) so it leaves the list.

### Disk growing fast

Pollers produce identical flows. Options:

- raise `--max-body` down (e.g. `--max-body 65536`) at start
- add noisy paths to `rules.json` → `exclude`
- drain and archive: `livehunt export <target>`, then trim `flows.jsonl`

`livehunt status` shows total size.

### Windows / WSL

- Run Chrome on **Windows** and the toolkit wherever you like: WSL can reach
  `127.0.0.1:9222` when Windows localhost forwarding is on (default in WSL2).
- If it is not, use the Windows host IP from `cat /etc/resolv.conf` and
  `LIVE_CDP=http://<ip>:9222`.
- Paths: `BOUNTY_ROOT=C:\Users\you\bounty` works in PowerShell; in WSL use
  `BOUNTY_ROOT=/mnt/c/Users/you/bounty`.

### `pipx install` fails

```bash
python3 -m pip install --user requests websocket-client
python3 -m pip install --user .
```

### Everything looks fine but `brief` shows nothing

No hypotheses means no class scored ≥3. Look at `live/delta.md` and
`livehunt query <target> once` — usually the traffic was static assets or config JSON.

## Still stuck

Collect and attach:

```bash
livehunt doctor <target>
tail -40 ~/bounty/<target>/live/capture.log
livehunt export <target>          # no bodies, safe to share
```

Open an issue with those three.
