"""
mx-data table parsing utilities.

Extracts two repeated patterns from fetchers.py:
1. stdout table parsing: find headers + date rows, build col→val dict
2. raw JSON file lookup: glob + mtime check + json.load
"""

import glob as _glob
import json as _json
import os
import re
import time


def find_latest_raw_json(query_str: str, max_age: int = 120) -> str:
    """Find the latest mx-data raw JSON file matching query_str.

    Search order:
    1. ~/.openclaw/workspace/mx_data/output/mx_data_{safe}_raw.json (live cache,
       subject to max_age)
    2. <cwd>/**/mx_data_{safe}_raw.json (workspace local cache, recursive,
       NOT subject to max_age — explicit archives are trusted)
    3. Fuzzy keyword match across all caches (NOT subject to max_age)

    Returns the path, or "" if no file found.
    """
    safe = query_str.replace(" ", "_")
    # 1. Live cache in ~/.openclaw (subject to max_age)
    live = _glob.glob(
        os.path.expanduser(f"~/.openclaw/workspace/mx_data/output/mx_data_{safe}_raw.json")
    )
    live_age_ok = [p for p in live if os.path.getmtime(p) >= time.time() - max_age]
    if live_age_ok:
        return max(live_age_ok, key=os.path.getmtime)

    # 2. Workspace local cache (explicit archives are trusted, ignore age)
    workspace_hits = _glob.glob(
        os.path.join(os.getcwd(), "**", f"mx_data_{safe}_raw.json"),
        recursive=True,
    )
    if workspace_hits:
        return max(workspace_hits, key=os.path.getmtime)

    # 3. Fuzzy keyword match across all caches.
    # Strategy: prefer local workspace cache (mtime independent),
    # only fall back to live cache if it has >= half the query keywords
    # (i.e. it's a reasonably close match, not just a partial overlap).
    m = re.match(r"(\d{6})", safe)
    if m:
        ticker = m.group(1)
        parts = [p for p in safe.split("_") if p]
        meaningful = [p for p in parts if len(p) >= 2 and p != ticker]
        if meaningful:
            # Gather workspace + live candidates separately
            ws_caches = _glob.glob(
                os.path.join(os.getcwd(), "**", "mx_data_*_raw.json"),
                recursive=True,
            )
            live_caches = _glob.glob(
                os.path.expanduser("~/.openclaw/workspace/mx_data/output/mx_data_*_raw.json")
            )

            def score(path: str) -> tuple:
                """Return (is_workspace, hit_count, mtime) for sorting."""
                fname = os.path.basename(path)
                is_ws = os.getcwd() in os.path.realpath(path)
                hits = sum(1 for p in meaningful if p in fname)
                if ticker not in fname:
                    return (-1, -1, 0)  # exclude
                return (1 if is_ws else 0, hits, os.path.getmtime(path))

            all_hits = [p for p in ws_caches + live_caches if ticker in os.path.basename(p)]
            scored = [(score(p), p) for p in all_hits]
            # Filter to entries with at least 1 keyword hit
            scored = [s for s in scored if s[0][1] >= 1]
            if scored:
                # Sort: workspace-first, then hits DESC, then mtime DESC
                scored.sort(key=lambda x: (-x[0][0], -x[0][1], -x[0][2]))
                return scored[0][1]
    return ""


def parse_stdout_table(stdout: str) -> tuple:
    """Parse mx-data table output: return (headers, date_rows).

    headers: list[str]  - column names from the | date | line
    date_rows: list[tuple] - (date_str, parts_list) for each date row
    """
    headers = []
    rows = []
    for line in stdout.splitlines():
        if re.match(r"\|\s*date\s*\|", line, re.I):
            headers = [p.strip() for p in line.strip().strip("|").split("|")]
        elif re.match(r"\|\s*\d{4}-\d{2}-\d{2}", line) or re.match(r"\|\s*\d{4}[AE]?\s*\|", line):
            parts = [p.strip() for p in line.strip().strip("|").split("|")]
            if parts:
                rows.append((parts[0], parts))
    return headers, rows


def parse_latest_row(stdout: str) -> dict:
    """Parse mx-data table output and return the latest date row as a col→val dict.

    Returns {} if no rows found.
    """
    headers, rows = parse_stdout_table(stdout)
    if not rows:
        return {}

    # Pick the row with the most recent date
    rows.sort(key=lambda r: r[0])
    _, parts = rows[-1]

    col_map = {}
    for i, col in enumerate(headers):
        if i < len(parts):
            col_map[col] = parts[i]
    return col_map


def strip_unit(val: str) -> str:
    """Strip trailing unit suffix (亿/万/% etc.), return numeric part or original."""
    if not val or val == "-":
        return val
    m = re.search(r"([\-\d.]+)", val)
    if m:
        try:
            return f"{float(m.group(1)):.2f}"
        except ValueError:
            return val
    return val