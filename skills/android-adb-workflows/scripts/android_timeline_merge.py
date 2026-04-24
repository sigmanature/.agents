#!/usr/bin/env python3

import argparse
import json
import math
import re
import textwrap
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from html import escape
from pathlib import Path
from typing import Iterable, Optional


LOGCAT_RE = re.compile(
    r"^(?P<month>\d{2})-(?P<day>\d{2})\s+"
    r"(?P<time>\d{2}:\d{2}:\d{2}\.\d{3,6})\s+"
    r"(?P<pid>\d+)\s+(?P<tid>\d+)\s+"
    r"(?P<priority>[VDIWEAF])\s+"
    r"(?P<tag>.*?):\s(?P<message>.*)$"
)
DMESG_RE = re.compile(
    r"^\[(?P<stamp>[A-Z][a-z]{2}\s+[A-Z][a-z]{2}\s+\d{1,2}\s+"
    r"\d{2}:\d{2}:\d{2}\s+\d{4})\]\s*(?P<text>.*)$"
)
TRACE_RAW_RE = re.compile(
    r"^(?P<task>.+)-(?P<tid>\d+)\s+\[(?P<cpu>\d+)\]\s+\S+\s+"
    r"(?P<timestamp>\d+\.\d+):\s*(?P<text>.*)$"
)
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")
DMESG_PID_RE = re.compile(r"\bpid=(?P<pid>\d+)\b")
DMESG_INO_RE = re.compile(r"\bino=(?P<ino>\d+)\b")
DMESG_PATH_RE = re.compile(r"\bpath=(?P<path>\S+)")
DMESG_COMM_RE = re.compile(r"\bcomm=(?P<comm>\S+)")
TRACE_PATH_RE = re.compile(r'(?:"(?P<quoted>/[^"]+)"|path=(?P<path>\S+))')
TRACE_GENERIC_ARG_RE = re.compile(r"^arg\d+$")
TRACE_COMPACT_SYSCALLS = {"futex", "rt_sigreturn", "rt_sigprocmask"}


@dataclass(frozen=True)
class Event:
    source: str
    wall: datetime
    loc: str
    text: str
    raw_line: Optional[str] = None
    source_raw: Optional[dict] = None
    pid: Optional[int] = None
    tid: Optional[int] = None
    inode: Optional[int] = None
    task: Optional[str] = None
    tag: Optional[str] = None
    file_path: Optional[str] = None
    extra_paths: tuple[str, ...] = field(default_factory=tuple)

    def matches(
        self,
        pids: set[int],
        tids: set[int],
        inodes: set[int],
        path_substrings: list[str],
    ) -> bool:
        clauses: list[bool] = []
        if pids:
            candidate_ids = {value for value in (self.pid, self.tid) if value is not None}
            clauses.append(bool(candidate_ids) and not candidate_ids.isdisjoint(pids))
        if tids:
            clauses.append(self.tid is not None and self.tid in tids)
        if inodes:
            clauses.append(self.inode is not None and self.inode in inodes)
        if path_substrings:
            structured_paths = [item for item in ([self.file_path] + list(self.extra_paths)) if item]
            haystacks = structured_paths or ([self.text] if self.text else [])
            lowered = [item.lower() for item in haystacks if item]
            clauses.append(
                bool(lowered)
                and any(any(fragment in item for item in lowered) for fragment in path_substrings)
            )
        return any(clauses) if clauses else True

    def to_json(self) -> dict:
        payload = asdict(self)
        payload["wall"] = self.wall.isoformat(timespec="microseconds")
        return payload


@dataclass(frozen=True)
class FilterSpec:
    window_start: Optional[datetime]
    window_end: Optional[datetime]
    pids: set[int]
    tids: set[int]
    inodes: set[int]
    path_substrings: list[str]

    def accepts(self, event: Event) -> bool:
        if self.window_start and event.wall < self.window_start:
            return False
        if self.window_end and event.wall > self.window_end:
            return False
        return event.matches(self.pids, self.tids, self.inodes, self.path_substrings)


@dataclass(frozen=True)
class ParseReport:
    source: str
    path: str
    total_items: int
    matched_items: int
    skipped_items: int

    def to_json(self) -> dict:
        return asdict(self)


