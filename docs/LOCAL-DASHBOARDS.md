# Local read-only dashboards

Use a dashboard as a static lens over an existing capture. It should help a hunter find a
principal, object, endpoint, or request quickly; it must not become a second testing tool.

## Contract

- No `fetch`, XHR, WebSocket, auto-refresh, or target request.
- Copy-to-clipboard is the only side effect.
- Snapshot data is generated from the local capture and is not a live view.
- Stable object IDs are deduplicated; alternate classifier type labels remain attached.
- Each object shows its owner and the other principals that reached it.
- Accounts from approved local session files may appear even when they have zero captured flows.
- Tokens and cookies may be included only when the operator explicitly requests them. They stay
  in the local artifact, are masked by default, and are never committed, reported, or pasted into
  chat. A reveal is local and temporary.

The dashboard is a map and clipboard, not Repeater. Active testing remains a separate approved
step and must be recorded in the hunting ledger.
