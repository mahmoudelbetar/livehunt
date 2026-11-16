"""Host-filter parsing and matching.

The `--hosts` filter is what decides whether a captured exchange is kept, so it must
accept what a human actually types. Every entry is normalised once, then matched
against the hostname + effective port of each request.

Accepted entry forms (comma or whitespace separated):

    app.example.com            host, any port
    app.example.com:8443       host, that port only
    https://app.example.com/x  scheme, path, query and fragment are stripped
    *.example.com              example.com and any subdomain
    .example.com               same as *.example.com
    *                          everything (last resort, warns on start)

Ports default to 80/443 by scheme, so `app.example.com:443` matches
`https://app.example.com/` and `app.example.com` matches `http://app.example.com:8080/`.
"""
import re
from urllib.parse import urlsplit

DEFAULT_PORTS = {"http": 80, "https": 443, "ws": 80, "wss": 443}


def _split_entry(part):
    """'https://user@App.Example.com:8443/x?y' -> ('.example.com', 8443) style tuple."""
    e = (part or "").strip()
    if not e:
        return None
    low = e.lower()
    if "://" in low:
        low = low.split("://", 1)[1]
    low = low.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    if "@" in low:
        low = low.rsplit("@", 1)[1]

    port = None
    if low.startswith("["):                      # [::1]:3000
        host, _, rest = low[1:].partition("]")
        if rest.startswith(":") and rest[1:].isdigit():
            port = int(rest[1:])
    else:
        host, sep, maybe = low.rpartition(":")
        if sep and maybe.isdigit():
            port = int(maybe)
        else:
            host = low
    host = host.strip().strip(".")
    if not host:
        return None
    if not re.match(r"^[a-z0-9._\-\[\]:*]+$", host):
        return None                              # spaces, unicode, shell junk

    if host in ("*", "*.", "*.*"):
        return ("*", port)
    if host.startswith("*."):
        host = host[1:]                          # *.example.com -> .example.com
    if host.startswith("."):
        return ("." + host.lstrip("."), port)
    return (host, port)


def parse_hosts(raw):
    """Raw filter text -> ordered, de-duplicated [(pattern, port_or_None)]."""
    if isinstance(raw, (list, tuple, set)):      # already-split input
        parts = [str(x) for x in raw]
    else:
        parts = re.split(r"[,\s]+", str(raw or ""))
    out, seen = [], set()
    for part in parts:
        item = _split_entry(part)
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def fmt_entry(entry):
    """(pattern, port) -> the canonical spelling used in run.json and messages."""
    pat, port = entry
    if pat == "*":
        base = "*"
    elif pat.startswith("."):
        base = "*" + pat
    else:
        base = pat
    return base if port is None else f"{base}:{port}"


def normalize_hosts(raw):
    """Raw filter text -> canonical entry list, e.g. ['app.example.com', '*.example.com']."""
    return [fmt_entry(e) for e in parse_hosts(raw)]


def canonical_hosts(raw):
    """Raw filter text -> the string to persist in run.json."""
    return ",".join(normalize_hosts(raw))


def sample_url(raw):
    """A URL worth opening first, from the most specific filter entry.

    juice.local:3000 -> http://juice.local:3000
    app.navan.com    -> https://app.navan.com
    """
    for pat, port in parse_hosts(raw):
        if pat == "*" or pat.startswith("."):
            continue
        host = f"[{pat}]" if ":" in pat else pat
        scheme = "http" if port == 80 else "https"
        if port and port not in (80, 443):
            return f"{scheme}://{host}:{port}"
        return f"{scheme}://{host}"
    return None


class HostFilter:
    """Matcher used by the capture loop and by any offline replay."""

    def __init__(self, raw=""):
        self.raw = raw if isinstance(raw, str) else ",".join(str(x) for x in raw)
        self.entries = parse_hosts(raw)

    @property
    def canonical(self):
        return [fmt_entry(e) for e in self.entries]

    def __bool__(self):
        return bool(self.entries)

    def __iter__(self):
        return iter(self.entries)

    def __len__(self):
        return len(self.entries)

    def __str__(self):
        return ",".join(self.canonical)

    __repr__ = __str__

    def matches(self, host, port=None, scheme=None):
        host = (host or "").strip().lower().rstrip(".")
        if not host:
            return False
        if port is None and scheme:
            port = DEFAULT_PORTS.get(str(scheme).lower())
        for pat, want in self.entries:
            if want is not None and want != port:
                continue
            if pat == "*" or pat == host:
                return True
            if pat.startswith(".") and (host == pat[1:] or host.endswith(pat)):
                return True
        return False

    def matches_url(self, url):
        u = urlsplit(url or "")
        return self.matches(u.hostname, u.port, u.scheme)