def iter_lines(path: Path) -> Iterable[tuple[int, str]]:
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            yield line_no, line.rstrip("\n")


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def parse_iso_wall(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"invalid ISO timestamp {value!r}; expected e.g. 2025-11-11T17:22:17.000"
        ) from exc


def parse_logcat(paths: list[Path], year: int, filters: FilterSpec) -> tuple[list[Event], list[ParseReport]]:
    events: list[Event] = []
    reports: list[ParseReport] = []
    for path in paths:
        total = 0
        matched = 0
        for line_no, line in iter_lines(path):
            total += 1
            clean = strip_ansi(line)
            match = LOGCAT_RE.match(clean)
            if not match:
                continue
            wall = datetime.strptime(
                f"{year}-{match.group('month')}-{match.group('day')} {match.group('time')}",
                "%Y-%m-%d %H:%M:%S.%f",
            )
            pid = int(match.group("pid"))
            tid = int(match.group("tid"))
            tag = match.group("tag").strip()
            message = match.group("message").strip()
            priority = match.group("priority")
            text = f"{tag} {priority} pid={pid} tid={tid} {message}"
            event = Event(
                source="logcat",
                wall=wall,
                loc=f"{path.name}:{line_no}",
                text=text,
                raw_line=clean,
                source_raw={
                    "raw_line": clean,
                    "month": match.group("month"),
                    "day": match.group("day"),
                    "time": match.group("time"),
                    "pid": pid,
                    "tid": tid,
                    "priority": priority,
                    "tag": tag,
                    "message": message,
                },
                pid=pid,
                tid=tid,
                tag=tag,
            )
            if filters.accepts(event):
                matched += 1
                events.append(event)
        reports.append(
            ParseReport(
                source="logcat",
                path=str(path),
                total_items=total,
                matched_items=matched,
                skipped_items=max(total - matched, 0),
            )
        )
    return events, reports


def parse_dmesg(paths: list[Path], filters: FilterSpec) -> tuple[list[Event], list[ParseReport]]:
    events: list[Event] = []
    reports: list[ParseReport] = []
    for path in paths:
        total = 0
        matched = 0
        for line_no, line in iter_lines(path):
            total += 1
            clean = strip_ansi(line)
            match = DMESG_RE.match(clean)
            if not match:
                continue
            wall = datetime.strptime(match.group("stamp"), "%a %b %d %H:%M:%S %Y")
            text = match.group("text").strip()
            pid_match = DMESG_PID_RE.search(text)
            ino_match = DMESG_INO_RE.search(text)
            path_match = DMESG_PATH_RE.search(text)
            comm_match = DMESG_COMM_RE.search(text)
            pid = int(pid_match.group("pid")) if pid_match else None
            inode = int(ino_match.group("ino")) if ino_match else None
            file_path = path_match.group("path") if path_match else None
            comm = comm_match.group("comm") if comm_match else None
            compact = text
            if comm and not compact.startswith(f"comm={comm}"):
                if pid is not None or inode is not None:
                    compact = f"pid={pid} ino={inode} comm={comm} {compact}"
                else:
                    compact = f"comm={comm} {compact}"
            event = Event(
                source="dmesg",
                wall=wall,
                loc=f"{path.name}:{line_no}",
                text=compact,
                raw_line=clean,
                source_raw={
                    "raw_line": clean,
                    "stamp": match.group("stamp"),
                    "text": text,
                    "pid": pid,
                    "inode": inode,
                    "path": file_path,
                    "comm": comm,
                },
                pid=pid,
                inode=inode,
                task=comm,
                file_path=file_path,
                extra_paths=(file_path,) if file_path else (),
            )
            if filters.accepts(event):
                matched += 1
                events.append(event)
        reports.append(
            ParseReport(
                source="dmesg",
                path=str(path),
                total_items=total,
                matched_items=matched,
                skipped_items=max(total - matched, 0),
            )
        )
    return events, reports


