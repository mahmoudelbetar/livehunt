#!/usr/bin/env python3
"""Classify flows, build the object graph, and grow hypotheses / context / chains.

Reads only rows with id > last classified. Prints a compact delta, never bodies.

Usage: classify.py --out /root/bounty/navan/live [--rebuild]
"""
import argparse, json, os, re, sqlite3, sys, time

ID_RE = re.compile(r"(^|/)(\d{1,12}|[0-9a-fA-F-]{16,})(/|$)")
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
HEX_RE = re.compile(r"^[0-9a-fA-F]{16,64}$")
NUM_RE = re.compile(r"^\d{1,12}$")
IDKEY_RE = re.compile(r"([a-z0-9]+)[_-]?id$", re.I)

LOGIC_KEYS = re.compile(r"price|amount|total|qty|quantity|discount|coupon|credit|balance|points|limit|fee|tip", re.I)
MASS_KEYS = re.compile(r"^(role|roles|is_?admin|admin|verified|active|isactive|owner|ownerid|userid|tenantid|companyid|orgid)$", re.I)
SQL_PARAMS = re.compile(r"^(q|query|search|filter|sort|order|orderby|where|id)$", re.I)
XSS_PARAMS = re.compile(r"^(q|name|title|comment|message|description|note|feedback|subject|bio|username)$", re.I)
SSRF_PARAMS = re.compile(r"^(url|uri|link|callback|webhook|redirect|next|returnurl|return_url|imageurl|image_url|fetch|proxy|dest|target|src)$", re.I)
UPLOAD_PATH = re.compile(r"upload|avatar|profile/image|attachment|import|file", re.I)
AUTH_PATH = re.compile(r"login|logout|token|session|password|reset|otp|2fa|mfa|register|signup|verify|captcha|security-question", re.I)
INFO_PATH = re.compile(r"config|version|debug|actuator|swagger|openapi|graphql|/env|/metrics|heapdump", re.I)
ADMIN_PATH = re.compile(r"(^|/)(admin|internal|manage|backoffice|staff)[a-z]*", re.I)
RACE_KEYS = re.compile(r"qty|quantity|amount|coupon|redeem|claim|transfer|withdraw|vote|like", re.I)
NOISE_PATH = re.compile(r"^/cdn-cgi/|^/__cf|/sentry|\.well-known|favicon|/rum\b", re.I)

NEXT_TEST = {
    "IDOR": "replay the same request as account B; then change the id — compare body, not status",
    "IDOR-write": "replay as account B with a real body; compare the object's state after, not the status",
    "BAC": "replay with the lowest-privilege token and with no token; diff status + body",
    "BusinessLogic": "set the money field to 0 / -1 / 999999 and compare the server-computed total",
    "MassAssignment": "add role/isAdmin/verified/ownerId to the body, then re-read the object",
    "SQLi": "send the parameter=' and parameter='' ; compare status, length, error text",
    "XSS": "send <img src=x onerror=alert(1)> and re-read the page that renders it",
    "SSRF": "point the url-like parameter at an interactsh URL; poll for the callback",
    "FileUpload": "upload .svg and .php with image/* mime; request the stored file",
    "CSRF": "replay from a cross-site form with no Origin/Referer and a cookie session",
    "Auth": "test enumeration (valid vs invalid input) and token reuse/expiry",
    "Race": "fire 10 identical concurrent requests; compare with the single-shot result",
    "InfoLeak": "request the same path with no auth; diff the body",
}
WEIGHT = {"IDOR": 3, "IDOR-write": 4, "BAC": 4, "BusinessLogic": 3, "MassAssignment": 3,
          "SQLi": 2, "XSS": 2, "SSRF": 3, "FileUpload": 2, "CSRF": 1, "Auth": 2,
          "Race": 3, "InfoLeak": 1}

SCHEMA = """
create table if not exists objects(
  kind text, oid text, first_flow integer, last_flow integer, count integer default 1,
  primary key(kind,oid)
);
create table if not exists touches(
  flow_id integer, kind text, oid text, op text, source text,
  primary key(flow_id,kind,oid,op,source)
);
create index if not exists idx_touches_obj on touches(kind,oid);
create index if not exists idx_touches_flow on touches(flow_id);
create table if not exists tests(
  id integer primary key autoincrement,
  hyp_id integer, ts text, action text, result text, evidence integer, note text
);
"""


