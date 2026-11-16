"""Claude Code usage from local transcripts (~/.claude/projects/**/*.jsonl).

No network, no login token: every assistant reply Claude Code writes carries a `usage`
block. We sum those into 5-hour blocks (same rule as ccusage: a block starts at the hour of
its first message and lasts 5 hours; a 5-hour gap also starts a new one).

The plan limit itself is not in the files, so the percentage is an estimate against either
a number you set or, by default, your largest finished block of the last 8 days.
"""
import calendar
import json
import os
import time

BLOCK = 5 * 3600
KEEP = 8 * 86400                       # how far back we hold single replies
ROOT = os.path.join(os.path.expanduser('~'), '.claude', 'projects')


def _ts(s):
    """ISO-8601 'Z' timestamp -> epoch seconds (None if unreadable)."""
    if not isinstance(s, str) or len(s) < 19:
        return None
    try:
        t = calendar.timegm(time.strptime(s[:19], '%Y-%m-%dT%H:%M:%S'))
    except (ValueError, OverflowError):
        return None
    frac = s[19:].split('Z')[0].split('+')[0]
    return t + (float('0' + frac) if frac.startswith('.') and frac[1:].isdigit() else 0)


def parse_line(raw):
    """One transcript line -> (key, ts, [input, output, cache_write, cache_read]) or None."""
    if b'"usage"' not in raw or b'"assistant"' not in raw:
        return None
    try:
        o = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(o, dict) or o.get('type') != 'assistant':
        return None
    m = o.get('message')
    us = m.get('usage') if isinstance(m, dict) else None
    if not isinstance(us, dict) or str(m.get('model') or '').startswith('<'):   # <synthetic>
        return None
    try:
        u = [int(us.get(k) or 0) for k in
             ('input_tokens', 'output_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
    except (TypeError, ValueError):
        return None
    ts = _ts(o.get('timestamp'))
    if ts is None or not any(u):
        return None
    key = '%s|%s' % (m.get('id') or '', o.get('requestId') or '')
    if key == '|':
        key = 'u|%s' % o.get('uuid')
    return key, ts, u


def used(u, cache_read=False):
    """Tokens that count toward the limit. Cache reads are cheap and left out by default."""
    return u[0] + u[1] + u[2] + (u[3] if cache_read else 0)


class Reader:
    """Reads only what was appended since last time. Replies are keyed by message id +
    request id, so a reply copied into a resumed session or streamed over several lines
    is counted once (the last line wins)."""

    def __init__(self, root=ROOT):
        self.root = root
        self.files = {}                # path -> [size, offset]
        self.recs = {}                 # key -> (ts, u)

    def refresh(self, now=None):
        now = now or time.time()
        floor = now - KEEP
        for dp, _dn, fn in os.walk(self.root):
            for n in fn:
                if not n.endswith('.jsonl'):
                    continue
                p = os.path.join(dp, n)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                if st.st_mtime < floor:
                    continue
                size, off = self.files.get(p, (0, 0))
                if st.st_size == size:
                    continue
                if st.st_size < off:   # file was rewritten — read again from the start
                    off = 0
                try:
                    with open(p, 'rb') as f:
                        f.seek(off)
                        buf = f.read(st.st_size - off)
                except OSError:
                    continue
                end = buf.rfind(b'\n')
                if end >= 0:
                    for raw in buf[:end].split(b'\n'):
                        r = parse_line(raw)
                        if r and r[1] >= floor:
                            self.recs[r[0]] = (r[1], r[2])
                    off += end + 1
                self.files[p] = (st.st_size, off)
        for k in [k for k, (ts, _u) in self.recs.items() if ts < floor]:
            del self.recs[k]
        return sorted(self.recs.values(), key=lambda r: r[0])


def blocks(recs):
    """[(start, last_ts, [4 sums])] in time order."""
    out = []
    for ts, u in recs:
        b = out[-1] if out else None
        if b is None or ts >= b[0] + BLOCK or ts - b[1] >= BLOCK:
            b = [ts - ts % 3600, ts, [0, 0, 0, 0]]
            out.append(b)
        b[1] = ts
        for i in range(4):
            b[2][i] += u[i]
    return [tuple(b) for b in out]


def summary(recs, limit=None, cache_read=False, now=None):
    """Numbers for the tray. limit=None -> auto (largest finished block of the last 8 days)."""
    now = now or time.time()
    bl = blocks(recs)
    cur = bl[-1] if bl and now < bl[-1][0] + BLOCK and now - bl[-1][1] < BLOCK else None
    done = [used(b[2], cache_read) for b in bl if b is not cur]
    auto = max(done) if done else None
    cap = limit or auto
    block_used = used(cur[2], cache_read) if cur else 0
    lt = time.localtime(now)
    midnight = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))
    today = sum(used(u, cache_read) for ts, u in recs if ts >= midnight)
    week = sum(used(u, cache_read) for ts, u in recs if ts >= now - 7 * 86400)
    recent = [(ts, u) for ts, u in recs if ts >= now - 600]
    per_min = sum(used(u, cache_read) for _ts, u in recent) / 10.0
    pct = round(block_used * 100.0 / cap) if cap and cur else (0 if cap else None)
    return {
        'active': bool(cur), 'block_used': block_used, 'limit': cap, 'auto_limit': auto,
        'limit_is_auto': not limit, 'pct': pct,
        'resets_at': cur[0] + BLOCK if cur else None,
        'today': today, 'week': week, 'per_min': per_min,
        'hits_limit_at': (now + (cap - block_used) / per_min * 60
                          if cur and cap and per_min > 0 and block_used < cap else None),
    }


def short(n):
    """1234 -> 1.2K, 2500000 -> 2.5M"""
    if n is None:
        return '-'
    for div, s in ((1e9, 'B'), (1e6, 'M'), (1e3, 'K')):
        if n >= div:
            v = n / div
            return ('%.1f' % v).rstrip('0').rstrip('.') + s
    return str(int(n))
