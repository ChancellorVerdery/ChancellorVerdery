#!/usr/bin/env python3
"""Render a GitHub-style heatmap of daily Claude Code usage.

Only per-day totals are kept or published — never prompts, paths or project names.

Sources, summed per day:
  * session logs on this machine (~/.claude/projects/**/*.jsonl), stored under --source (default "this-mac");
  * other machines' logs, imported once with `--import NAME < logs` (raw .jsonl lines on stdin) or
    `--import-totals NAME file.json` (the output of `--export` run on that machine), `--import-stats NAME
    stats-cache.json` (Claude Code's own /stats cache, which outlives the transcripts) or `--import-ledger NAME file.csv`
    (per-day token ledger: date,model,project,records,sessions,input_tokens,output_tokens,cache_read…,cache_creation…);
  * Claude-co-authored git commits in the repos under ~/Desktop/Claude, which mark days whose session logs are gone
    (Claude Code deletes transcripts after 30 days). Those days are drawn in a lighter shade and labelled as such.

Writes assets/claude-usage.svg and assets/claude-usage.json in this script's repo.
"""
import argparse, datetime as dt, glob, json, os, subprocess, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "assets", "claude-usage.json")
LOGS = os.path.expanduser("~/.claude/projects")
REPOS = [os.path.expanduser("~/Desktop/Claude"), os.path.expanduser("~/Desktop/Claude/Atlas Archive/clones")]
WEEKS = 53


def aggregate(lines):
    days = defaultdict(lambda: {"tokens": 0, "output": 0, "messages": 0, "sessions": set()})
    seen = set()
    for line in lines:
        if '"assistant"' not in line or '"usage"' not in line:
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant" or not d.get("timestamp"):
            continue
        msg = d.get("message") or {}
        key = d.get("requestId") or msg.get("id") or d.get("uuid")
        if key in seen:
            continue
        seen.add(key)
        u = msg.get("usage") or {}
        day = dt.datetime.fromisoformat(d["timestamp"].replace("Z", "+00:00")).astimezone().date().isoformat()
        rec = days[day]
        rec["tokens"] += sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        rec["output"] += u.get("output_tokens") or 0
        rec["messages"] += 1
        if d.get("sessionId"):
            rec["sessions"].add(d["sessionId"])
    return {k: {**{f: v[f] for f in ("tokens", "output", "messages")}, "sessions": len(v["sessions"])} for k, v in sorted(days.items())}


def local_lines():
    for path in glob.glob(os.path.join(LOGS, "**", "*.jsonl"), recursive=True):
        try:
            with open(path, encoding="utf-8", errors="ignore") as fh:
                yield from fh
        except OSError:
            continue


def claude_commits():
    """Days with commits co-authored by Claude, across every git repo one level under REPOS."""
    days, seen = defaultdict(int), set()
    me = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True).stdout.strip()
    if not me:
        return {}
    for base in REPOS:
        for repo in glob.glob(os.path.join(base, "*", ".git")):
            try:
                out = subprocess.run(["git", "-C", os.path.dirname(repo), "log", "--all", f"--author={me}",
                                      "--format=%H %aI %(trailers:key=Co-Authored-By,valueonly,separator=|)"],
                                     capture_output=True, text=True, timeout=60).stdout
            except (OSError, subprocess.SubprocessError):
                continue
            for line in out.splitlines():
                sha, _, rest = line.partition(" ")
                when, _, trailers = rest.partition(" ")
                if sha in seen or "claude" not in trailers.lower():
                    continue
                seen.add(sha)
                days[dt.datetime.fromisoformat(when).astimezone().date().isoformat()] += 1
    return dict(sorted(days.items()))


def from_stats(path):
    """Claude Code's stats-cache.json: daily message counts (dailyActivity) and daily tokens per model."""
    d = json.load(open(path))
    days = defaultdict(lambda: {"tokens": 0, "output": 0, "messages": 0, "sessions": 0})
    for a in d.get("dailyActivity", []):
        days[a["date"]]["messages"] = a.get("messageCount", 0)
        days[a["date"]]["sessions"] = a.get("sessionCount", 0)
    for t in d.get("dailyModelTokens", []):
        days[t["date"]]["tokens"] = sum((t.get("tokensByModel") or {}).values())
    return dict(days)