def parse_headers(h):
    try:
        return {k.lower(): v for k, v in json.loads(h or "{}").items()}
    except Exception:
        return {}


def body_keys(body, ctype):
    keys = set()
    if not body:
        return keys
    if "json" in (ctype or ""):
        try:
            d = json.loads(body)
            if isinstance(d, dict):
                keys |= set(d.keys())
                for v in d.values():
                    if isinstance(v, dict):
                        keys |= set(v.keys())
        except Exception:
            pass
    elif "x-www-form-urlencoded" in (ctype or ""):
        for part in body.split("&"):
            if "=" in part:
                keys.add(part.split("=", 1)[0])
    return keys


def kind_of(key, fallback="obj"):
    k = (key or "").lower().replace("-", "").replace("_", "")
    if k in ("uuid", "guid", "id"):
        return fallback
    for suf in ("uuid", "guid", "id"):
        if k.endswith(suf):
            k = k[: -len(suf)]
            break
    else:
        return fallback
    if k.endswith("ies"):
        k = k[:-3] + "y"
    elif k.endswith("s") and not k.endswith("ss") and not k.endswith("us"):
        k = k[:-1]
    return k or fallback


def kind_of_segment(seg, fallback="obj"):
    """Kind for a path segment like 'expenses' -> 'expense'."""
    k = re.sub(r"[^a-z0-9]", "", (seg or "").lower())
    if not k or k in ("api", "rest", "v1", "v2", "v3", "user", "users", "me"):
        return fallback
    if k.endswith("ies"):
        k = k[:-3] + "y"
    elif k.endswith("s") and not k.endswith("ss") and not k.endswith("us"):
        k = k[:-1]
    return k or fallback


def is_oid(v):
    return isinstance(v, str) and bool(UUID_RE.match(v) or NUM_RE.match(v) or HEX_RE.match(v))


def extract_ids(path, url, req_body, resp_body, ctype):
    """Return {(kind, oid, source)} for this flow."""
    found = set()
    p = path.split("?")[0]
    parts = [x for x in p.split("/") if x]
    path_kind = "obj"
    for i, seg in enumerate(parts):
        if is_oid(seg):
            path_kind = kind_of_segment(parts[i - 1] if i else "")
            found.add((path_kind, seg, "path"))
    qs = url.split("?", 1)[1] if "?" in url else ""
    for pair in qs.split("&"):
        if "=" not in pair:
            continue
        k, v = pair.split("=", 1)
        if IDKEY_RE.search(k) or k.lower() == "id":
            if is_oid(v):
                found.add((kind_of(k, path_kind), v, "query"))
    for blob, source in ((req_body, "body"), (resp_body, "resp")):
        if not blob or blob[0] not in "{[":
            continue
        try:
            d = json.loads(blob)
        except Exception:
            continue
        base = path_kind if path_kind != "obj" else None
        stack = [(d, 0, base or "obj")]
        while stack:
            node, depth, hint = stack.pop()
            if depth > 2:
                continue
            if isinstance(node, dict):
                for k, v in node.items():
                    if isinstance(v, (dict, list)):
                        stack.append((v, depth + 1, base or kind_of_segment(str(k), hint)))
                    elif is_oid(v) and (IDKEY_RE.search(str(k)) or str(k).lower() == "id"):
                        found.add((kind_of(str(k), hint), v, source))
            elif isinstance(node, list):
                for v in node[:20]:
                    if isinstance(v, (dict, list)):
                        stack.append((v, depth + 1, hint))
    return found