def render_trace_decoded_event(item: dict) -> tuple[str, tuple[str, ...]]:
    task = item.get("task")
    tid = item.get("tid")
    syscall = item.get("syscall")
    phase = item.get("phase")
    parts = [f"{task}-{tid}", f"{phase} {syscall}"]
    field_texts: list[str] = []
    extra_paths: list[str] = []
    compact_generic_args = syscall in TRACE_COMPACT_SYSCALLS
    for field in item.get("fields", []):
        name = field.get("name")
        display = field.get("display")
        value = field.get("value")
        if display is None:
            continue
        if compact_generic_args and isinstance(name, str) and TRACE_GENERIC_ARG_RE.fullmatch(name):
            continue
        field_texts.append(f"{name}={display}")
        if isinstance(value, str) and value.startswith("/"):
            extra_paths.append(value)
    if field_texts:
        parts.append(", ".join(field_texts))
    return_display = item.get("return_display")
    if return_display is not None:
        parts.append(f"ret={return_display}")
    annotations = item.get("annotations") or []
    if annotations:
        parts.append("; ".join(annotations))
    path_hints = item.get("path_hints") or {}
    for value in path_hints.values():
        if isinstance(value, str) and value.startswith("/"):
            extra_paths.append(value)
    return " ".join(parts), tuple(dict.fromkeys(extra_paths))


def parse_trace_json(
    paths: list[Path],
    anchor_mono: float,
    anchor_wall: datetime,
    filters: FilterSpec,
) -> tuple[list[Event], list[ParseReport]]:
    events: list[Event] = []
    reports: list[ParseReport] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            payload = json.load(handle)
        raw_events = payload["events"] if isinstance(payload, dict) else payload
        matched = 0
        for item in raw_events:
            mono = float(item["timestamp"])
            wall = anchor_wall + timedelta(seconds=mono - anchor_mono)
            text, extra_paths = render_trace_decoded_event(item)
            file_path = extra_paths[0] if extra_paths else None
            event = Event(
                source="syscall",
                wall=wall,
                loc=f"{path.name}:{item.get('line_no', '?')}",
                text=text,
                raw_line=item.get("raw_line"),
                source_raw=item,
                tid=item.get("tid"),
                task=item.get("task"),
                file_path=file_path,
                extra_paths=extra_paths,
            )
            if filters.accepts(event):
                matched += 1
                events.append(event)
        skipped = int(payload.get("skipped_lines", 0)) if isinstance(payload, dict) else 0
        reports.append(
            ParseReport(
                source="syscall",
                path=str(path),
                total_items=len(raw_events) + skipped,
                matched_items=matched,
                skipped_items=skipped,
            )
        )
    return events, reports


def parse_trace_raw(
    paths: list[Path],
    anchor_mono: float,
    anchor_wall: datetime,
    filters: FilterSpec,
) -> tuple[list[Event], list[ParseReport]]:
    events: list[Event] = []
    reports: list[ParseReport] = []
    for path in paths:
        total = 0
        matched = 0
        for line_no, line in iter_lines(path):
            total += 1
            clean = strip_ansi(line)
            match = TRACE_RAW_RE.match(clean)
            if not match:
                continue
            mono = float(match.group("timestamp"))
            wall = anchor_wall + timedelta(seconds=mono - anchor_mono)
            tid = int(match.group("tid"))
            task = match.group("task").strip()
            text = match.group("text").strip()
            path_hits = []
            for path_match in TRACE_PATH_RE.finditer(text):
                chosen = path_match.group("quoted") or path_match.group("path")
                if chosen:
                    path_hits.append(chosen)
            file_path = path_hits[0] if path_hits else None
            event = Event(
                source="syscall",
                wall=wall,
                loc=f"{path.name}:{line_no}",
                text=f"{task}-{tid} {text}",
                raw_line=clean,
                source_raw={
                    "raw_line": clean,
                    "task": task,
                    "tid": tid,
                    "cpu": int(match.group("cpu")),
                    "timestamp": mono,
                    "text": text,
                    "path_hints": path_hits,
                },
                tid=tid,
                task=task,
                file_path=file_path,
                extra_paths=tuple(path_hits),
            )
            if filters.accepts(event):
                matched += 1
                events.append(event)
        reports.append(
            ParseReport(
                source="syscall",
                path=str(path),
                total_items=total,
                matched_items=matched,
                skipped_items=max(total - matched, 0),
            )
        )
    return events, reports


def parse_trace(
    paths: list[Path],
    anchor_mono: float,
    anchor_wall: datetime,
    filters: FilterSpec,
) -> tuple[list[Event], list[ParseReport]]:
    events: list[Event] = []
    reports: list[ParseReport] = []
    for path in paths:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            prefix = handle.read(256)
        leading = prefix.lstrip("\ufeff\r\n\t ")
        if leading.startswith("{") or leading.startswith("["):
            path_events, path_reports = parse_trace_json([path], anchor_mono, anchor_wall, filters)
        else:
            path_events, path_reports = parse_trace_raw([path], anchor_mono, anchor_wall, filters)
        events.extend(path_events)
        reports.extend(path_reports)
    return events, reports