def from_ledger(path):
    import csv
    days = defaultdict(lambda: {"tokens": 0, "output": 0, "messages": 0, "sessions": 0})
    for row in csv.DictReader(open(path)):
        rec, n = days[row["date"]], lambda k: int(row.get(k) or 0)
        rec["tokens"] += n("input_tokens") + n("output_tokens") + n("cache_read_input_tokens") + n("cache_creation_input_tokens")
        rec["output"] += n("output_tokens")
        rec["messages"] += n("records")
        rec["sessions"] += n("sessions")
    return dict(days)


def load():
    try:
        data = json.load(open(DATA))
    except (OSError, ValueError):
        data = {}
    if "days" in data and "sources" not in data:          # first format: one machine, no sources
        data["sources"] = {"this-mac": data.pop("days")}
    data.setdefault("sources", {})
    data.setdefault("commits", {})
    return data


def merge(into, fresh):
    """Same machine, another read or another record of it: keep the larger value of each field per day, so days
    survive log cleanup and overlapping records (logs, stats cache, ledger) never double count."""
    for k, v in fresh.items():
        old = into.setdefault(k, {})
        for f in ("tokens", "output", "messages", "sessions"):
            old[f] = max(old.get(f, 0), v.get(f, 0))


def totals(data):
    days = defaultdict(lambda: {"tokens": 0, "output": 0, "messages": 0})
    for src in data["sources"].values():
        for k, v in src.items():
            for f in ("tokens", "output", "messages"):
                days[k][f] += v.get(f, 0)
    return days


def human(n):
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)


def streaks(active, today):
    longest = run = 0
    d = min((dt.date.fromisoformat(k) for k in active), default=today)
    while d <= today:
        run = run + 1 if d.isoformat() in active else 0
        longest = max(longest, run)
        d += dt.timedelta(days=1)
    cur, d = 0, today if today.isoformat() in active else today - dt.timedelta(days=1)
    while d.isoformat() in active:
        cur += 1
        d -= dt.timedelta(days=1)
    return cur, longest