def classify(row):
    method, path, url = row["method"], row["path"], row["url"]
    if NOISE_PATH.search(path):
        return "Noise", 0, [], ""
    qs = url.split("?", 1)[1] if "?" in url else ""
    qparams = {p.split("=")[0] for p in qs.split("&") if p} if qs else set()
    rh = parse_headers(row["req_headers"])
    ctype = rh.get("content-type", "")
    keys = body_keys(row["req_body"], ctype)
    tags, why = [], []

    has_id = bool(ID_RE.search(path.split("?")[0]))
    if has_id:
        tags.append("IDOR")
        why.append("object id in path")
    if has_id and method in ("PUT", "PATCH", "DELETE"):
        tags.append("IDOR-write")
        why.append("id + write verb")
    if ADMIN_PATH.search(path):
        tags.append("BAC")
        why.append("privileged path prefix")
    if LOGIC_KEYS.search(" ".join(keys)) or LOGIC_KEYS.search(qs):
        tags.append("BusinessLogic")
        why.append("money/quantity field")
    if any(MASS_KEYS.match(k) for k in keys):
        tags.append("MassAssignment")
        why.append("role/owner field in body")
    if any(SQL_PARAMS.match(p) for p in qparams):
        tags.append("SQLi")
        why.append("search/sort param")
    if any(XSS_PARAMS.match(p) for p in qparams) or any(XSS_PARAMS.match(k) for k in keys):
        tags.append("XSS")
        why.append("text param")
    if any(SSRF_PARAMS.match(p) for p in qparams) or any(SSRF_PARAMS.match(k) for k in keys):
        tags.append("SSRF")
        why.append("url-like param")
    if "multipart" in ctype or UPLOAD_PATH.search(path):
        tags.append("FileUpload")
        why.append("upload surface")
    if method in ("POST", "PUT", "PATCH", "DELETE") and ("cookie" in rh) and not ({"csrf", "csrftoken", "x-csrf-token"} & set(rh)):
        tags.append("CSRF")
        why.append("state change + cookie auth, no csrf header")
    if AUTH_PATH.search(path):
        tags.append("Auth")
        why.append("auth surface")
    if INFO_PATH.search(path):
        tags.append("InfoLeak")
        why.append("config/debug path")
    if method == "POST" and RACE_KEYS.search(" ".join(keys)):
        tags.append("Race")
        why.append("state-changing counter")
    if row["status"] in (401, 403):
        why.append(f"status {row['status']}")

    primary = max(tags, key=lambda t: WEIGHT.get(t, 0)) if tags else "None"
    score = sum(WEIGHT.get(t, 0) for t in tags)
    return primary, score, tags, "; ".join(why)


def build_context(conn, out):
    lines = ["# Live context — what the app actually does (auto)", ""]
    lines.append("## Principals")
    lines.append("| principal | flows | endpoints | first | last |")
    lines.append("|---|---|---|---|---|")
    for r in conn.execute("""select principal, count(*), count(distinct path_template),
                                    datetime(min(ts),'unixepoch'), datetime(max(ts),'unixepoch')
                             from flows group by principal order by 2 desc"""):
        lines.append(f"| {r[0] or '?'} | {r[1]} | {r[2]} | {r[3]} | {r[4]} |")
    lines += ["", "## Objects touched"]
    lines.append("| kind | distinct ids | flows | endpoints | principals |")
    lines.append("|---|---|---|---|---|")
    for r in conn.execute("""select t.kind, count(distinct t.oid), count(distinct t.flow_id),
                                    count(distinct f.path_template), count(distinct f.principal)
                             from touches t join flows f on f.id=t.flow_id
                             group by t.kind order by 3 desc limit 20"""):
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} |")
    lines += ["", "## Features (by path prefix)"]
    lines.append("| prefix | endpoints | methods | flows | classes |")
    lines.append("|---|---|---|---|---|")
    for r in conn.execute("""select
              case when instr(substr(path_template,2),'/')>0
                   then substr(path_template,1,instr(substr(path_template,2),'/')+1)
                   else path_template end as pfx,
              count(distinct path_template), group_concat(distinct method),
              count(*), group_concat(distinct class)
            from flows where class not in ('Noise') group by pfx order by 4 desc limit 20"""):
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} | {r[3]} | {r[4]} |")
    lines += ["", "## Money / state-changing endpoints"]
    lines.append("| request | class | flows |")
    lines.append("|---|---|---|")
    for r in conn.execute("""select method||' '||path_template, class, count(*) from flows
                             where method in ('POST','PUT','PATCH','DELETE')
                               and class in ('BusinessLogic','Race','MassAssignment','IDOR-write')
                             group by 1,2 order by 3 desc limit 15"""):
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    lines += ["", "## Auth surface seen"]
    lines.append("| request | status | flows |")
    lines.append("|---|---|---|")
    for r in conn.execute("""select method||' '||path_template, status, count(*) from flows
                             where class='Auth' group by 1,2 order by 3 desc limit 15"""):
        lines.append(f"| {r[0]} | {r[1]} | {r[2]} |")
    with open(os.path.join(out, "context.md"), "w") as f:
        f.write("\n".join(lines) + "\n")


