#!/usr/bin/env python3
"""Passive CDP capture: full request/response bodies from the operator's own Chrome.

Read-only. It never sends a request to the target; it only subscribes to Chrome's
Network domain and reads bodies Chrome already fetched.

Writes:
  <out>/flows.jsonl   append-only, one line per completed exchange
  <out>/live.db       sqlite: flows (+ context/label/sub/principal)

Config (optional, hot-read at start):
  <out>/contexts.json   browserContextId -> A/B/C
  <out>/rules.json      {"exclude": ["^/cdn-cgi/"], "keep_static": false}

Usage:
  cdp_capture.py --hosts app.navan.com --out /root/bounty/navan/live
  cdp_capture.py --hosts app.navan.com --out ... --duration 20   # smoke test

--hosts accepts hostnames, host:port, full URLs and *.wildcards (see hosts.py).
"""
import argparse, base64, hashlib, json, os, queue, re, signal, sqlite3, sys, threading, time
from urllib.parse import urlsplit

import requests
import websocket  # websocket-client

try:                                   # imported as a package
    from .hosts import DEFAULT_PORTS, HostFilter, normalize_hosts
except ImportError:                    # run as a script
    from hosts import DEFAULT_PORTS, HostFilter, normalize_hosts

SKIP_MIME = ("image/", "font/", "video/", "audio/", "text/css")
SKIP_EXT = re.compile(r"\.(png|jpe?g|gif|svg|webp|avif|ico|woff2?|ttf|eot|css|mp4|webm|map)(\?|$)", re.I)
DEFAULT_EXCLUDE = r"^/cdn-cgi/|^/__cf|/sentry|\.well-known|favicon|/rum\b"

SCHEMA = """
create table if not exists flows(
  id integer primary key autoincrement,
  ts real, session text, target text, context text, label text, sub text, principal text,
  method text, url text, host text, path text, path_template text,
  status integer, mime text,
  req_headers text, req_body text, resp_headers text, resp_body text,
  req_size integer, resp_size integer, truncated integer,
  sha text, class text, score real, tags text
);
create index if not exists idx_flows_host on flows(host);
create table if not exists meta(k text primary key, v text);
create table if not exists hypotheses(
  id integer primary key autoincrement,
  host text, class text, method text, path_template text,
  first_flow integer, last_flow integer, count integer default 1,
  score real, why text, next_test text, status text default 'TODO',
  unique(host, class, method, path_template)
);
"""


def mask(headers: dict) -> dict:
    out = {}
    for k, v in (headers or {}).items():
        if k.lower() in ("authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization"):
            out[k] = "[REDACTED]"
        else:
            out[k] = v
    return out


