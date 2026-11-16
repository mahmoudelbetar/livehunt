#!/usr/bin/env python3
"""Self-test: proves the analysis pipeline without a browser or a target.

  python3 tests/selftest.py            # exits 0 when everything holds

Covers: schema, classification, object graph, principal collision boost, query views,
test ledger dedupe, brief counters.
"""
import json, os, shutil, sqlite3, subprocess, sys, tempfile, uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from livehunt import capture  # noqa: E402

ROOT = tempfile.mkdtemp(prefix="livehunt-test-")
TARGET = "selftest"
D = os.path.join(ROOT, TARGET, "live")
PY = sys.executable
ENV = dict(os.environ, BOUNTY_ROOT=ROOT)
FAILS = []


def ok(name, cond, detail=""):
    print(("PASS " if cond else "FAIL ") + name + (f" — {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


def sh(*args, expect=0):
    p = subprocess.run([PY, "-m", "livehunt.cli", *args], env=ENV,
                       capture_output=True, text=True)
    if expect == 0 and p.returncode != 0:
        print(p.stdout, p.stderr)
    return p


def seed():
    os.makedirs(D, exist_ok=True)
    from livehunt import capture
    con = sqlite3.connect(os.path.join(D, "live.db"))
    con.executescript(capture.SCHEMA)
    cols = [r[1] for r in con.execute("pragma table_info(flows)")]
    for c in ("context", "label", "sub", "principal"):
        if c not in cols:
            con.execute(f"alter table flows add column {c} text")
    exp = str(uuid.uuid4())
    rows = [
        # method, path, status, label, principal, body, resp, ctype
        ("GET", f"/api/expenses/{exp}", 200, "A", "A", "", '{"id":"%s","amount":10}' % exp, "application/json"),
        ("GET", f"/api/expenses/{exp}", 200, "B", "B", "", '{"id":"%s","amount":10}' % exp, "application/json"),
        ("PUT", f"/api/expenses/{exp}", 200, "A", "A", '{"amount":99}', "{}", "application/json"),
        ("GET", "/api/search?q=juice", 200, "", "anon", "", '{"hits":[]}', "application/json"),
        ("POST", "/api/orders", 200, "A", "A", '{"amount":10,"coupon":"X"}', '{"ok":true}', "application/json"),
        ("POST", "/api/upload", 200, "A", "A", "x", "{}", "multipart/form-data; boundary=x"),
    ]
    for m, p, st, lab, pr, body, resp, ct in rows:
        con.execute(
            "insert into flows(ts,session,target,context,label,sub,principal,method,url,host,path,"
            "path_template,status,mime,req_headers,req_body,resp_headers,resp_body,req_size,resp_size,"
            "truncated,sha,class,score,tags) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (0, "", "", lab and "ctx-" + lab, lab, "sub" + lab, pr, m,
             f"https://app.test{p}", "app.test", p, capture.path_template(p), st, ct,
             json.dumps({"content-type": ct}), body, "{}", resp, len(body), len(resp), 0, "x", None, None, None))
    con.commit()
    con.close()
    return exp


def main():
    print("root:", ROOT)
    sh("init", TARGET, "--hosts", "app.test")
    ok("init creates dirs", os.path.isdir(D) and os.path.isfile(os.path.join(ROOT, TARGET, "stash.md")))

    exp = seed()
    p = sh("drain", TARGET)
    ok("drain runs", p.returncode == 0, p.stdout.strip().splitlines()[:1])

    con = sqlite3.connect(os.path.join(D, "live.db"))
    con.row_factory = sqlite3.Row

    cls = {(r["method"], r["path_template"]): r["class"] for r in con.execute("select method,path_template,class from flows")}
    ok("IDOR classified", cls.get(("GET", "/api/expenses/{uuid}")) == "IDOR", cls.get(("GET", "/api/expenses/{uuid}")))
    ok("IDOR-write classified", cls.get(("PUT", "/api/expenses/{uuid}")) == "IDOR-write", cls.get(("PUT", "/api/expenses/{uuid}")))
    ok("SQLi classified", cls.get(("GET", "/api/search")) == "SQLi", cls.get(("GET", "/api/search")))
    ok("BusinessLogic classified", cls.get(("POST", "/api/orders")) in ("BusinessLogic", "Race"), cls.get(("POST", "/api/orders")))
    ok("upload classified", cls.get(("POST", "/api/upload")) == "FileUpload", cls.get(("POST", "/api/upload")))

    objs = {r["kind"]: r for r in con.execute("select kind,count(*) n,count(distinct oid) d from objects group by kind")}
    ok("object graph built", objs.get("expense") and objs["expense"]["d"] == 1, dict(objs.get("expense") or {}))

    t = con.execute("select count(*) from touches where kind='expense' and oid=?", (exp,)).fetchone()[0]
    ok("touches recorded", t >= 3, t)

    h = con.execute("select id,score,status from hypotheses where class='IDOR' and method='GET'").fetchone()
    ok("IDOR hypothesis exists", h is not None)
    ok("collision boost applied", h and h["score"] >= 5, h["score"] if h else None)

    q = sh("query", TARGET, "collisions")
    ok("collisions view finds the object", exp in q.stdout, q.stdout.strip().splitlines()[-1:])

    if h:
        p1 = sh("test", TARGET, str(h["id"]), "dead", "--note", "selftest")
        p2 = sh("test", TARGET, str(h["id"]), "dead", "--note", "again", expect=1)
        ok("ledger records a test", p1.returncode == 0, p1.stdout.strip())
        ok("ledger refuses duplicate", p2.returncode != 0, p2.stderr.strip()[:60])

    b = sh("brief", TARGET)
    ok("brief shows dead", "1 dead" in b.stdout, [l for l in b.stdout.splitlines() if "ledger" in l][:1])
    ok("brief lists principals", "A=" in b.stdout and "B=" in b.stdout, [l for l in b.stdout.splitlines() if l.startswith("principals")][:1])

    e = sh("export", TARGET)
    ok("export bundle", e.returncode == 0 and os.path.exists(os.path.join(D, "bundle.json")))
    if os.path.exists(os.path.join(D, "bundle.json")):
        data = json.load(open(os.path.join(D, "bundle.json")))
        ok("bundle has no bodies", "flows" not in data and "hypotheses" in data)

    con.close()
    print()
    if FAILS:
        print(f"{len(FAILS)} FAILED: {FAILS}")
        return 1
    print("all checks passed")
    shutil.rmtree(ROOT, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
