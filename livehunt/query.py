#!/usr/bin/env python3
"""Canned questions against live.db — the hunting views, not raw dumps.

Usage:
  query.py --out <target>/live objects
  query.py --out <target>/live collisions
  query.py --out <target>/live writes
  query.py --out <target>/live ids
  query.py --out <target>/live money
  query.py --out <target>/live principals
  query.py --out <target>/live once
  query.py --out <target>/live object <kind> <oid>
"""
import argparse, os, sqlite3, sys

Q = {
    "objects": """select t.kind, count(distinct t.oid) ids, count(distinct f.path_template) endpoints,
                         count(*) touches, group_concat(distinct t.op) ops
                  from touches t join flows f on f.id=t.flow_id
                  group by t.kind order by endpoints desc, ids desc limit 20""",
    "collisions": """select t.kind, t.oid, group_concat(distinct f.label) labels,
                            count(distinct f.path_template) endpoints
                     from touches t join flows f on f.id=t.flow_id
                     where f.label<>'' group by t.kind,t.oid
                     having count(distinct f.label)>1 order by endpoints desc limit 30""",
    "writes": """select t.kind, t.oid, count(distinct case when t.op='write' then f.path_template end) w,
                        count(distinct case when t.op='read' then f.path_template end) r
                 from touches t join flows f on f.id=t.flow_id
                 group by t.kind,t.oid having w>0 order by w desc, r asc limit 30""",
    "ids": """select method||' '||path_template req, class, count(*) n, group_concat(distinct principal) who
              from flows where path_template like '%{uuid}%' or path_template like '%{id}%'
              group by req, class order by n desc limit 30""",
    "money": """select method||' '||path_template req, class, count(*) n
                from flows where method in ('POST','PUT','PATCH','DELETE')
                  and class in ('BusinessLogic','Race','MassAssignment','IDOR-write','CSRF')
                group by req, class order by n desc limit 25""",
    "principals": """select principal, count(*) flows, count(distinct path_template) endpoints,
                            min(datetime(ts,'unixepoch')) first, max(datetime(ts,'unixepoch')) last
                     from flows group by principal order by flows desc""",
    "once": """select method||' '||path_template req, class, count(*) n
               from flows group by req, class having n=1 order by req limit 40""",
}
HEAD = {
    "objects": ["kind", "ids", "endpoints", "touches", "ops"],
    "collisions": ["kind", "id", "labels", "endpoints"],
    "writes": ["kind", "id", "write_eps", "read_eps"],
    "ids": ["request", "class", "n", "principals"],
    "money": ["request", "class", "n"],
    "principals": ["principal", "flows", "endpoints", "first", "last"],
    "once": ["request", "class", "n"],
}


def show(cur, name):
    print(" | ".join(HEAD[name]))
    for r in cur:
        print(" | ".join(str(x) for x in r))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("what", choices=list(Q) + ["object"])
    ap.add_argument("args", nargs="*")
    a = ap.parse_args()
    db = os.path.join(a.out, "live.db")
    if not os.path.exists(db):
        sys.exit("no live.db")
    c = sqlite3.connect(db)
    if a.what == "object":
        if len(a.args) != 2:
            sys.exit("object <kind> <oid>")
        kind, oid = a.args
        print(f"# {kind} {oid}")
        for r in c.execute("""select f.id, f.method||' '||f.path_template, f.status, f.principal,
                                     f.class, t.op, t.source
                              from touches t join flows f on f.id=t.flow_id
                              where t.kind=? and t.oid=? order by f.id""", (kind, oid)):
            print(" | ".join(str(x) for x in r))
        return
    show(c.execute(Q[a.what]), a.what)


def _guard():
    """One friendly line instead of a traceback, whatever goes wrong."""
    import sqlite3
    try:
        main()
    except SystemExit:
        raise
    except sqlite3.DatabaseError as e:
        sys.exit(f"livehunt: the capture db is unreadable ({e}) — start fresh: "
                 f"livehunt reset <target> --yes")
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except Exception:
            pass
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as e:  # noqa: BLE001
        sys.exit(f"livehunt: {e or e.__class__.__name__}")


if __name__ == "__main__":
    _guard()