def path_template(path: str) -> str:
    p = path.split("?")[0]
    p = re.sub(r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", "/{uuid}", p)
    p = re.sub(r"/\d+", "/{id}", p)
    p = re.sub(r"/[0-9a-f]{16,}", "/{hex}", p)
    return p


def _token(headers: dict):
    h = {k.lower(): v for k, v in headers.items()}
    a = h.get("authorization", "")
    if a.lower().startswith("bearer "):
        return a[7:].strip()
    if a and a.count(".") >= 2:
        return a.strip()
    m = re.search(r"(?:^|;\s*)token=([^;]+)", h.get("cookie", ""))
    return m.group(1) if m else None


def _subject(headers: dict) -> str:
    """Stable, non-reversible id for whoever made the call. Never stores the token."""
    tok = _token(headers)
    if not tok:
        return ""
    try:
        p = tok.split(".")[1]
        p += "=" * (-len(p) % 4)
        d = json.loads(base64.urlsafe_b64decode(p))
        for k in ("email", "sub", "username", "user", "id"):
            if d.get(k):
                return hashlib.sha256(str(d[k]).encode()).hexdigest()[:12]
    except Exception:
        pass
    return hashlib.sha256(tok.encode()).hexdigest()[:12]


class Writer(threading.Thread):
    def __init__(self, out, db):
        super().__init__(daemon=True)
        self.q = queue.Queue(maxsize=5000)
        self.out = out
        self.db = db
        self.fh = open(os.path.join(out, "flows.jsonl"), "a", encoding="utf-8")
        self.conn = sqlite3.connect(db, check_same_thread=False)
        self.conn.execute("pragma journal_mode=WAL")
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("pragma table_info(flows)")}
        for c, t in (("context", "text"), ("label", "text"), ("sub", "text"), ("principal", "text")):
            if c not in cols:
                self.conn.execute(f"alter table flows add column {c} {t}")
        self.conn.execute("create index if not exists idx_flows_ctx on flows(context)")
        self.conn.execute("create index if not exists idx_flows_principal on flows(principal)")
        self.conn.commit()
        self.n = 0

    def run(self):
        while True:
            row = self.q.get()
            if row is None:
                break
            self.fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            self.fh.flush()
            for attempt in (1, 2):
                try:
                    self.conn.execute(
                        "insert into flows(ts,session,target,context,label,sub,principal,method,url,host,path,"
                        "path_template,status,mime,req_headers,req_body,resp_headers,resp_body,req_size,"
                        "resp_size,truncated,sha,class,score,tags)"
                        " values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (row["ts"], row["session"], row["target"], row.get("context"), row.get("label"),
                         row.get("sub"), row.get("principal"), row["method"], row["url"], row["host"],
                         row["path"], row["path_template"], row["status"], row["mime"],
                         row["req_headers"], row["req_body"], row["resp_headers"], row["resp_body"],
                         row["req_size"], row["resp_size"], row["truncated"], row["sha"],
                         None, None, None))
                    self.conn.commit()
                    self.n += 1
                    break
                except sqlite3.OperationalError as e:
                    if attempt == 2:
                        print("write error:", e, file=sys.stderr)
                    time.sleep(0.2)

    def put(self, row):
        try:
            self.q.put_nowait(row)
        except queue.Full:
            print("queue full, dropping", file=sys.stderr)


