#!/usr/bin/env python3
"""livehunt — passive live-capture toolkit for web hunters.

One entrypoint. Everything per target lives under <BOUNTY_ROOT>/<target>/live/.

  livehunt init   <target> --hosts h1,h2      # create the target folder + configs
  livehunt up     <target>                    # doctor -> start/resume -> status -> brief
  livehunt start  <target> [--hosts h1,h2]
  livehunt stop   <target>
  livehunt resume <target>
  livehunt status <target>
  livehunt brief  <target>
  livehunt drain  <target> [--rebuild]
  livehunt query  <target> <view> [args...]
  livehunt test   <target> <hyp_id> <result> [--flow N] [--note ...]
  livehunt doctor <target>
  livehunt export <target>
  livehunt chrome [--launch]                  # how to start Chrome with CDP
  livehunt version

Env: BOUNTY_ROOT (default ~/bounty), LIVE_CDP (default http://127.0.0.1:9222)
"""
import argparse, json, os, shutil, signal, subprocess, sys, time

__version__ = "0.1.0"
HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "capture.py")
CLASSIFY = os.path.join(HERE, "classify.py")
QUERY = os.path.join(HERE, "query.py")
TEMPLATES = os.path.join(HERE, "templates")
ROOT = os.environ.get("BOUNTY_ROOT") or os.path.expanduser("~/bounty")
CDP = os.environ.get("LIVE_CDP", "http://127.0.0.1:9222")


def tdir(target):
    return os.path.join(ROOT, target, "live")


def run_json(d):
    return os.path.join(d, "run.json")