def render(data):
    today = dt.date.today()
    start = today - dt.timedelta(days=(today.weekday() + 1) % 7) - dt.timedelta(weeks=WEEKS - 1)
    days, commits = totals(data), data["commits"]
    vals = sorted(v["messages"] for v in days.values() if v["messages"])
    quart = [vals[int(len(vals) * f)] for f in (0.25, 0.5, 0.75)] if vals else []

    def level(k):
        v = days.get(k)
        if v and v["messages"]:
            return f"l{1 + sum(v['messages'] > t for t in quart)}"
        return "lc" if commits.get(k) else "l0"

    cell, gap, left, top = 11, 3, 30, 34
    width = left + WEEKS * (cell + gap) + 10
    height = top + 7 * (cell + gap) + 46
    out, months = [], set()
    d = start
    for w in range(WEEKS):
        for r in range(7):
            if d > today:
                break
            k, v = d.isoformat(), days.get(d.isoformat())
            if v and v["messages"]:
                tip = f'{d:%b %-d, %Y}: {v["messages"]:,} messages, {human(v["tokens"])} tokens'
            elif commits.get(k):
                tip = f'{d:%b %-d, %Y}: {commits[k]} Claude-co-authored commits (session logs no longer kept)'
            else:
                tip = f'{d:%b %-d, %Y}: no activity'
            x, y = left + w * (cell + gap), top + r * (cell + gap)
            out.append(f'<rect class="{level(k)}" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2"><title>{tip}</title></rect>')
            if d.day <= 7 and r == 0 and (d.year, d.month) not in months:
                months.add((d.year, d.month))
                out.append(f'<text class="t" x="{x}" y="{top - 8}">{d:%b}</text>')
            d += dt.timedelta(days=1)
    for r, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out.append(f'<text class="t" x="0" y="{top + r * (cell + gap) + 9}">{name}</text>')

    active = {k for k, v in days.items() if v["messages"]} | {k for k, n in commits.items() if n}
    window = [k for k in active if k >= start.isoformat()]
    first = min(active) if active else today.isoformat()
    cur, longest = streaks(active, today)
    total = sum(v["tokens"] for v in days.values())
    msgs = sum(v["messages"] for v in days.values())
    summary = (f'{len(window)} active days since {dt.date.fromisoformat(first):%b %-d, %Y} · {human(total)} tokens · '
               f'{msgs:,} messages logged · streak {cur} d (longest {longest})')
    lx = width - 10 - 6 * (cell + gap) - 64
    legend = [f'<text class="t" x="{lx}" y="{height - 12}">Less</text>'] + [
        f'<rect class="{c}" x="{lx + 30 + i * (cell + gap)}" y="{height - 22}" width="{cell}" height="{cell}" rx="2"/>'
        for i, c in enumerate(("l0", "lc", "l1", "l2", "l3", "l4"))] + [
        f'<text class="t" x="{lx + 34 + 6 * (cell + gap)}" y="{height - 12}">More</text>']
    font = "-apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif"
    style = f"""
  .t{{font:10px {font};fill:#57606a}} .h{{font:600 13px {font};fill:#1f2328}} .s{{font:11px {font};fill:#57606a}}
  .l0{{fill:#ebedf0}}.lc{{fill:#f8e6dc;stroke:#e3b49c;stroke-width:1}}.l1{{fill:#f5d0bd}}.l2{{fill:#eba27f}}.l3{{fill:#d97757}}.l4{{fill:#a94f31}}
  @media (prefers-color-scheme: dark){{
    .t,.s{{fill:#8b949e}}.h{{fill:#e6edf3}}
    .l0{{fill:#161b22}}.lc{{fill:#2a1a14;stroke:#7d3a22}}.l1{{fill:#4a2618}}.l2{{fill:#7d3a22}}.l3{{fill:#c2603d}}.l4{{fill:#ec8f6a}}
  }}"""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'role="img" aria-label="Claude Code activity: {summary}"><style>{style}</style>'
           f'<text class="h" x="0" y="14">Claude Code activity</text>'
           f'<text class="s" x="{width - 10}" y="14" text-anchor="end">{summary}</text>'
           + "".join(out) + "".join(legend) +
           f'<text class="t" x="0" y="{height - 12}">Updated {today:%b %-d, %Y} · shade = messages per day · '
           f'outlined = days known only from Claude-co-authored commits</text></svg>')
    data["updated"], data["summary"] = today.isoformat(), summary
    return svg


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--source", default="this-mac", help="name for this machine's logs (default: this-mac)")
    ap.add_argument("--import", dest="imp", metavar="NAME", help="aggregate raw .jsonl session logs from stdin as source NAME")
    ap.add_argument("--import-totals", nargs=2, metavar=("NAME", "FILE"), help="merge an --export file as source NAME")
    ap.add_argument("--import-stats", nargs=2, metavar=("NAME", "FILE"), help="merge Claude Code's stats-cache.json as source NAME")
    ap.add_argument("--import-ledger", nargs=2, metavar=("NAME", "FILE"), help="merge a per-day token ledger CSV as source NAME")
    ap.add_argument("--export", action="store_true", help="print this machine's daily totals as JSON and exit")
    a = ap.parse_args()
    if a.export:
        json.dump(aggregate(local_lines()), sys.stdout, indent=1)
        return
    data = load()
    if a.imp:
        merge(data["sources"].setdefault(a.imp, {}), aggregate(sys.stdin))
    elif a.import_totals:
        merge(data["sources"].setdefault(a.import_totals[0], {}), json.load(open(a.import_totals[1])))
    elif a.import_stats:
        merge(data["sources"].setdefault(a.import_stats[0], {}), from_stats(a.import_stats[1]))
    elif a.import_ledger:
        merge(data["sources"].setdefault(a.import_ledger[0], {}), from_ledger(a.import_ledger[1]))
    merge(data["sources"].setdefault(a.source, {}), aggregate(local_lines()))
    for k, n in claude_commits().items():
        data["commits"][k] = max(n, data["commits"].get(k, 0))
    svg = render(data)
    os.makedirs(os.path.dirname(DATA), exist_ok=True)
    open(os.path.join(ROOT, "assets", "claude-usage.svg"), "w").write(svg)
    json.dump(data, open(DATA, "w"), indent=1, sort_keys=True)
    print(data["summary"])


if __name__ == "__main__":
    main()