class CDP:
    def __init__(self, ws_url, hosts, writer, max_body, keep_static, labels, exclude):
        self.ws = websocket.create_connection(ws_url, max_size=None, suppress_origin=True)
        self.hosts = hosts
        self.w = writer
        self.max_body = max_body
        self.keep_static = keep_static
        self.next_id = 0
        self.pending = {}
        self.reqs = {}
        self.sess_ctx = {}
        self.tgt_ctx = {}
        self.labels = labels or {}
        self.exclude = exclude
        self.stop = False

    def send(self, method, params=None, session=None):
        self.next_id += 1
        mid = self.next_id
        msg = {"id": mid, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        self.pending[mid] = (method, params or {})
        self.ws.send(json.dumps(msg))
        return mid

    def call(self, method, params=None, session=None):
        mid = self.send(method, params, session)
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result") or {}
            self.handle(msg)

    def handle(self, msg):
        if "id" in msg and msg["id"] in self.pending:
            method, params = self.pending.pop(msg["id"])
            if method == "Target.attachToTarget":
                sess = (msg.get("result") or {}).get("sessionId")
                tid = (params or {}).get("targetId")
                if sess:
                    if tid and self.tgt_ctx.get(tid):
                        self.sess_ctx[sess] = self.tgt_ctx[tid]
                    self.send("Network.enable", {"maxTotalBufferSize": 50_000_000,
                                                 "maxResourceBufferSize": 10_000_000}, sess)
            return
        m = msg.get("method")
        p = msg.get("params", {})
        s = msg.get("sessionId")
        if m == "Target.attachedToTarget":
            sess = p.get("sessionId")
            ti = p.get("targetInfo") or {}
            if ti.get("browserContextId"):
                self.sess_ctx[sess] = ti["browserContextId"]
                self.tgt_ctx[ti.get("targetId")] = ti["browserContextId"]
            self.send("Network.enable", {"maxTotalBufferSize": 50_000_000,
                                         "maxResourceBufferSize": 10_000_000}, sess)
        elif m == "Target.targetCreated":
            ti = p.get("targetInfo") or {}
            if ti.get("browserContextId"):
                self.tgt_ctx[ti.get("targetId")] = ti["browserContextId"]
        elif m == "Network.requestWillBeSent":
            rid = p["requestId"]
            r = p["request"]
            url = r.get("url", "")
            u = urlsplit(url)
            host = (u.hostname or "").lower()
            port = u.port or DEFAULT_PORTS.get(u.scheme.lower())
            self.reqs[(s, rid)] = {
                "ts": time.time(), "session": s, "target": msg.get("targetId") or "",
                "method": r.get("method"), "url": url, "host": host, "port": port,
                "path": ("/" + url.split("/", 3)[3]) if url.count("/") >= 3 else "/",
                "req_headers": dict(r.get("headers") or {}),
                "req_body": r.get("postData") or "",
                "resp_headers": {}, "resp_body": "", "status": 0, "mime": "",
            }
        elif m == "Network.requestWillBeSentExtraInfo":
            rec = self.reqs.get((s, p.get("requestId")))
            if rec:
                rec["req_headers"].update(p.get("headers") or {})
        elif m == "Network.responseReceived":
            rec = self.reqs.get((s, p.get("requestId")))
            if not rec:
                return
            resp = p.get("response", {})
            rec["status"] = resp.get("status", 0)
            rec["mime"] = resp.get("mimeType", "")
            rec["resp_headers"].update(resp.get("headers") or {})
            rec["type"] = p.get("type", "")
        elif m == "Network.responseReceivedExtraInfo":
            rec = self.reqs.get((s, p.get("requestId")))
            if rec:
                rec["resp_headers"].update(p.get("headers") or {})
                rec["status"] = p.get("statusCode", rec["status"])
        elif m == "Network.loadingFinished":
            rec = self.reqs.pop((s, p.get("requestId")), None)
            if rec:
                self.finalize(s, p["requestId"], rec)
        elif m == "Network.loadingFailed":
            self.reqs.pop((s, p.get("requestId")), None)

    def finalize(self, session, rid, rec):
        if not self.hosts.matches(rec["host"], rec.get("port")):
            return
        if self.exclude.search(rec["path"]):
            return
        if not self.keep_static:
            if rec.get("type") in ("Image", "Font", "Stylesheet", "Media"):
                return
            if SKIP_EXT.search(rec["path"]) or any(rec["mime"].startswith(x) for x in SKIP_MIME):
                return
        body, enc = "", False
        try:
            self.send("Network.getResponseBody", {"requestId": rid}, session)
            self.ws.settimeout(3.0)
            while True:
                msg = json.loads(self.ws.recv())
                if msg.get("id") and "body" in (msg.get("result") or {}):
                    body = msg["result"]["body"]
                    enc = msg["result"].get("base64Encoded", False)
                    break
                self.handle(msg)
        except Exception:
            pass
        finally:
            try:
                self.ws.settimeout(None)
            except Exception:
                pass
        if enc:
            try:
                body = base64.b64decode(body).decode("utf-8", "replace")
            except Exception:
                body = ""
        truncated = 0
        if len(body) > self.max_body:
            body = body[: self.max_body]
            truncated = 1
        req_body = rec["req_body"] or ""
        context = self.sess_ctx.get(session) or self.tgt_ctx.get(rec.get("target"))
        sub = _subject(rec["req_headers"])
        label = self.labels.get(context, "")
        row = {
            "ts": round(rec["ts"], 3), "session": session or "", "target": rec.get("target", ""),
            "context": context or "", "label": label, "sub": sub,
            "principal": label or (f"sub:{sub}" if sub else "anon"),
            "method": rec["method"], "url": rec["url"], "host": rec["host"],
            "path": rec["path"], "path_template": path_template(rec["path"]),
            "status": rec["status"], "mime": rec["mime"],
            "req_headers": json.dumps(mask(rec["req_headers"]), ensure_ascii=False),
            "req_body": req_body[: self.max_body],
            "resp_headers": json.dumps(mask(rec["resp_headers"]), ensure_ascii=False),
            "resp_body": body,
            "req_size": len(req_body), "resp_size": len(body), "truncated": truncated,
            "sha": hashlib.sha256((rec["method"] + rec["url"] + req_body + body).encode()).hexdigest()[:16],
        }
        self.w.put(row)

    def attach_all(self):
        try:
            for ti in self.call("Target.getTargets").get("targetInfos", []):
                if ti.get("browserContextId"):
                    self.tgt_ctx[ti["targetId"]] = ti["browserContextId"]
        except Exception:
            pass
        ctxs = sorted(set(self.tgt_ctx.values()))
        print("contexts: " + ", ".join(f"{c[:10]}={self.labels.get(c, 'unlabelled')}" for c in ctxs))
        for t in requests.get(self.ws_url_http + "/json/list", timeout=5).json():
            if t.get("type") == "page":
                self.send("Target.attachToTarget", {"targetId": t["id"], "flatten": True})

    def run(self, duration=None):
        self.send("Target.setDiscoverTargets", {"discover": True})
        self.send("Target.setAutoAttach", {"autoAttach": True, "waitForDebuggerOnStart": False, "flatten": True})
        self.attach_all()
        start = time.time()
        while not self.stop:
            if duration and time.time() - start > duration:
                break
            try:
                self.ws.settimeout(1.0)
                raw = self.ws.recv()
            except websocket.WebSocketTimeoutException:
                continue
            except Exception:
                break
            if not raw:
                continue
            try:
                self.handle(json.loads(raw))
            except Exception as e:
                print("handle error:", e, file=sys.stderr)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--hosts", required=True, help="comma-separated in-scope hosts")
    ap.add_argument("--out", required=True)
    ap.add_argument("--max-body", type=int, default=262144)
    ap.add_argument("--keep-static", action="store_true")
    ap.add_argument("--duration", type=int, default=0, help="exit after N seconds (smoke test)")
    a = ap.parse_args()

    hosts = HostFilter(a.hosts)
    if not hosts:
        sys.exit(f"refusing to run: --hosts {a.hosts!r} contains no usable host")
    if str(hosts) != a.hosts.strip():
        print(f"hosts normalised: {a.hosts!r} -> {hosts}")
    os.makedirs(a.out, exist_ok=True)

    lab_path = os.path.join(a.out, "contexts.json")
    labels = load_json(lab_path, {})
    if not labels:
        with open(lab_path, "w") as f:
            json.dump({"_help": "browserContextId -> A/B/C. Labels land on every flow."}, f, indent=2)
    rules = load_json(os.path.join(a.out, "rules.json"), {})
    exclude = re.compile(rules.get("exclude") or DEFAULT_EXCLUDE, re.I)
    keep_static = a.keep_static or bool(rules.get("keep_static"))

    db = os.path.join(a.out, "live.db")
    w = Writer(a.out, db)
    w.start()

    ver = requests.get(a.cdp + "/json/version", timeout=5).json()
    c = CDP(ver["webSocketDebuggerUrl"], hosts, w, a.max_body, keep_static, labels, exclude)
    c.ws_url_http = a.cdp

    def bye(*_):
        c.stop = True
    signal.signal(signal.SIGTERM, bye)
    signal.signal(signal.SIGINT, bye)

    print(f"attached: {ver['Browser']} · hosts={hosts.canonical} · out={a.out}")
    tries = 0
    while tries < 5 and not c.stop:
        try:
            c.run(a.duration or None)
            break
        except Exception as e:
            tries += 1
            print(f"reconnect {tries}/5 after: {e}", file=sys.stderr)
            time.sleep(2)
            try:
                c.ws = websocket.create_connection(ver["webSocketDebuggerUrl"], max_size=None, suppress_origin=True)
                c.attach_all()
            except Exception:
                continue

    w.q.put(None)
    time.sleep(0.3)
    print(f"flows written: {w.n}")


def _guard():
    """Never die with a traceback — the log is the only witness."""
    import sqlite3
    try:
        main()
    except SystemExit:
        raise
    except sqlite3.DatabaseError as e:
        sys.exit(f"livehunt capture: the db is unreadable ({e}) — move or reset it, then restart")
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"livehunt capture: {e or e.__class__.__name__}")


if __name__ == "__main__":
    _guard()
