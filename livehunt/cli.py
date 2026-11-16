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

from . import __version__  # single source of truth (livehunt/__init__.py)
from . import chrome as chrome_tool
from .hosts import canonical_hosts, parse_hosts, sample_url
HERE = os.path.dirname(os.path.abspath(__file__))
CAPTURE = os.path.join(HERE, "capture.py")
CLASSIFY = os.path.join(HERE, "classify.py")
QUERY = os.path.join(HERE, "query.py")
TEMPLATES = os.path.join(HERE, "templates")
ROOT = os.environ.get("BOUNTY_ROOT") or os.path.expanduser("~/bounty")
CDP = os.environ.get("LIVE_CDP", "http://127.0.0.1:9222")


def check_target(target):
    """Refuse anything that is not a plain folder name — never a path, never traversal."""
    t = (target or "").strip()
    if not t:
        sys.exit("target name is required — e.g. livehunt up shop")
    if t in (".", "..") or "/" in t or "\\" in t or ":" in t or os.path.isabs(t):
        sys.exit(f"invalid target name {target!r}: use a plain folder name "
                 f"(letters, digits, - _ .), not a path")
    if len(t) > 64:
        sys.exit(f"invalid target name {target!r}: keep it under 64 characters")
    return t


def tdir(target):
    return os.path.join(ROOT, check_target(target), "live")


def require_dir(target):
    d = tdir(target)
    if not os.path.isdir(d):
        sys.exit(f"no such target {target!r} (looked in {d})\n"
                 f"  create it: livehunt init {target} --hosts <host>")
    return d


def require_db(target):
    d = require_dir(target)
    db = os.path.join(d, "live.db")
    if not os.path.exists(db):
        sys.exit(f"{target} has no capture yet (no live.db in {d})\n"
                 f"  start it: livehunt up {target}")
    return d, db


def body_size(s):
    """--max-body: a non-negative byte count, sane upper bound."""
    try:
        n = int(s)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(f"not a number: {s!r}")
    if n < 0:
        raise argparse.ArgumentTypeError("must be >= 0")
    if n > 50_000_000:
        raise argparse.ArgumentTypeError("too large (max 50,000,000)")
    return n


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


