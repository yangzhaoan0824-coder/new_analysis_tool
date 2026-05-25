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

    mx-data writes output to ~/.openclaw/workspace/mx_data/output/mx_data_{safe}_raw.json
    where safe = query_str with spaces replaced by underscores.

    Returns the path, or "" if no file found within max_age seconds.
    """
    safe = query_str.replace(" ", "_")
    pattern = os.path.expanduser(f"~/.openclaw/workspace/mx_data/output/mx_data_{safe}_raw.json")
    files = _glob.glob(pattern)
    if not files:
        return ""
    latest = max(files, key=os.path.getmtime)
    if os.path.getmtime(latest) < time.time() - max_age:
        return ""
    return latest


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