def load(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def pid_alive(pid):
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"capture.py" in f.read()
    except Exception:
        # non-Linux (macOS/Windows): fall back to a liveness probe
        try:
            os.kill(pid, 0)
            return True
        except Exception:
            return False


def running(d):
    st = load(run_json(d), {})
    pid = st.get("pid")
    return st, (pid if pid and pid_alive(pid) else None)


def db_counts(d):
    db = os.path.join(d, "live.db")
    if not os.path.exists(db):
        return {}
    import sqlite3
    c = sqlite3.connect(db)
    try:
        n = c.execute("select count(*) from flows").fetchone()[0]
        mx = c.execute("select max(id) from flows").fetchone()[0] or 0
        cur = c.execute("select v from meta where k='classified_upto'").fetchone()
        hyp = c.execute("select count(*) from hypotheses").fetchone()[0]
        obj = c.execute("select count(*) from objects").fetchone()[0]
        return {"flows": n, "max_id": mx, "cursor": int(cur[0]) if cur else 0,
                "hypotheses": hyp, "objects": obj}
    except Exception as e:
        return {"error": str(e)}
    finally:
        c.close()


def cmd_init(a):
    d = tdir(a.target)
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(os.path.join(d, "contexts.json")):
        shutil.copy(os.path.join(TEMPLATES, "contexts.json"), os.path.join(d, "contexts.json"))
    if not os.path.exists(os.path.join(d, "rules.json")):
        shutil.copy(os.path.join(TEMPLATES, "rules.json"), os.path.join(d, "rules.json"))
    if a.hosts:
        save_atomic(run_json(d), {"pid": None, "hosts": a.hosts, "cdp": a.cdp or CDP,
                                  "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    tdir_target = os.path.join(ROOT, a.target)
    for name, body in (
        ("stash.md", f"# Stash — {a.target}\n\nLast exchange id: 0\n\n## Coverage\n| Area | Examined? | Result lives in | Status |\n|---|---|---|---|\n"),
        ("STATE.md", f"# STATE — {a.target}\n\nScope:\nAccounts:\nNext step:\n"),
    ):
        p = os.path.join(tdir_target, name)
        if not os.path.exists(p):
            with open(p, "w") as f:
                f.write(body)
    print(f"initialised {d}")
    print(f"next: livehunt chrome --print   then   livehunt up {a.target}")


def cmd_start(a):
    d = tdir(a.target)
    os.makedirs(d, exist_ok=True)
    st, pid = running(d)
    if pid and not a.force:
        sys.exit(f"already running (pid {pid}) — use stop, or --force")
    hosts = a.hosts or st.get("hosts")
    if not hosts:
        sys.exit("--hosts is required on first start (e.g. --hosts app.example.com)")
    cdp = a.cdp or st.get("cdp") or CDP
    cmd = [sys.executable, "-u", CAPTURE, "--hosts", hosts, "--out", d, "--cdp", cdp]
    if a.max_body:
        cmd += ["--max-body", str(a.max_body)]
    if a.keep_static:
        cmd += ["--keep-static"]
    log = open(os.path.join(d, "capture.log"), "a")
    p = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    save_atomic(run_json(d), {"pid": p.pid, "hosts": hosts, "cdp": cdp,
                              "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "max_body": a.max_body or 262144})
    print(f"started pid {p.pid} · hosts={hosts} · out={d}")


def cmd_stop(a):
    d = tdir(a.target)
    st, pid = running(d)
    if not pid:
        print("not running")
        return
    os.kill(pid, signal.SIGTERM)
    for _ in range(20):
        if not pid_alive(pid):
            break
        time.sleep(0.25)
    if pid_alive(pid):
        os.kill(pid, signal.SIGKILL)
        print(f"killed pid {pid}")
    else:
        print(f"stopped pid {pid}")
    st["pid"] = None
    st["stopped"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_atomic(run_json(d), st)


def cmd_resume(a):
    d = tdir(a.target)
    st, pid = running(d)
    if pid:
        print(f"already running (pid {pid})")
        return
    st = load(run_json(d), {})
    if not st.get("hosts"):
        sys.exit("no run.json — use start --hosts")
    a.hosts = st["hosts"]
    a.cdp = st.get("cdp")
    a.max_body = st.get("max_body", 0)
    a.keep_static = False
    a.force = False
    cmd_start(a)


def cmd_status(a):
    d = tdir(a.target)
    st, pid = running(d)
    c = db_counts(d)
    size = 0
    for f in ("live.db", "flows.jsonl"):
        p = os.path.join(d, f)
        if os.path.exists(p):
            size += os.path.getsize(p)
    delta = os.path.join(d, "delta.md")
    print(f"target      {a.target}")
    print(f"dir         {d}")
    print(f"capture     {'running pid ' + str(pid) if pid else 'stopped'}"
          + (f" · since {st.get('started')}" if pid else ""))
    print(f"hosts       {st.get('hosts') or '-'}")
    if c:
        print(f"flows       {c.get('flows')} (max id {c.get('max_id')}) · cursor {c.get('cursor')}"
              f" · {c.get('flows', 0) - c.get('cursor', 0)} unclassified")
        print(f"objects     {c.get('objects')} · hypotheses {c.get('hypotheses')}")
    print(f"disk        {size/1e6:.1f} MB")
    if os.path.exists(delta):
        print(f"last drain  {time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(delta)))}")


def cmd_brief(a):
    d = tdir(a.target)
    st, pid = running(d)
    c = db_counts(d)
    print(f"# brief — {a.target}")
    print(f"capture   {'running pid ' + str(pid) if pid else 'stopped'} · hosts {st.get('hosts') or '-'}")
    if c:
        print(f"flows     {c.get('flows')} (max {c.get('max_id')}) · cursor {c.get('cursor')}"
              f" · {c.get('flows', 0) - c.get('cursor', 0)} unclassified")
    db = os.path.join(d, "live.db")
    if not os.path.exists(db):
        print("no live.db — run: livehunt start " + a.target + " --hosts <host>")
        return
    import sqlite3
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    con.execute("""create table if not exists tests(
        id integer primary key autoincrement, hyp_id integer, ts text,
        action text, result text, evidence integer, note text)""")
    try:
        pr = list(con.execute("select principal, count(*) n from flows group by principal order by n desc limit 4"))
        print("principals " + " · ".join(f"{r['principal']}={r['n']}" for r in pr))
        if len([r for r in pr if str(r["principal"]).startswith(("A", "B", "C"))]) < 2:
            print("           ! only one labelled principal — horizontal tests (IDOR/BOLA) prove nothing yet")
        todo = list(con.execute("select id,class,method,path_template,count,score,next_test from hypotheses "
                                "where status='TODO' order by score desc, count desc limit 5"))
        if todo:
            print(f"todo      {con.execute('select count(*) from hypotheses where status=\"TODO\"').fetchone()[0]} open")
            for r in todo:
                print(f"  H{r['id']} {r['class']} {r['method']} {r['path_template']} (x{r['count']}, s{r['score']})")
                print(f"     test: {r['next_test']}")
        dead = con.execute("select count(*) from hypotheses where status='FALSE-POSITIVE'").fetchone()[0]
        conf = con.execute("select count(*) from hypotheses where status='CONFIRMED'").fetchone()[0]
        blk = con.execute("select count(*) from hypotheses where status='BLOCKED'").fetchone()[0]
        print(f"ledger    {conf} confirmed · {dead} dead (never retest) · {blk} blocked (needs a principal/object) · "
              f"{con.execute('select count(*) from tests').fetchone()[0]} test(s) recorded")
        once = con.execute("select count(*) from (select 1 from flows group by path_template having count(*)=1)").fetchone()[0]
        print(f"coverage  {con.execute('select count(distinct path_template) from flows').fetchone()[0]} endpoints seen"
              f" · {once} seen only once")
    finally:
        con.close()


def cmd_drain(a):
    d = tdir(a.target)
    if not os.path.exists(os.path.join(d, "live.db")):
        sys.exit("no live.db — start capture first")
    subprocess.call([sys.executable, CLASSIFY, "--out", d] + (["--rebuild"] if a.rebuild else []))


def cmd_query(a):
    d = tdir(a.target)
    subprocess.call([sys.executable, QUERY, "--out", d, a.view] + a.args)


def cmd_test(a):
    d = tdir(a.target)
    db = os.path.join(d, "live.db")
    if not os.path.exists(db):
        sys.exit("no live.db")
    import sqlite3
    con = sqlite3.connect(db)
    con.execute("""create table if not exists tests(
        id integer primary key autoincrement, hyp_id integer, ts text,
        action text, result text, evidence integer, note text)""")
    h = con.execute("select id,class,method,path_template,status from hypotheses where id=?", (a.hyp_id,)).fetchone()
    if not h:
        sys.exit(f"no hypothesis H{a.hyp_id}")
    action = a.action or f"{h[2]} {h[3]}"
    dup = con.execute("select id,ts,result from tests where hyp_id=? and action=?",
                      (a.hyp_id, action)).fetchone()
    if dup and not a.force:
        sys.exit(f"already tested H{a.hyp_id} with the same action on {dup[1]} -> {dup[2]} "
                 f"(use --force only if the target changed)")
    con.execute("insert into tests(hyp_id,ts,action,result,evidence,note) values(?,?,?,?,?,?)",
                (a.hyp_id, time.strftime("%Y-%m-%dT%H:%M:%S"), action, a.result, a.flow, a.note))
    status = {"confirmed": "CONFIRMED", "dead": "FALSE-POSITIVE",
              "blocked": "BLOCKED"}.get(a.result)
    if status:
        con.execute("update hypotheses set status=? where id=?", (status, a.hyp_id))
    con.commit()
    print(f"recorded H{a.hyp_id} -> {a.result} ({action})")


def cmd_doctor(a):
    d = tdir(a.target)
    ok = True

    def chk(name, cond, detail=""):
        nonlocal ok
        ok = ok and bool(cond)
        print(f"{'OK  ' if cond else 'FAIL'} {name}{(' — ' + detail) if detail else ''}")

    chk("python", sys.version_info >= (3, 9), sys.version.split()[0])
    try:
        import requests, websocket  # noqa
        chk("deps", True, "requests + websocket-client")
    except Exception as e:
        chk("deps", False, f"pip install requests websocket-client ({e})")
    chk("live dir", os.path.isdir(d), d)
    chk("root writable", os.access(ROOT, os.W_OK), ROOT)
    st, pid = running(d)
    cdp = st.get("cdp") or CDP
    try:
        import requests
        v = requests.get(cdp + "/json/version", timeout=3).json()
        chk("chrome cdp", True, v.get("Browser", ""))
    except Exception as e:
        chk("chrome cdp", False, f"{cdp} — {str(e)[:40]} (run: livehunt chrome)")
    chk("capture process", (pid is not None) or not st.get("pid"),
        f"pid {pid}" if pid else ("not started" if not st.get("pid") else "dead — run: livehunt resume " + a.target))
    chk("hosts filter", bool(st.get("hosts")), st.get("hosts") or "missing")
    labels = load(os.path.join(d, "contexts.json"), {})
    unlabelled = [k for k in labels if not k.startswith("_")]
    chk("contexts labelled", True, f"{len(unlabelled)} label(s)")
    c = db_counts(d)
    if c:
        chk("db readable", "error" not in c, c.get("error", f"{c.get('flows', 0)} flows"))
        chk("cursor sane", c.get("cursor", 0) <= c.get("max_id", 0),
            f"cursor {c.get('cursor')} / max {c.get('max_id')}")
    else:
        chk("db", True, "no capture yet")
    free = shutil.disk_usage(d if os.path.isdir(d) else ROOT).free / 1e9
    chk("disk free", free > 1, f"{free:.1f} GB")
    print("doctor:", "clean" if ok else "issues above — see docs/TROUBLESHOOTING.md")


def cmd_up(a):
    cmd_doctor(a)
    d = tdir(a.target)
    st, pid = running(d)
    if not pid:
        if st.get("hosts"):
            cmd_resume(a)
        elif getattr(a, "hosts", None):
            cmd_start(a)
        else:
            print("not started — add --hosts on first run")
    print()
    cmd_status(a)
    print()
    cmd_brief(a)


def cmd_export(a):
    d = tdir(a.target)
    c = db_counts(d)
    bundle = {
        "target": a.target, "exported": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "version": __version__, "counts": c,
        "contexts": load(os.path.join(d, "contexts.json"), {}),
        "rules": load(os.path.join(d, "rules.json"), {}),
        "run": load(run_json(d), {}),
        "hypotheses": [],
    }
    db = os.path.join(d, "live.db")
    if os.path.exists(db):
        import sqlite3
        con = sqlite3.connect(db)
        try:
            bundle["hypotheses"] = [
                dict(zip(("class", "method", "path", "count", "score", "status"), r))
                for r in con.execute("select class,method,path_template,count,score,status "
                                     "from hypotheses order by score desc, count desc limit 100")]
        except Exception:
            pass
        con.close()
    out = os.path.join(d, "bundle.json")
    save_atomic(out, bundle)
    print(f"exported {out} ({os.path.getsize(out)} B, no bodies)")


CHROME = {
    "linux": 'google-chrome --remote-debugging-port=9222 --user-data-dir="$HOME/.livehunt-chrome"',
    "darwin": '"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" '
              '--remote-debugging-port=9222 --user-data-dir="$HOME/.livehunt-chrome"',
    "win32": '"C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
             '--remote-debugging-port=9222 --user-data-dir="%USERPROFILE%\\.livehunt-chrome"',
}


def cmd_chrome(a):
    key = "win32" if sys.platform.startswith("win") else ("darwin" if sys.platform == "darwin" else "linux")
    cmd = CHROME[key]
    print("Start Chrome with the DevTools port open (use a separate profile, not your main one):\n")
    print("  " + cmd)
    print("\nThen open your target in that window and run: livehunt up <target>")
    print("Chrome 136+ refuses the port on the default profile, so the --user-data-dir is required.")
    if a.launch:
        subprocess.Popen(cmd, shell=True, start_new_session=True)
        print("\nlaunched.")


def main():
    ap = argparse.ArgumentParser(prog="livehunt", description="passive live-capture toolkit for web hunters")
    ap.add_argument("--version", action="version", version=f"livehunt {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn):
        p = sub.add_parser(name)
        p.add_argument("target")
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init)
    p.add_argument("--hosts")
    p.add_argument("--cdp")
    p = add("start", cmd_start)
    p.add_argument("--hosts")
    p.add_argument("--cdp")
    p.add_argument("--max-body", type=int, default=0)
    p.add_argument("--keep-static", action="store_true")
    p.add_argument("--force", action="store_true")
    add("stop", cmd_stop)
    add("resume", cmd_resume)
    add("status", cmd_status)
    add("brief", cmd_brief)
    p = add("drain", cmd_drain)
    p.add_argument("--rebuild", action="store_true")
    p = add("query", cmd_query)
    p.add_argument("view")
    p.add_argument("args", nargs="*")
    p = add("test", cmd_test)
    p.add_argument("hyp_id", type=int)
    p.add_argument("result", choices=["confirmed", "dead", "blocked", "partial", "observed"])
    p.add_argument("--action")
    p.add_argument("--flow", type=int)
    p.add_argument("--note")
    p.add_argument("--force", action="store_true")
    add("doctor", cmd_doctor)
    add("export", cmd_export)
    p = add("up", cmd_up)
    p.add_argument("--hosts")
    p.add_argument("--cdp")
    p.add_argument("--max-body", type=int, default=0)
    p.add_argument("--keep-static", action="store_true")
    p.add_argument("--force", action="store_true")
    p = sub.add_parser("chrome")
    p.add_argument("--print", action="store_true", help="print the command (default)")
    p.add_argument("--launch", action="store_true")
    p.set_defaults(fn=cmd_chrome)
    sub.add_parser("version").set_defaults(fn=lambda a: print(f"livehunt {__version__}"))

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