def bucketize(
    events: list[Event],
    bucket_ms: int,
    window_start: Optional[datetime],
) -> list[tuple[int, datetime, dict[str, list[Event]]]]:
    if not events:
        return []
    origin = window_start or min(item.wall for item in events)
    buckets: dict[int, dict[str, list[Event]]] = {}
    for event in sorted(events, key=lambda item: (item.wall, item.source, item.loc)):
        delta_ms = (event.wall - origin).total_seconds() * 1000.0
        bucket = int(math.floor(delta_ms / bucket_ms))
        source_map = buckets.setdefault(bucket, {"logcat": [], "dmesg": [], "syscall": []})
        source_map[event.source].append(event)
    rows: list[tuple[int, datetime, dict[str, list[Event]]]] = []
    for bucket in sorted(buckets):
        bucket_wall = origin + timedelta(milliseconds=bucket * bucket_ms)
        rows.append((bucket, bucket_wall, buckets[bucket]))
    return rows


def render_entries(entries: list[Event], width: int, max_events: int) -> list[str]:
    rendered: list[str] = []
    trimmed = entries if max_events <= 0 else entries[:max_events]
    for event in trimmed:
        body = f"{event.loc} {event.text}"
        wrapped = textwrap.wrap(
            body,
            width=width,
            initial_indent="- ",
            subsequent_indent="  ",
            break_long_words=False,
            break_on_hyphens=False,
        )
        rendered.extend(wrapped or ["-"])
    remaining = len(entries) - len(trimmed)
    if remaining > 0:
        rendered.extend(
            textwrap.wrap(
                f"... (+{remaining} more)",
                width=width,
                initial_indent="  ",
                subsequent_indent="  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    return rendered or [""]


def render_table(
    rows: list[tuple[int, datetime, dict[str, list[Event]]]],
    col_width: int,
    max_events: int,
) -> str:
    time_width = 23
    columns = ("logcat", "dmesg", "syscall")
    header = (
        f"{'Wall':<{time_width}} | "
        f"{'Logcat':<{col_width}} | "
        f"{'Dmesg':<{col_width}} | "
        f"{'Syscall':<{col_width}}"
    )
    separator = (
        f"{'-' * time_width}-+-"
        f"{'-' * col_width}-+-"
        f"{'-' * col_width}-+-"
        f"{'-' * col_width}"
    )
    lines = [header, separator]
    for _, bucket_wall, source_map in rows:
        cells = {
            name: render_entries(source_map.get(name, []), col_width, max_events)
            for name in columns
        }
        height = max(len(cell_lines) for cell_lines in cells.values())
        for line_index in range(height):
            time_text = bucket_wall.isoformat(timespec="milliseconds") if line_index == 0 else ""
            logcat_text = cells["logcat"][line_index] if line_index < len(cells["logcat"]) else ""
            dmesg_text = cells["dmesg"][line_index] if line_index < len(cells["dmesg"]) else ""
            syscall_text = cells["syscall"][line_index] if line_index < len(cells["syscall"]) else ""
            lines.append(
                f"{time_text:<{time_width}} | "
                f"{logcat_text:<{col_width}} | "
                f"{dmesg_text:<{col_width}} | "
                f"{syscall_text:<{col_width}}"
            )
        lines.append(separator)
    return "\n".join(lines)


def shorten_text(text: str, limit: int = 140) -> str:
    compact = " ".join(text.split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1] + "…"


def shorten_path(path: str, keep_parts: int = 4) -> str:
    parts = [part for part in path.split("/") if part]
    if len(parts) <= keep_parts:
        return "/" + "/".join(parts)
    return ".../" + "/".join(parts[-keep_parts:])


def summarize_event(event: Event) -> str:
    if event.source == "logcat":
        message = event.source_raw.get("message") if event.source_raw else event.text
        prefix = event.tag or "logcat"
        ids = []
        if event.pid is not None:
            ids.append(f"pid={event.pid}")
        if event.tid is not None:
            ids.append(f"tid={event.tid}")
        suffix = f" ({' '.join(ids)})" if ids else ""
        return f"{prefix}{suffix}: {shorten_text(message, 150)}"

    if event.source == "dmesg":
        pieces = []
        if event.pid is not None:
            pieces.append(f"pid={event.pid}")
        if event.inode is not None:
            pieces.append(f"ino={event.inode}")
        if event.file_path:
            pieces.append(shorten_path(event.file_path))
        pieces.append(shorten_text(event.text, 150))
        return " ".join(pieces)

    pieces = []
    if event.task:
        pieces.append(event.task)
    if event.tid is not None:
        pieces.append(f"tid={event.tid}")
    if event.file_path:
        pieces.append(shorten_path(event.file_path))
    pieces.append(shorten_text(event.text, 150))
    return " ".join(pieces)


def score_event_for_summary(event: Event) -> int:
    text = (event.text or "").lower()
    score = 0
    keyword_weights = {
        "fatal exception": 10,
        "classnotfoundexception": 9,
        "noclassdeffounderror": 9,
        "am_crash": 9,
        "opened fds": 8,
        "running dex2oat": 8,
        "artd": 7,
        "dex2oat": 7,
        "base.vdex": 7,
        "base.odex": 7,
        "wait_wb_begin": 6,
        "wait_wb_end": 6,
        "mmap_prepare": 6,
        "filemap_fault": 6,
        "readahead": 5,
        "clear_and_dec": 5,
        "ret=-": 8,
        " = -": 6,
    }
    for needle, weight in keyword_weights.items():
        if needle in text:
            score += weight
    if event.file_path:
        score += 2
    if event.source == "logcat" and event.tag in {"AndroidRuntime", "artd", "ArtService", "am_crash"}:
        score += 4
    return score


def build_row_summary(source_map: dict[str, list[Event]]) -> dict:
    counts = {source: len(source_map.get(source, [])) for source in ("logcat", "dmesg", "syscall")}
    headline_parts = [f"{count} {source}" for source, count in counts.items() if count]
    headline = ", ".join(headline_parts) + " event(s)" if headline_parts else "no events"
    highlights: list[str] = []
    for source in ("logcat", "dmesg", "syscall"):
        events = source_map.get(source, [])
        if not events:
            continue
        best = max(
            events,
            key=lambda event: (
                score_event_for_summary(event),
                -int(event.wall.timestamp() * 1_000_000),
            ),
        )
        highlights.append(f"{source}: {summarize_event(best)}")
    return {
        "headline": headline,
        "counts": counts,
        "highlights": highlights,
    }


def build_raw_payload(
    rows: list[tuple[int, datetime, dict[str, list[Event]]]],
    reports: list[ParseReport],
    args: argparse.Namespace,
) -> dict:
    payload_rows = []
    for bucket_index, bucket_wall, source_map in rows:
        payload_rows.append(
            {
                "bucket_index": bucket_index,
                "bucket_wall": bucket_wall.isoformat(timespec="milliseconds"),
                "summary": build_row_summary(source_map),
                "cells": {
                    source: [event.to_json() for event in source_map.get(source, [])]
                    for source in ("logcat", "dmesg", "syscall")
                },
            }
        )

    report_json = [report.to_json() for report in reports]
    trace_skipped_lines = sum(
        report["skipped_items"] for report in report_json if report["source"] == "syscall"
    )
    return {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "bucket_ms": args.bucket_ms,
            "filters": {
                "pid": args.pid,
                "tid": args.tid,
                "inode": args.inode,
                "path_substr": args.path_substr,
                "window_start": args.window_start.isoformat() if args.window_start else None,
                "window_end": args.window_end.isoformat() if args.window_end else None,
            },
            "warnings": {
                "trace_skipped_lines": trace_skipped_lines,
            },
            "parse_reports": report_json,
        },
        "rows": payload_rows,
    }


def render_html(payload: dict, title: str) -> str:
    payload_json = (
        json.dumps(payload, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )
    safe_title = escape(title)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{safe_title}</title>
  <style>
    body {{ font-family: Arial, sans-serif; margin: 0; padding: 16px; background: #111827; color: #e5e7eb; }}
    h1, h2 {{ margin: 0 0 12px 0; }}
    .meta {{ margin-bottom: 16px; color: #cbd5e1; }}
    table {{ width: 100%; border-collapse: collapse; table-layout: fixed; }}
    th, td {{ border: 1px solid #334155; vertical-align: top; padding: 8px; }}
    th {{ position: sticky; top: 0; background: #1e293b; z-index: 1; }}
    .wall {{ width: 180px; font-family: monospace; color: #93c5fd; }}
    .cell-summary {{ color: #fbbf24; margin-bottom: 6px; }}
    .row-summary {{ margin-bottom: 8px; padding: 8px; background: #0f172a; border: 1px solid #334155; border-radius: 6px; }}
    .row-summary-headline {{ color: #f8fafc; font-weight: 600; margin-bottom: 6px; }}
    .row-summary ul {{ margin: 0; padding-left: 18px; }}
    .row-summary li {{ margin: 4px 0; color: #cbd5e1; }}
    details {{ margin-bottom: 6px; }}
    summary {{ cursor: pointer; }}
    pre {{ white-space: pre-wrap; word-break: break-word; margin: 6px 0 0 0; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; }}
    .empty {{ color: #64748b; }}
    .warning {{ color: #fca5a5; }}
  </style>
</head>
<body>
  <h1>{safe_title}</h1>
  <div class="meta" id="meta"></div>
  <table id="timeline">
    <thead>
      <tr>
        <th class="wall">Wall</th>
        <th>Logcat</th>
        <th>Dmesg</th>
        <th>Syscall</th>
      </tr>
    </thead>
    <tbody></tbody>
  </table>
  <script id="payload-data" type="application/json">{payload_json}</script>
  <script>
    const payload = JSON.parse(document.getElementById("payload-data").textContent);
    const meta = document.getElementById("meta");
    const tbody = document.querySelector("#timeline tbody");
    meta.innerHTML = `
      <div>bucket_ms=${{payload.meta.bucket_ms}}</div>
      <div class="${'{'}payload.meta.warnings.trace_skipped_lines ? 'warning' : ''{'}'}">trace_skipped_lines=${{payload.meta.warnings.trace_skipped_lines}}</div>
    `;

    function renderEvent(event) {{
      const details = document.createElement("details");
      const summary = document.createElement("summary");
      summary.textContent = `${{event.loc}} — ${{event.text}}`;
      const pre = document.createElement("pre");
      pre.textContent = JSON.stringify(event, null, 2);
      details.appendChild(summary);
      details.appendChild(pre);
      return details;
    }}

    function renderCell(events) {{
      const cell = document.createElement("td");
      if (!events.length) {{
        const empty = document.createElement("div");
        empty.className = "empty";
        empty.textContent = "—";
        cell.appendChild(empty);
        return cell;
      }}
      const summary = document.createElement("div");
      summary.className = "cell-summary";
      summary.textContent = `${{events.length}} event(s)`;
      cell.appendChild(summary);
      for (const event of events) {{
        cell.appendChild(renderEvent(event));
      }}
      return cell;
    }}

    function renderRowSummary(summary) {{
      const box = document.createElement("div");
      box.className = "row-summary";
      const headline = document.createElement("div");
      headline.className = "row-summary-headline";
      headline.textContent = summary.headline;
      box.appendChild(headline);
      if (summary.highlights && summary.highlights.length) {{
        const list = document.createElement("ul");
        for (const item of summary.highlights) {{
          const li = document.createElement("li");
          li.textContent = item;
          list.appendChild(li);
        }}
        box.appendChild(list);
      }}
      return box;
    }}

    for (const row of payload.rows) {{
      const tr = document.createElement("tr");
      const wall = document.createElement("td");
      wall.className = "wall";
      wall.textContent = row.bucket_wall;
      tr.appendChild(wall);
      const logcatCell = renderCell(row.cells.logcat);
      logcatCell.prepend(renderRowSummary(row.summary));
      tr.appendChild(logcatCell);
      tr.appendChild(renderCell(row.cells.dmesg));
      tr.appendChild(renderCell(row.cells.syscall));
      tbody.appendChild(tr);
    }}
  </script>
</body>
</html>
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Render one wall-clock window as a three-column table: logcat | dmesg | syscall. "
            "Supports pid/tid/inode/path filtering before alignment, plus raw JSON/HTML export."
        )
    )
    parser.add_argument("--logcat", action="append", default=[], type=Path, help="Path to logcat -v threadtime file. May be repeated.")
    parser.add_argument("--dmesg", action="append", default=[], type=Path, help="Path to bracketed dmesg file. May be repeated.")
    parser.add_argument(
        "--trace",
        action="append",
        default=[],
        type=Path,
        help="Path to tracefs decoded JSON from tracefs_syscall_decode.py --json, or raw trace text. May be repeated.",
    )
    parser.add_argument("--trace-anchor-monotonic", type=float, help="Trace monotonic seconds that correspond to --trace-anchor-wall.")
    parser.add_argument("--trace-anchor-wall", type=parse_iso_wall, help="Wall-clock timestamp for --trace-anchor-monotonic.")
    parser.add_argument("--year", type=int, help="Calendar year for logcat lines when no trace anchor wall is available.")
    parser.add_argument("--window-start", type=parse_iso_wall, help="Inclusive wall-clock window start in ISO format.")
    parser.add_argument("--window-end", type=parse_iso_wall, help="Inclusive wall-clock window end in ISO format.")
    parser.add_argument("--bucket-ms", type=int, default=1000, help="Bucket size in milliseconds for row alignment. Default: 1000.")
    parser.add_argument("--pid", action="append", default=[], type=int, help="Keep only events with matching pid. May be repeated.")
    parser.add_argument("--tid", action="append", default=[], type=int, help="Keep only events with matching tid. May be repeated.")
    parser.add_argument("--inode", action="append", default=[], type=int, help="Keep only events with matching inode. May be repeated.")
    parser.add_argument("--path-substr", action="append", default=[], help="Keep only events whose path/text contains this substring. May be repeated.")
    parser.add_argument("--col-width", type=int, default=72, help="Maximum width for each source column.")
    parser.add_argument("--max-events-per-cell", type=int, default=0, help="Maximum event count rendered inside one text-table cell before truncation. 0 means unlimited.")
    parser.add_argument("--raw-json-out", type=Path, help="Write the lossless merged raw dataset to JSON.")
    parser.add_argument("--html-out", type=Path, help="Write an interactive HTML three-column view.")
    parser.add_argument("--html-title", default="Android Timeline Merge", help="Title used for --html-out.")
    parser.add_argument("--table-out", type=Path, help="Write the plain-text three-column table to a file.")
    parser.add_argument("--no-stdout-table", action="store_true", help="Do not print the plain-text table to stdout.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if not (args.logcat or args.dmesg or args.trace):
        parser.error("provide at least one of --logcat, --dmesg, or --trace")
    if args.trace and ((args.trace_anchor_monotonic is None) != (args.trace_anchor_wall is None)):
        parser.error("--trace requires both --trace-anchor-monotonic and --trace-anchor-wall")
    if args.logcat and args.year is None:
        if args.trace_anchor_wall is not None:
            year = args.trace_anchor_wall.year
        else:
            parser.error("--logcat without --trace-anchor-wall requires --year")
    else:
        year = args.year
    if args.no_stdout_table and args.table_out is None and args.raw_json_out is None and args.html_out is None:
        parser.error("no output selected: use stdout table or pass --table-out/--raw-json-out/--html-out")

    filters = FilterSpec(
        window_start=args.window_start,
        window_end=args.window_end,
        pids=set(args.pid),
        tids=set(args.tid),
        inodes=set(args.inode),
        path_substrings=[item.lower() for item in args.path_substr],
    )

    events: list[Event] = []
    reports: list[ParseReport] = []
    if args.logcat:
        path_events, path_reports = parse_logcat(args.logcat, year, filters)
        events.extend(path_events)
        reports.extend(path_reports)
    if args.dmesg:
        path_events, path_reports = parse_dmesg(args.dmesg, filters)
        events.extend(path_events)
        reports.extend(path_reports)
    if args.trace:
        path_events, path_reports = parse_trace(args.trace, args.trace_anchor_monotonic, args.trace_anchor_wall, filters)
        events.extend(path_events)
        reports.extend(path_reports)

    rows = bucketize(events, args.bucket_ms, args.window_start)
    table = render_table(rows, args.col_width, args.max_events_per_cell)
    payload = build_raw_payload(rows, reports, args)

    if args.raw_json_out is not None:
        args.raw_json_out.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")
    if args.html_out is not None:
        args.html_out.write_text(render_html(payload, args.html_title), encoding="utf-8")
    if args.table_out is not None:
        args.table_out.write_text(table + "\n", encoding="utf-8")
    if not args.no_stdout_table:
        print(table)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
