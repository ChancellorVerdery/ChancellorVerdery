#!/usr/bin/env python3
"""Render a GitHub-style heatmap of daily Claude Code usage from local transcripts.

Reads ~/.claude/projects/**/*.jsonl and publishes only per-day totals (tokens, messages, sessions) — never prompts,
paths or project names. Writes assets/claude-usage.svg and assets/claude-usage.json next to this script's repo.
"""
import json, os, glob, datetime as dt
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.expanduser("~/.claude/projects")
WEEKS = 53

def collect():
    days = defaultdict(lambda: {"tokens": 0, "output": 0, "messages": 0, "sessions": set()})
    seen = set()
    for path in glob.glob(os.path.join(SRC, "**", "*.jsonl"), recursive=True):
        try:
            fh = open(path, encoding="utf-8", errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"assistant"' not in line or '"usage"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except ValueError:
                    continue
                if d.get("type") != "assistant":
                    continue
                msg = d.get("message") or {}
                key = d.get("requestId") or msg.get("id") or d.get("uuid")
                if key in seen:
                    continue
                seen.add(key)
                u = msg.get("usage") or {}
                ts = d.get("timestamp")
                if not ts:
                    continue
                day = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()
                tok = sum(u.get(k) or 0 for k in ("input_tokens", "output_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
                rec = days[day]
                rec["tokens"] += tok
                rec["output"] += u.get("output_tokens") or 0
                rec["messages"] += 1
                if d.get("sessionId"):
                    rec["sessions"].add(d["sessionId"])
    return {k: {"tokens": v["tokens"], "output": v["output"], "messages": v["messages"], "sessions": len(v["sessions"])}
            for k, v in sorted(days.items())}

def streaks(days, today):
    active = {k for k, v in days.items() if v["messages"]}
    longest = run = 0
    d = min((dt.date.fromisoformat(k) for k in active), default=today)
    while d <= today:
        run = run + 1 if d.isoformat() in active else 0
        longest = max(longest, run)
        d += dt.timedelta(days=1)
    cur, d = 0, today if today.isoformat() in active else today - dt.timedelta(days=1)
    while d.isoformat() in active:
        cur += 1; d -= dt.timedelta(days=1)
    return cur, longest

def human(n):
    for unit, size in (("B", 1e9), ("M", 1e6), ("K", 1e3)):
        if n >= size:
            return f"{n / size:.1f}{unit}"
    return str(n)

def render(days):
    today = dt.date.today()
    start = today - dt.timedelta(days=today.weekday() + 1 if today.weekday() != 6 else 0) - dt.timedelta(weeks=WEEKS - 1)
    vals = sorted(v["output"] for v in days.values() if v["output"])
    def level(x):
        if not x or not vals: return 0
        q = [vals[int(len(vals) * f)] for f in (0.25, 0.5, 0.75)]
        return 1 + sum(x > t for t in q)
    cell, gap, left, top = 11, 3, 30, 34
    width = left + WEEKS * (cell + gap) + 10
    height = top + 7 * (cell + gap) + 46
    out = []
    months_drawn = set()
    d = start
    for w in range(WEEKS):
        for r in range(7):
            if d > today: break
            v = days.get(d.isoformat(), {})
            x, y = left + w * (cell + gap), top + r * (cell + gap)
            tip = f'{d:%b %-d, %Y}: {v.get("messages", 0)} messages, {human(v.get("tokens", 0))} tokens'
            out.append(f'<rect class="l{level(v.get("output", 0))}" x="{x}" y="{y}" width="{cell}" height="{cell}" rx="2"><title>{tip}</title></rect>')
            if d.day <= 7 and r == 0 and (d.year, d.month) not in months_drawn:
                months_drawn.add((d.year, d.month))
                out.append(f'<text class="t" x="{x}" y="{top - 8}">{d:%b}</text>')
            d += dt.timedelta(days=1)
    for r, name in ((1, "Mon"), (3, "Wed"), (5, "Fri")):
        out.append(f'<text class="t" x="0" y="{top + r * (cell + gap) + 9}">{name}</text>')
    window = {k: v for k, v in days.items() if k >= start.isoformat()}
    total = sum(v["tokens"] for v in window.values())
    active = sum(1 for v in window.values() if v["messages"])
    msgs = sum(v["messages"] for v in window.values())
    cur, longest = streaks(days, today)
    summary = (f'{human(total)} tokens · {msgs:,} messages · {active} active days · '
               f'current streak {cur} d · longest {longest} d')
    legend_x = width - 10 - 5 * (cell + gap) - 64
    legend = [f'<text class="t" x="{legend_x}" y="{height - 12}">Less</text>'] + [
        f'<rect class="l{i}" x="{legend_x + 30 + i * (cell + gap)}" y="{height - 22}" width="{cell}" height="{cell}" rx="2"/>' for i in range(5)
    ] + [f'<text class="t" x="{legend_x + 34 + 5 * (cell + gap)}" y="{height - 12}">More</text>']
    style = """
  .t{font:10px -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;fill:#57606a}
  .h{font:600 13px -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;fill:#1f2328}
  .s{font:11px -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;fill:#57606a}
  .l0{fill:#ebedf0}.l1{fill:#f5d0bd}.l2{fill:#eba27f}.l3{fill:#d97757}.l4{fill:#a94f31}
  @media (prefers-color-scheme: dark){
    .t,.s{fill:#8b949e}.h{fill:#e6edf3}
    .l0{fill:#161b22}.l1{fill:#4a2618}.l2{fill:#7d3a22}.l3{fill:#c2603d}.l4{fill:#ec8f6a}
  }"""
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'role="img" aria-label="Claude Code usage, last 12 months: {summary}"><style>{style}</style>'
           f'<text class="h" x="0" y="14">Claude Code activity</text>'
           f'<text class="s" x="{width - 10}" y="14" text-anchor="end">{summary}</text>'
           + "".join(out) + "".join(legend) +
           f'<text class="t" x="0" y="{height - 12}">Updated {today:%b %-d, %Y} · shade = output tokens per day</text></svg>')
    return svg, {"updated": today.isoformat(), "summary": summary, "days": days}

def merged():
    """Fresh totals merged with the saved history, so days survive Claude Code's transcript cleanup (30 days)."""
    days = collect()
    try:
        saved = json.load(open(os.path.join(ROOT, "assets", "claude-usage.json"))).get("days", {})
    except (OSError, ValueError):
        saved = {}
    for k, v in saved.items():
        if k not in days or v.get("tokens", 0) > days[k]["tokens"]:
            days[k] = v
    return dict(sorted(days.items()))

if __name__ == "__main__":
    svg, data = render(merged())
    os.makedirs(os.path.join(ROOT, "assets"), exist_ok=True)
    open(os.path.join(ROOT, "assets", "claude-usage.svg"), "w").write(svg)
    json.dump(data, open(os.path.join(ROOT, "assets", "claude-usage.json"), "w"), indent=1)
    print(data["summary"])