def build_chains(conn, out):
    chains = []
    # 1. auth flow that leaks a secret material
    for r in conn.execute("""select id, method||' '||path_template from flows
                             where class='Auth' and (resp_body like '%totpSecret%'
                                or resp_body like '%deluxeToken%' or resp_body like '%password%')
                             order by id limit 5"""):
        chains.append(f"- Auth flow `{r[1]}` (flow {r[0]}) returns secret material → "
                      f"take the secret, then log in as that user and skip 2FA.")
    # 2. same object read by one endpoint, written by another
    for r in conn.execute("""select t.kind, count(distinct t.flow_id)
                             from touches t join flows f on f.id=t.flow_id
                             where t.op='write' group by t.kind order by 2 desc limit 5"""):
        kind = r[0]
        reads = conn.execute("""select count(distinct path_template) from touches t
                                join flows f on f.id=t.flow_id
                                where t.kind=? and t.op='read'""", (kind,)).fetchone()[0]
        writes = conn.execute("""select count(distinct path_template) from touches t
                                 join flows f on f.id=t.flow_id
                                 where t.kind=? and t.op='write'""", (kind,)).fetchone()[0]
        if reads and writes:
            chains.append(f"- Object `{kind}`: {reads} read endpoint(s) and {writes} write endpoint(s). "
                          f"Check that the write re-checks the owner the read assumes.")
    # 3. mass assignment + privileged prefix
    if conn.execute("select count(*) from flows where class='MassAssignment'").fetchone()[0] and \
       conn.execute("select count(*) from flows where class='BAC'").fetchone()[0]:
        chains.append("- Mass-assignment candidate + privileged path both seen → "
                      "try role/isAdmin on the body, then call the admin path with the same token.")
    # 4. money + race
    if conn.execute("select count(*) from flows where class='Race'").fetchone()[0]:
        chains.append("- State-changing money flow tagged Race → fire concurrent duplicates and "
                      "compare balances/orders before and after.")
    # 5. object seen under more than one label
    for r in conn.execute("""select t.kind, t.oid, group_concat(distinct f.label)
                             from touches t join flows f on f.id=t.flow_id
                             where f.label<>'' group by t.kind,t.oid
                             having count(distinct f.label)>1 limit 5"""):
        chains.append(f"- Object `{r[0]}` `{r[1]}` was touched by {r[2]} → "
                      f"compare the two responses field by field.")
    lines = ["# Candidate chains (heuristic, not findings)", ""] + (chains or ["- none yet"]) + [""]
    with open(os.path.join(out, "chains.md"), "w") as f:
        f.write("\n".join(lines))