def owns(pid, d):
    """A pid is only ours if its command line carries this exact out dir."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return d.encode() in f.read()
    except Exception:
        return False


def running(d):
    st = load(run_json(d), {})
    pid = st.get("pid")
    if not pid:
        return st, None
    return st, (pid if pid_alive(pid) and owns(pid, d) else None)


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


def warn_hosts(hosts, quiet=False):
    """A host without a dot is legal (intranet) but is usually a typo — say so once."""
    if quiet:
        return
    for h in (hosts or "").split(","):
        name = h.strip().split("://", 1)[-1].split("/", 1)[0].split(":", 1)[0]
        if name and "." not in name and name not in ("localhost", "*") and not name.startswith("["):
            print(f"warn      host {name!r} has no dot — typo? it will still be used")


def hint(a, msg):
    if getattr(a, "quiet", False):
        return
    print(f"\nnext → {msg}")


def top_todo(d):
    db = os.path.join(d, "live.db")
    if not os.path.exists(db):
        return None
    import sqlite3
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        return con.execute("select id,method,path_template from hypotheses where status='TODO' "
                           "order by score desc, count desc limit 1").fetchone()
    except Exception:
        return None
    finally:
        con.close()


def next_after_surf(a, d):
    h = top_todo(d)
    if h:
        hint(a, f"livehunt test {a.target} {h['id']} <confirmed|dead|blocked>  "
                f"({h['method']} {h['path_template']})")
    else:
        hint(a, f"surf one more flow, then: livehunt drain {a.target}")


def cmd_init(a):
    d = tdir(a.target)
    os.makedirs(d, exist_ok=True)
    if not os.path.exists(os.path.join(d, "contexts.json")):
        shutil.copy(os.path.join(TEMPLATES, "contexts.json"), os.path.join(d, "contexts.json"))
    if not os.path.exists(os.path.join(d, "rules.json")):
        shutil.copy(os.path.join(TEMPLATES, "rules.json"), os.path.join(d, "rules.json"))
    if a.hosts:
        hosts = canonical_hosts(a.hosts)
        if not hosts:
            sys.exit(f"no usable host in --hosts {a.hosts!r} (e.g. --hosts app.example.com)")
        warn_hosts(hosts, getattr(a, "quiet", False))
        save_atomic(run_json(d), {"pid": None, "hosts": hosts, "cdp": a.cdp or CDP,
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
    hint(a, f"livehunt chrome --print   then   livehunt up {a.target}")


def cmd_start(a):
    d = tdir(a.target)
    os.makedirs(d, exist_ok=True)
    st, pid = running(d)
    if pid and not a.force:
        sys.exit(f"already running (pid {pid}) — use stop, or --force")
    hosts = a.hosts or st.get("hosts")
    if not hosts:
        sys.exit("--hosts is required on first start (e.g. --hosts app.example.com)")
    hosts = canonical_hosts(hosts)
    if not hosts:
        sys.exit(f"no usable host in --hosts {a.hosts!r} (e.g. --hosts app.example.com)")
    warn_hosts(hosts, getattr(a, "quiet", False))
    cdp = a.cdp or st.get("cdp") or CDP
    say = (lambda *x: None) if getattr(a, "quiet", False) else print
    ok, msg = chrome_tool.ensure(cdp, say, url=sample_url(hosts))
    if not ok:
        sys.exit(msg)
    cmd = [sys.executable, "-u", CAPTURE, "--hosts", hosts, "--out", d, "--cdp", cdp]
    if a.max_body:
        cmd += ["--max-body", str(a.max_body)]
    if a.keep_static:
        cmd += ["--keep-static"]
    log = open(os.path.join(d, "capture.log"), "a")
    p = subprocess.Popen(cmd, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    save_atomic(run_json(d), {"pid": p.pid, "hosts": hosts, "cdp": cdp, "out": d,
                              "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                              "max_body": a.max_body or 262144})
    print(f"started pid {p.pid} · hosts={hosts} · out={d}")
    hint(a, f"surf the app, then: livehunt drain {a.target}")


def cmd_stop(a):
    d = require_dir(a.target)
    st, pid = running(d)
    if not pid:
        print("not running")
        hint(a, f"livehunt up {a.target} to start")
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
    hint(a, f"update stash.md + STATE.md, or: livehunt up {a.target}")


def cmd_resume(a):
    d = require_dir(a.target)
    st, pid = running(d)
    if pid:
        print(f"already running (pid {pid})")
        hint(a, f"livehunt drain {a.target}")
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
    if not os.path.isdir(d):
        print(f"target      {a.target}")
        print(f"dir         {d}   (does not exist)")
        hint(a, f"livehunt init {a.target} --hosts <host>")
        return
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
        if c.get("error"):
            print(f"db          unreadable — {c['error']}")
            print("            start fresh: livehunt reset " + a.target + " --yes")
            return
        print(f"flows       {c.get('flows')} (max id {c.get('max_id')}) · cursor {c.get('cursor')}"
              f" · {c.get('flows', 0) - c.get('cursor', 0)} unclassified")
        print(f"objects     {c.get('objects')} · hypotheses {c.get('hypotheses')}")
    print(f"disk        {size/1e6:.1f} MB")
    if os.path.exists(delta):
        print(f"last drain  {time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(delta)))}")
    unclassified = (c.get("flows", 0) - c.get("cursor", 0)) if c else 0
    if unclassified:
        hint(a, f"livehunt drain {a.target}  ({unclassified} new flow(s))")
    else:
        hint(a, f"livehunt brief {a.target}")


def cmd_brief(a):
    d = tdir(a.target)
    if not os.path.isdir(d):
        print(f"# brief — {a.target}")
        print(f"dir       {d}   (does not exist)")
        hint(a, f"livehunt init {a.target} --hosts <host>")
        return
    st, pid = running(d)
    c = db_counts(d)
    print(f"# brief — {a.target}")
    print(f"capture   {'running pid ' + str(pid) if pid else 'stopped'} · hosts {st.get('hosts') or '-'}")
    if c and not c.get("error"):
        print(f"flows     {c.get('flows')} (max {c.get('max_id')}) · cursor {c.get('cursor')}"
              f" · {c.get('flows', 0) - c.get('cursor', 0)} unclassified")
    db = os.path.join(d, "live.db")
    if not os.path.exists(db):
        print("no live.db — run: livehunt start " + a.target + " --hosts <host>")
        return
    import sqlite3
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    if c.get("error"):
        print(f"db        unreadable — {c['error']}")
        print(f"          start fresh: livehunt reset {a.target} --yes")
        con.close()
        return
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
        notes = con.execute("select count(*) from tests where result='note'").fetchone()[0]
        if notes:
            last = con.execute("select note from tests where result='note' order by id desc limit 1").fetchone()[0]
            print(f"notes     {notes} · last: {last[:70]}")
        once = con.execute("select count(*) from (select 1 from flows group by path_template having count(*)=1)").fetchone()[0]
        print(f"coverage  {con.execute('select count(distinct path_template) from flows').fetchone()[0]} endpoints seen"
              f" · {once} seen only once")
        h = con.execute("select id,method,path_template from hypotheses where status='TODO' "
                        "order by score desc, count desc limit 1").fetchone()
        if h:
            hint(a, f"livehunt test {a.target} {h['id']} <confirmed|dead|blocked>  ({h['method']} {h['path_template']})")
        else:
            hint(a, f"surf one more flow, then: livehunt drain {a.target}")
    finally:
        con.close()


def cmd_drain(a):
    d, _ = require_db(a.target)
    rc = subprocess.call([sys.executable, CLASSIFY, "--out", d] + (["--rebuild"] if a.rebuild else []))
    if rc:
        sys.exit(rc)
    next_after_surf(a, d)


def cmd_query(a):
    d, _ = require_db(a.target)
    if a.view == "object" and len(a.args) != 2:
        sys.exit("object needs two arguments: livehunt query <target> object <kind> <id>\n"
                 "  example: livehunt query shop object invoice 1001")
    if a.view != "object" and a.args:
        sys.exit(f"{a.view} takes no arguments (got: {' '.join(a.args)})")
    rc = subprocess.call([sys.executable, QUERY, "--out", d, a.view] + list(a.args))
    if rc:
        sys.exit(rc)
    h = top_todo(d)
    if h:
        hint(a, f"livehunt test {a.target} {h['id']} <result>  ({h['method']} {h['path_template']})")
    else:
        hint(a, f"livehunt query {a.target} writes")


def cmd_note(a):
    """Record a fact the classifier cannot hold (e.g. a confirmed finding with score < 3)."""
    d, _ = require_db(a.target)
    import sqlite3
    con = sqlite3.connect(os.path.join(d, "live.db"))
    con.execute("""create table if not exists tests(
        id integer primary key autoincrement, hyp_id integer, ts text,
        action text, result text, evidence integer, note text)""")
    text = " ".join(a.text)
    con.execute("insert into tests(hyp_id,ts,action,result,evidence,note) values(null,?,?,?,?,?)",
                (time.strftime('%Y-%m-%dT%H:%M:%S'), "note", "note", a.flow, text))
    con.commit()
    con.close()
    print(f"note recorded: {text[:140]}")
    hint(a, f"livehunt brief {a.target}")


def cmd_test(a):
    d, _ = require_db(a.target)
    import sqlite3
    con = sqlite3.connect(os.path.join(d, "live.db"))
    con.execute("""create table if not exists tests(
        id integer primary key autoincrement, hyp_id integer, ts text,
        action text, result text, evidence integer, note text)""")
    h = con.execute("select id,class,method,path_template,status from hypotheses where id=?", (a.hyp_id,)).fetchone()
    if not h:
        sys.exit(f"no hypothesis H{a.hyp_id}")
    action = a.action or f"{h[2]} {h[3]}"
    if a.flow and not con.execute("select 1 from flows where id=?", (a.flow,)).fetchone():
        con.close()
        sys.exit(f"no flow {a.flow} in this capture — the evidence id must be a real flow id "
                 f"(see: livehunt query {a.target} object <kind> <id>)")
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
    if a.result == "confirmed":
        hint(a, f"livehunt export {a.target}  (keep the flow id as evidence, then write it up)")
    else:
        hint(a, f"livehunt brief {a.target}  (next hypothesis)")


def cmd_doctor(a):
    d = tdir(a.target)
    if not os.path.isdir(d):
        print(f"note  no such target {a.target!r} (looked in {d}) — create it: "
              f"livehunt init {a.target} --hosts <host>")
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
        chk("chrome cdp", False,
            f"{cdp} closed — livehunt up launches Chrome for you ({str(e)[:30]})")
    chk("capture process", (pid is not None) or not st.get("pid"),
        f"pid {pid}" if pid else ("not started" if not st.get("pid") else "dead — run: livehunt resume " + a.target))
    chk("hosts filter", bool(parse_hosts(st.get("hosts") or "")), st.get("hosts") or "missing")
    if any(p == "*" for p, _ in parse_hosts(st.get("hosts") or "")):
        chk("hosts filter narrow", False, "wildcard * captures every host — narrow it")
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
    hint(a, f"livehunt up {a.target}" if ok else "docs/TROUBLESHOOTING.md")


def cmd_up(a):
    a.quiet = True
    d = tdir(a.target)
    if not os.path.isdir(d):
        if not getattr(a, "hosts", None):
            sys.exit(f"no such target {a.target!r} (looked in {d})\n"
                     f"  create it: livehunt init {a.target} --hosts <host>")
        a.quiet = False
        cmd_init(a)
        a.quiet = True
    st, pid = running(d)
    cdp = st.get("cdp") or CDP
    ok, msg = chrome_tool.ensure(cdp, print, url=sample_url(st.get("hosts")))
    if not msg.startswith("chrome     already open"):
        print(msg)
    cmd_doctor(a)
    st, pid = running(d)
    if not pid:
        if st.get("hosts"):
            cmd_resume(a)
        elif getattr(a, "hosts", None):
            cmd_start(a)
        else:
            print("not started — add --hosts on first run")
    a.quiet = True
    print()
    cmd_status(a)
    print()
    cmd_brief(a)
    a.quiet = False
    next_after_surf(a, d)


def cmd_reset(a):
    d = tdir(a.target)
    if not os.path.isdir(d):
        sys.exit(f"no such target dir: {d}")
    if not a.yes:
        sys.exit("refusing to reset without --yes "
                 "(this archives the capture state; add --purge to delete it)")
    st, pid = running(d)
    if pid:
        a.quiet = True
        cmd_stop(a)
        a.quiet = False
    hosts = load(run_json(d), {}).get("hosts")
    ts = time.strftime("%Y%m%d-%H%M%S")
    arch = os.path.join(d, "archive", ts)
    os.makedirs(arch, exist_ok=True)
    keep = {"contexts.json", "rules.json"}
    moved = []
    for f in sorted(os.listdir(d)):
        p = os.path.join(d, f)
        if f == "archive" or (f in keep and not a.all):
            continue
        shutil.move(p, os.path.join(arch, f))
        moved.append(f)
    if a.purge:
        shutil.rmtree(arch, ignore_errors=True)
        try:
            os.rmdir(os.path.join(d, "archive"))
        except OSError:
            pass
        print(f"purged {len(moved)} item(s) — target is empty")
    else:
        print(f"archived {len(moved)} item(s) -> {arch}")
    a.quiet = False
    hint(a, f"livehunt up {a.target}" + ("" if hosts else " --hosts <host>"))


def cmd_agent(a):
    """One compact block (or JSON) that an AI can ingest in a single call."""
    d = tdir(a.target)
    st, pid = running(d)
    c = db_counts(d)
    data = {"target": a.target, "version": __version__,
            "capture": {"running": bool(pid), "pid": pid, "hosts": st.get("hosts"),
                        "since": st.get("started")},
            "flows": c or {}, "principals": {}, "hypotheses": {"open": 0, "dead": 0, "blocked": 0,
                                                               "confirmed": 0, "top": []},
            "coverage": {}, "tests_recent": [], "next": None}
    db = os.path.join(d, "live.db")
    if os.path.exists(db):
        import sqlite3
        con = sqlite3.connect(db)
        con.row_factory = sqlite3.Row
        con.execute("""create table if not exists tests(
            id integer primary key autoincrement, hyp_id integer, ts text,
            action text, result text, evidence integer, note text)""")
        try:
            data["principals"] = {r["principal"]: r["n"] for r in con.execute(
                "select principal, count(*) n from flows group by principal order by n desc limit 6")}
            for r in con.execute("select status, count(*) n from hypotheses group by status"):
                k = {"TODO": "open", "FALSE-POSITIVE": "dead", "BLOCKED": "blocked",
                     "CONFIRMED": "confirmed"}.get(r["status"])
                if k:
                    data["hypotheses"][k] = r["n"]
            data["hypotheses"]["top"] = [dict(r) for r in con.execute(
                "select id,class,method,path_template,count,score,next_test from hypotheses "
                "where status='TODO' order by score desc, count desc limit 5")]
            data["coverage"] = {
                "endpoints": con.execute("select count(distinct path_template) from flows").fetchone()[0],
                "seen_once": con.execute("select count(*) from (select 1 from flows group by path_template having count(*)=1)").fetchone()[0],
                "objects": con.execute("select count(*) from objects").fetchone()[0]}
            data["tests_recent"] = [dict(r) for r in con.execute(
                "select hyp_id,ts,result,note from tests order by id desc limit 3")]
        except Exception as e:
            data["error"] = str(e)
        finally:
            con.close()
    top = data["hypotheses"]["top"]
    data["next"] = (f"livehunt test {a.target} {top[0]['id']} <confirmed|dead|blocked>"
                    if top else f"livehunt drain {a.target} or surf one more flow")
    if a.json:
        print(json.dumps(data, indent=2))
        return
    cap = "running" if data["capture"]["running"] else "stopped"
    f = data["flows"] or {}
    print(f"livehunt {__version__} · {a.target}")
    print(f"capture {cap} · hosts {data['capture']['hosts'] or '-'} · "
          f"flows {f.get('flows', 0)} (cursor {f.get('cursor', 0)}, {f.get('flows', 0) - f.get('cursor', 0)} unclassified)")
    print("principals " + (" · ".join(f"{k}={v}" for k, v in data["principals"].items()) or "-"))
    h = data["hypotheses"]
    print(f"hypotheses {h['open']} open · {h['dead']} dead · {h['blocked']} blocked · {h['confirmed']} confirmed")
    for r in top:
        print(f"  H{r['id']} {r['class']} {r['method']} {r['path_template']} s{r['score']} x{r['count']}")
        print(f"     {r['next_test']}")
    cov = data["coverage"] or {}
    print(f"coverage {cov.get('endpoints', 0)} endpoints · {cov.get('objects', 0)} objects")
    print(f"next → {data['next']}")


DEMO_FLOWS = [
    # method, path, status, label, principal, req_body, resp_body, ctype
    ("GET", "/api/products?search=gin", 200, "", "anon", "", '{"items":[]}', "application/json"),
    ("GET", "/api/search?q=%3Cscript%3E", 200, "", "anon", "", '{"hits":[]}', "application/json"),
    ("POST", "/rest/user/login", 200, "", "anon", '{"email":"a@shop.demo","password":"x"}',
     '{"authentication":{"token":"...","bid":6}}', "application/json"),
    ("GET", "/api/expenses/1001", 200, "A", "A", "", '{"id":"1001","amount":42}', "application/json"),
    ("GET", "/api/expenses/1001", 200, "B", "B", "", '{"id":"1001","amount":42}', "application/json"),
    ("GET", "/api/expenses/1001/approvals", 200, "A", "A", "", '{"approvals":[]}', "application/json"),
    ("GET", "/api/expenses/1001/approvals", 200, "B", "B", "", '{"approvals":[]}', "application/json"),
    ("PUT", "/api/expenses/1001", 200, "A", "A", '{"amount":9999}', '{"ok":true}', "application/json"),
    ("DELETE", "/api/expenses/1001/receipt", 200, "A", "A", "", '{"ok":true}', "application/json"),
    ("GET", "/api/invoices/77", 200, "A", "A", "", '{"id":"77","total":120}', "application/json"),
    ("GET", "/api/invoices/77", 200, "B", "B", "", '{"id":"77","total":120}', "application/json"),
    ("POST", "/api/orders", 200, "A", "A", '{"total":100,"coupon":"SAVE10"}', '{"order":"1"}', "application/json"),
    ("POST", "/api/orders", 200, "A", "A", '{"total":100,"coupon":"SAVE10"}', '{"order":"1"}', "application/json"),
    ("POST", "/api/users", 200, "A", "A", '{"email":"x@shop.demo","role":"admin"}', '{"ok":true}', "application/json"),
    ("GET", "/api/admin/users", 200, "A", "A", "", '{"users":[]}', "application/json"),
    ("POST", "/api/profile/avatar", 200, "A", "A", "binary", '{"ok":true}', "multipart/form-data; boundary=x"),
    ("GET", "/api/files?url=http://169.254.169.254/", 200, "A", "A", "", '{"content":"..."}', "application/json"),
    ("GET", "/api/health/config", 200, "", "anon", "", '{"debug":true}', "application/json"),
    ("POST", "/rest/user/reset-password", 200, "", "anon",
     '{"email":"a@shop.demo","answer":"x","new":"y","repeat":"y"}',
     '{"user":{"id":1,"totpSecret":"SEED","password":"hash"}}', "application/json"),
    ("GET", "/api/orders/1", 200, "A", "A", "", '{"id":"1","total":100}', "application/json"),
]


def cmd_demo(a):
    """Seed a synthetic target so anyone can see every output without a browser."""
    target = a.name or "demo"
    d = tdir(target)
    if os.path.isdir(d) and not a.force:
        sys.exit(f"{d} already exists — use --force to rebuild, or pick another name")
    if a.force and os.path.isdir(d):
        shutil.rmtree(d)
    os.makedirs(d, exist_ok=True)
    import sqlite3
    from . import capture as _cap
    con = sqlite3.connect(os.path.join(d, "live.db"))
    con.executescript(_cap.SCHEMA)
    cols = {r[1] for r in con.execute("pragma table_info(flows)")}
    for c in ("context", "label", "sub", "principal"):
        if c not in cols:
            con.execute(f"alter table flows add column {c} text")
    ts = time.time()
    for i, (m, p, st, lab, pr, body, resp, ct) in enumerate(DEMO_FLOWS):
        host = "shop.demo"
        con.execute(
            "insert into flows(ts,session,target,context,label,sub,principal,method,url,host,path,"
            "path_template,status,mime,req_headers,req_body,resp_headers,resp_body,req_size,resp_size,"
            "truncated,sha,class,score,tags) values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts + i, "demo", "", "ctx-" + lab if lab else "", lab, "sub" + lab if lab else "", pr, m,
             f"https://{host}{p}", host, p, _cap.path_template(p), st, ct,
             json.dumps({"content-type": ct}), body, "{}", resp, len(body), len(resp), 0,
             "demo%d" % i, None, None, None))
    con.commit()
    con.close()
    save_atomic(os.path.join(d, "contexts.json"),
                {"_help": "synthetic demo", "ctx-A": "A", "ctx-B": "B"})
    save_atomic(run_json(d), {"pid": None, "hosts": "shop.demo", "cdp": CDP,
                              "created": time.strftime("%Y-%m-%dT%H:%M:%S")})
    subprocess.call([sys.executable, CLASSIFY, "--out", d])
    print()
    print("This is a synthetic target — no browser, no traffic, no real app.")
    print("Read the delta above, then try:")
    print(f"  livehunt agent {target}")
    print(f"  livehunt query {target} collisions")
    print(f"  livehunt query {target} objects")
    print(f"  livehunt test {target} <H> dead --note \"demo\"")
    hint(a, f"livehunt agent {target}")


def cmd_export(a):
    d = require_dir(a.target)
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
    hint(a, "attach bundle.json to your report or issue — it contains no bodies")


def cmd_chrome(a):
    cdp = os.environ.get("LIVE_CDP", CDP)
    if not cdp.startswith(("http://", "https://")):
        sys.exit(f"bad CDP endpoint {cdp!r} — expected http://127.0.0.1:9222")
    binary = chrome_tool.find_binary(getattr(a, "binary", None))
    port = chrome_tool.cdp_port(cdp)
    v = chrome_tool.cdp_version(cdp)
    if v:
        print(f"chrome already open ({v.get('Browser', 'Chrome')}) on {cdp} — nothing to do.")
        return
    if not binary:
        print("Chrome not found. Install Chrome, or point at it explicitly:\n")
        print(f"  LIVE_CHROME='/path/to/chrome' livehunt chrome --launch")
        return
    cmd = chrome_tool.launch_cmd(binary, port)
    if getattr(a, "launch", False):
        ok, msg = chrome_tool.ensure(cdp, print, explicit=binary)
        print(msg)
        if not ok:
            sys.exit(1)
        print("\nChrome is open and will be reused on every run. Log in to the target there.")
        return
    print("Chrome is not running with the DevTools port. The tool can do it for you:\n")
    print("  livehunt chrome --launch\n")
    print("or run it yourself:\n")
    print("  " + " ".join(f'"{c}"' if " " in c else c for c in cmd))
    print(f"\nProfile: {chrome_tool.profile_dir(binary)} (separate from your daily Chrome, logins persist).")


def main():
    ap = argparse.ArgumentParser(
        prog="livehunt",
        description="passive live-capture toolkit for web hunters — records what Chrome sends, "
                    "never sends a request itself",
        epilog="start here: livehunt up <target> --hosts <host>   ·   docs: docs/REFERENCE.md")
    ap.add_argument("--version", action="version", version=f"livehunt {__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True, metavar="<command>")

    TARGET_HELP = "target name — also its folder name under $BOUNTY_ROOT (default ~/bounty)"
    HOSTS_HELP = ("comma-separated hosts to record, e.g. app.example.com,api.example.com — "
                  "host, host:port, full URL or *.domain; nothing else in the browser is stored")
    CDP_HELP = "Chrome DevTools endpoint (default http://127.0.0.1:9222, or $LIVE_CDP)"
    MAXBODY_HELP = "bytes kept per request/response body (default 262144; 0 = use the saved value)"
    KEEPSTATIC_HELP = "also record images, fonts, CSS and media — skipped by default"
    FORCE_START_HELP = "start even if a capture for this target is already running"

    def add(name, fn, help_text):
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("target", help=TARGET_HELP)
        p.set_defaults(fn=fn)
        return p

    p = add("init", cmd_init, "create the target folder, configs and stash/STATE stubs (hosts required)")
    p.add_argument("--hosts", help=HOSTS_HELP)
    p.add_argument("--cdp", help=CDP_HELP)

    p = add("start", cmd_start, "start capture for this target on the given hosts")
    p.add_argument("--hosts", help=HOSTS_HELP)
    p.add_argument("--cdp", help=CDP_HELP)
    p.add_argument("--max-body", type=body_size, default=0, help=MAXBODY_HELP)
    p.add_argument("--keep-static", action="store_true", help=KEEPSTATIC_HELP)
    p.add_argument("--force", action="store_true", help=FORCE_START_HELP)

    add("stop", cmd_stop, "stop capture for this target")
    add("resume", cmd_resume, "restart capture with the hosts saved on the last start")
    add("status", cmd_status, "one screen: capture, hosts, flows, cursor, objects, disk")
    add("brief", cmd_brief, "what to do next: principals, top hypotheses, ledger, coverage")

    p = add("drain", cmd_drain, "classify only the new flows; rewrites delta.md, context.md, chains.md")
    p.add_argument("--rebuild", action="store_true",
                   help="re-classify every flow and rebuild all hypotheses, objects and touches")

    p = add("query", cmd_query, "canned hunting questions, one table each")
    p.add_argument("view", choices=["objects", "collisions", "writes", "ids", "money",
                                    "principals", "once", "object"],
                   help="objects | collisions | writes | ids | money | principals | once | object")
    p.add_argument("args", nargs="*",
                   help="view arguments — only `object` needs them: <kind> <id>, e.g. invoice 1001")

    p = add("test", cmd_test, "record a test result; refuses to record the same action twice")
    p.add_argument("hyp_id", type=int, help="hypothesis number as shown by brief/agent, e.g. 4 for H4")
    p.add_argument("result", choices=["confirmed", "dead", "blocked", "partial", "observed"],
                   help="confirmed=bug reproduced · dead=not a bug (never retested) · "
                        "blocked=cannot test yet (needs a principal/object) · partial=signal only · "
                        "observed=seen in traffic, nothing sent")
    p.add_argument("--action", help="what the test did; defaults to the hypothesis method + path")
    p.add_argument("--flow", type=int, help="flow id that is the evidence (the N in ev_N)")
    p.add_argument("--note", help="one line of what you saw, e.g. '403 for a foreign id'")
    p.add_argument("--force", action="store_true",
                   help="re-record the same action — only if the target itself changed")

    p = add("note", cmd_note, "record a fact that has no hypothesis row (score < 3, out-of-band finding)")
    p.add_argument("text", nargs="+", help="the fact, in quotes — one sentence")
    p.add_argument("--flow", type=int, help="flow id that is the evidence, if any")

    add("doctor", cmd_doctor, "check the preconditions (python, deps, chrome cdp, hosts, db, disk)")
    add("export", cmd_export, "write bundle.json — counts, configs and top hypotheses, no bodies")

    p = sub.add_parser("demo", help="seed a synthetic target — every output with no browser",
                       description="seed a synthetic target — every output with no browser")
    p.add_argument("name", nargs="?", default="demo", help="demo target name (default: demo)")
    p.add_argument("--force", action="store_true", help="overwrite an existing demo target")
    p.set_defaults(fn=cmd_demo)

    p = add("agent", cmd_agent, "one compact state block an AI can read instead of the traffic")
    p.add_argument("--json", action="store_true", help="machine-readable JSON instead of the text block")

    p = add("reset", cmd_reset, "archive and clear this target's capture state (needs --yes)")
    p.add_argument("--yes", action="store_true", help="required confirmation — without it nothing happens")
    p.add_argument("--all", action="store_true", help="also archive contexts.json / rules.json")
    p.add_argument("--purge", action="store_true", help="delete instead of archiving")

    p = add("up", cmd_up, "session start: opens Chrome, doctor, start/resume, status, brief")
    p.add_argument("--hosts", help=HOSTS_HELP)
    p.add_argument("--cdp", help=CDP_HELP)
    p.add_argument("--max-body", type=body_size, default=0, help=MAXBODY_HELP)
    p.add_argument("--keep-static", action="store_true", help=KEEPSTATIC_HELP)
    p.add_argument("--force", action="store_true", help=FORCE_START_HELP)

    p = sub.add_parser("chrome", help="print or launch Chrome with the DevTools port",
                       description="print or launch Chrome with the DevTools port")
    p.add_argument("--print", action="store_true", help="print the command (default)")
    p.add_argument("--launch", action="store_true", help="open Chrome with the DevTools port")
    p.add_argument("--binary", help="explicit Chrome path (else auto-detected)")
    p.set_defaults(fn=cmd_chrome)

    sub.add_parser("version", help="print the version").set_defaults(
        fn=lambda a: print(f"livehunt {__version__}"))

    a = ap.parse_args()
    try:
        a.fn(a)
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except SystemExit:
        raise
    except FileNotFoundError as e:
        sys.exit(f"missing file: {getattr(e, 'filename', None) or e}\n"
                 f"  run: livehunt doctor {getattr(a, 'target', '')}")
    except PermissionError as e:
        sys.exit(f"permission denied: {getattr(e, 'filename', None) or e}")
    except OSError as e:
        if "database is locked" in str(e).lower():
            sys.exit(f"database is locked — two captures on one folder? "
                     f"run: livehunt stop {getattr(a, 'target', '')} and keep one")
        sys.exit(f"i/o error: {e}")
    except Exception as e:  # noqa: BLE001 — last resort: never show a traceback
        import sqlite3
        msg = str(e) or e.__class__.__name__
        if isinstance(e, sqlite3.Error):
            low = msg.lower()
            if "locked" in low:
                sys.exit(f"database is locked — another capture is writing this folder\n"
                         f"  run: livehunt stop {getattr(a, 'target', '')} and keep one")
            if "no such table" in low or "no such column" in low:
                sys.exit(f"the capture db is from an older livehunt ({msg})\n"
                         f"  rebuild it: livehunt drain {getattr(a, 'target', '')} --rebuild")
            sys.exit(f"database error: {msg}\n"
                     f"  start fresh: livehunt reset {getattr(a, 'target', '')} --yes")
        if os.environ.get("LIVEHUNT_DEBUG"):
            raise
        sys.exit(f"error: {msg}\n"
                 f"  run: livehunt doctor {getattr(a, 'target', '')}   "
                 f"(LIVEHUNT_DEBUG=1 for a traceback)")


if __name__ == "__main__":
    main()