def capture_state(out):
    """'running since <time> · tab idle' — explains an empty delta without opening Chrome."""
    try:
        st = json.load(open(os.path.join(out, "run.json")))
    except Exception:
        return ""
    pid = st.get("pid")
    alive = bool(pid) and os.path.exists(f"/proc/{pid}")
    when = st.get("started") or "?"
    return f"{'running' if alive else 'stopped'} since {when}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--rebuild", action="store_true", help="re-classify every flow and rebuild hypotheses")
    a = ap.parse_args()
    db = os.path.join(a.out, "live.db")
    if not os.path.exists(db):
        sys.exit("no live.db yet — run cdp_capture.py first")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("pragma table_info(flows)")}
    for c in ("context", "label", "sub", "principal"):
        if c not in cols:
            conn.execute(f"alter table flows add column {c} text")
    conn.execute("update flows set principal='unknown' where principal is null")
    conn.commit()
    if a.rebuild:
        conn.execute("delete from hypotheses")
        conn.execute("delete from objects")
        conn.execute("delete from touches")
        conn.execute("delete from meta where k='classified_upto'")
        conn.commit()
    cur = conn.execute("select v from meta where k='classified_upto'").fetchone()
    upto = int(cur["v"]) if cur else 0
    rows = list(conn.execute("select * from flows where id > ? order by id", (upto,)))
    if not rows:
        total = conn.execute("select count(*) from flows").fetchone()[0]
        print(f"no new flows (cursor {upto}{', ' + str(total) + ' recorded' if total else ''})")
        if not total:
            state = capture_state(a.out)
            if state:
                print(f"capture   {state}")
            print("next      the browser has sent nothing yet — click into the app "
                  "(capture only records what happens after it starts)")
        build_context(conn, a.out)
        build_chains(conn, a.out)
        return

    counts, top, new_h = {}, {}, []
    for r in rows:
        primary, score, tags, why = classify(r)
        conn.execute("update flows set class=?, score=?, tags=? where id=?",
                     (primary, score, json.dumps(tags), r["id"]))
        counts[primary] = counts.get(primary, 0) + 1
        ctype = parse_headers(r["req_headers"]).get("content-type", "")
        op = "read" if r["method"] in ("GET", "HEAD") else "write"
        for kind, oid, source in extract_ids(r["path"], r["url"], r["req_body"], r["resp_body"], ctype):
            conn.execute("""insert into objects(kind,oid,first_flow,last_flow,count) values(?,?,?,?,1)
                            on conflict(kind,oid) do update set last_flow=excluded.last_flow, count=count+1""",
                         (kind, oid, r["id"], r["id"]))
            conn.execute("insert or ignore into touches(flow_id,kind,oid,op,source) values(?,?,?,?,?)",
                         (r["id"], kind, oid, op, source))
        if primary not in ("None", "Noise") and score >= 3:
            k = (primary, r["method"], r["path_template"])
            e = top.get(k)
            if e:
                e["n"] += 1
                e["score"] = max(e["score"], score)
            else:
                top[k] = {"score": score, "n": 1, "why": why, "flow": r["id"]}
            test = NEXT_TEST.get(primary, "review manually")
            existing = conn.execute(
                "select id from hypotheses where host=? and class=? and method=? and path_template=?",
                (r["host"], primary, r["method"], r["path_template"])).fetchone()
            if existing:
                conn.execute("update hypotheses set count=count+1, last_flow=? where id=?",
                             (r["id"], existing["id"]))
            else:
                conn.execute("insert into hypotheses(host,class,method,path_template,first_flow,last_flow,count,score,why,next_test)"
                             " values(?,?,?,?,?,?,?,?,?,?)",
                             (r["host"], primary, r["method"], r["path_template"], r["id"], r["id"], 1, score, why, test))
                new_h.append((primary, r["method"], r["path_template"], test))

    # principal collisions: same object touched under two different labels
    for row in conn.execute("""select t.kind, t.oid, count(distinct f.label) n, group_concat(distinct f.label)
                               from touches t join flows f on f.id=t.flow_id
                               where f.label<>'' group by t.kind,t.oid having n>1 limit 50"""):
        conn.execute("""update hypotheses set score=score+2,
                        why=why||' ; object '||?||'/'||?||' seen under '||?
                        where class like 'IDOR%' and path_template in
                          (select f.path_template from touches t join flows f on f.id=t.flow_id
                           where t.kind=? and t.oid=?)""",
                     (row["kind"], row["oid"], row["n"], row["kind"], row["oid"]))

    conn.execute("insert or replace into meta(k,v) values('classified_upto',?)", (str(rows[-1]["id"]),))
    conn.commit()

    ranked = sorted(top.items(), key=lambda kv: (-kv[1]["score"], -kv[1]["n"]))
    tagged = {k: v for k, v in counts.items() if k not in ("None", "Noise")}
    untagged = counts.get("None", 0) + counts.get("Noise", 0)
    lines = [f"# live delta — {time.strftime('%Y-%m-%d %H:%M:%S')}",
             f"new flows: {len(rows)} (ids {rows[0]['id']}..{rows[-1]['id']})", ""]
    if tagged:
        lines += ["| class | count |", "|---|---|"]
        for k, v in sorted(tagged.items(), key=lambda x: -x[1]):
            lines.append(f"| {k} | {v} |")
    lines.append(f"untagged {untagged}" if untagged else "untagged 0")
    if not tagged:
        lines += ["", "no class fired — these flows are surface, not candidates."]
    if ranked:
        lines += ["", "## top candidates (one row per endpoint)", "| class | score | n | request | why | first flow |",
                  "|---|---|---|---|---|---|"]
        for (cls, method, pt), e in ranked[:8]:
            lines.append(f"| {cls} | {e['score']} | {e['n']} | {method} {pt} | {e['why'][:70]} | {e['flow']} |")
    if new_h:
        lines += ["", "## new hypotheses", "| class | request | next test |", "|---|---|---|"]
        for cls, method, pt, test in new_h[:10]:
            lines.append(f"| {cls} | {method} {pt} | {test} |")
    text = "\n".join(lines) + "\n"
    with open(os.path.join(a.out, "delta.md"), "w") as f:
        f.write(text)
    build_context(conn, a.out)
    build_chains(conn, a.out)
    print(text)


def _guard():
    """Never die with a traceback."""
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
