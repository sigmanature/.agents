#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
agentctl: job registry + close gate + narrow auditor

No external dependencies. Designed for repo-local governance enforcement.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import errno
import json
import os
import pathlib
import random
import shlex
import subprocess
import sys
import textwrap
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = pathlib.Path(os.getcwd())

DEFAULT_WORKLOG_DIR = REPO_ROOT / "worklog"
DEFAULT_WORKLOG_JSONL = DEFAULT_WORKLOG_DIR / "governance.jsonl"
DEFAULT_WORKLOG_NOTES = DEFAULT_WORKLOG_DIR / "governance-notes.md"

RUNTIME_DIR = REPO_ROOT / ".agent"
STATE_PATH = RUNTIME_DIR / "state.json"
JOBS_PATH = RUNTIME_DIR / "jobs.json"
JOBS_DIR = RUNTIME_DIR / "jobs"
RUNS_DIR = RUNTIME_DIR / "runs"

TERMINAL_WORKLOG_STATUSES = {"discarded", "deferred", "promoted"}

def utc_now() -> str:
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

def ensure_dirs() -> None:
    DEFAULT_WORKLOG_DIR.mkdir(parents=True, exist_ok=True)
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)

def load_json(path: pathlib.Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def save_json(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

def append_jsonl(path: pathlib.Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def read_jsonl(path: pathlib.Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    items: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except Exception:
            # keep parsing; bad lines become warnings in audit
            items.append({"_parse_error": True, "raw": line})
    return items

def write_jsonl(path: pathlib.Path, items: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for it in items:
            f.write(json.dumps(it, ensure_ascii=False) + "\n")

def gen_id(prefix: str) -> str:
    # sortable-ish ID: YYYYMMDDHHMMSS + 6 random
    ts = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S")
    rand = "".join(random.choice("abcdefghijklmnopqrstuvwxyz0123456789") for _ in range(6))
    return f"{prefix}_{ts}_{rand}"

def update_state(**kwargs: Any) -> Dict[str, Any]:
    ensure_dirs()
    st = load_json(STATE_PATH, {})
    st["updated_at"] = utc_now()
    for k, v in kwargs.items():
        st[k] = v
    save_json(STATE_PATH, st)
    return st

def load_jobs() -> Dict[str, Any]:
    ensure_dirs()
    return load_json(JOBS_PATH, {"jobs": {}})

def save_jobs(j: Dict[str, Any]) -> None:
    ensure_dirs()
    save_json(JOBS_PATH, j)

def pid_alive(pid: int) -> bool:
    # POSIX best-effort: os.kill(pid, 0)
    try:
        os.kill(pid, 0)
    except OSError as e:
        if e.errno in (errno.ESRCH,):
            return False
        # EPERM means it exists but no permission
        return True
    else:
        return True

def run_cmd_capture(cmd: List[str], log_path: pathlib.Path, cwd: Optional[str]=None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as lf:
        p = subprocess.Popen(
            cmd,
            cwd=cwd or None,
            stdout=lf,
            stderr=subprocess.STDOUT,
        )
        return p.wait()

def start_cmd_background(cmd: List[str], log_path: pathlib.Path, cwd: Optional[str]=None) -> int:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    # Start process, redirect output. Detach session on POSIX.
    kwargs = {}
    if hasattr(os, "setsid"):
        kwargs["preexec_fn"] = os.setsid
    with log_path.open("wb") as lf:
        p = subprocess.Popen(
            cmd,
            cwd=cwd or None,
            stdout=lf,
            stderr=subprocess.STDOUT,
            **kwargs,
        )
    return p.pid

def cmd_init(_: argparse.Namespace) -> int:
    ensure_dirs()
    DEFAULT_WORKLOG_DIR.mkdir(parents=True, exist_ok=True)
    DEFAULT_WORKLOG_JSONL.touch(exist_ok=True)
    DEFAULT_WORKLOG_NOTES.touch(exist_ok=True)
    # initialize runtime files
    if not JOBS_PATH.exists():
        save_jobs({"jobs": {}})
    if not STATE_PATH.exists():
        update_state(created_at=utc_now(), last_progress_at=utc_now())
    print("Initialized agent governance directories:")
    print(f"- worklog: {DEFAULT_WORKLOG_DIR}")
    print(f"- runtime: {RUNTIME_DIR} (should be gitignored)")
    return 0

def cmd_capture(args: argparse.Namespace) -> int:
    ensure_dirs()
    wid = gen_id("wl")
    entry = {
        "id": wid,
        "ts": utc_now(),
        "kind": args.kind,
        "summary": args.summary,
        "details": args.details or "",
        "reusable": (args.reusable.lower() in ("y", "yes", "true", "1")),
        "status": "open",
        "outcome": None,  # to be set by triage
        "promoted_paths": [],
        "defer_reason": "",
        "tags": [t for t in (args.tags or "").split(",") if t.strip()],
        "meta": {},
    }
    append_jsonl(DEFAULT_WORKLOG_JSONL, entry)
    update_state(last_progress_at=utc_now(), last_worklog_id=wid)
    print(wid)
    return 0

def cmd_run(args: argparse.Namespace) -> int:
    ensure_dirs()
    tag = args.tag or "run"
    ts = dt.datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    log_path = RUNS_DIR / f"{ts}_{tag}.log"
    cmd = args.command
    print(f"[agentctl] running: {shlex.join(cmd)}")
    print(f"[agentctl] log: {log_path}")
    rc = run_cmd_capture(cmd, log_path, cwd=args.cwd)
    update_state(last_progress_at=utc_now(), last_run_log=str(log_path), last_run_rc=rc)

    # Auto-capture failed attempts (or capture-always)
    if rc != 0 or args.capture_always:
        wid = gen_id("wl")
        entry = {
            "id": wid,
            "ts": utc_now(),
            "kind": "command_attempt",
            "summary": f"Command {'failed' if rc!=0 else 'ran'}: {tag}",
            "details": "",
            "reusable": True if rc != 0 else False,
            "status": "open",
            "outcome": None,
            "promoted_paths": [],
            "defer_reason": "",
            "tags": ["auto", "run", tag],
            "meta": {
                "cmd": cmd,
                "cwd": args.cwd or "",
                "exit_code": rc,
                "log_path": str(log_path),
            },
        }
        append_jsonl(DEFAULT_WORKLOG_JSONL, entry)
        update_state(last_worklog_id=wid)
        print(f"[agentctl] captured worklog item: {wid}")

    return rc

def cmd_start(args: argparse.Namespace) -> int:
    ensure_dirs()
    job_id = gen_id("job")
    tag = args.tag or "job"
    log_path = JOBS_DIR / f"{job_id}_{tag}.log"
    pid = start_cmd_background(args.command, log_path, cwd=args.cwd)

    reg = load_jobs()
    reg["jobs"][job_id] = {
        "id": job_id,
        "tag": tag,
        "cmd": args.command,
        "cwd": args.cwd or "",
        "pid": pid,
        "log_path": str(log_path),
        "status": "running",
        "started_at": utc_now(),
        "finished_at": None,
        "exit_code": None,
        "consumed_at": None,
        "notes": "",
    }
    save_jobs(reg)
    update_state(last_progress_at=utc_now(), last_job_id=job_id)
    print(job_id)
    return 0

def cmd_poll(_: argparse.Namespace) -> int:
    ensure_dirs()
    reg = load_jobs()
    changed = 0
    for job_id, job in reg.get("jobs", {}).items():
        if job.get("status") != "running":
            continue
        pid = job.get("pid")
        if not isinstance(pid, int):
            continue
        alive = pid_alive(pid)
        if alive:
            continue
        # process ended, but we may not know exit code without psutil.
        # We best-effort infer exit code by checking last line for "Exit code:" if user prints it,
        # otherwise set to None and require manual consume note.
        job["status"] = "finished"
        job["finished_at"] = utc_now()
        job["exit_code"] = job.get("exit_code")  # may stay None
        changed += 1
    if changed:
        save_jobs(reg)
    update_state(last_progress_at=utc_now(), last_poll_at=utc_now())
    # Print summary
    jobs = list(reg.get("jobs", {}).values())
    running = [j for j in jobs if j.get("status") == "running"]
    finished = [j for j in jobs if j.get("status") == "finished"]
    consumed = [j for j in jobs if j.get("status") == "consumed"]
    print(json.dumps({
        "running": [{"id": j["id"], "tag": j.get("tag"), "pid": j.get("pid")} for j in running],
        "finished": [{"id": j["id"], "tag": j.get("tag"), "log_path": j.get("log_path")} for j in finished],
        "consumed": [{"id": j["id"], "tag": j.get("tag")} for j in consumed],
    }, ensure_ascii=False, indent=2))
    return 0

def tail_log(path: pathlib.Path, max_bytes: int = 16_000) -> str:
    try:
        data = path.read_bytes()
    except Exception:
        return ""
    if len(data) <= max_bytes:
        return data.decode("utf-8", errors="replace")
    return data[-max_bytes:].decode("utf-8", errors="replace")

def cmd_consume(args: argparse.Namespace) -> int:
    ensure_dirs()
    job_id = args.job_id
    reg = load_jobs()
    job = reg.get("jobs", {}).get(job_id)
    if not job:
        print(f"Unknown job_id: {job_id}", file=sys.stderr)
        return 2
    if job.get("status") == "running":
        print(f"Job still running: {job_id}", file=sys.stderr)
        return 3

    log_path = pathlib.Path(job.get("log_path", ""))
    tail = tail_log(log_path)
    # Heuristic extract
    lines = [ln for ln in tail.splitlines() if ln.strip()]
    interesting = []
    for ln in lines[-200:]:
        low = ln.lower()
        if "error" in low or "failed" in low or "exception" in low:
            interesting.append(ln)
    summary = {
        "job_id": job_id,
        "tag": job.get("tag"),
        "status": job.get("status"),
        "log_path": str(log_path),
        "tail_excerpt": "\n".join(lines[-40:]),
        "signal_lines": "\n".join(interesting[-20:]),
        "note": args.note or "",
    }
    # Mark consumed
    job["status"] = "consumed"
    job["consumed_at"] = utc_now()
    if args.exit_code is not None:
        job["exit_code"] = int(args.exit_code)
    if args.note:
        job["notes"] = args.note
    save_jobs(reg)
    update_state(last_progress_at=utc_now(), last_consumed_job=job_id)

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0

def cmd_triage(args: argparse.Namespace) -> int:
    ensure_dirs()
    wid = args.worklog_id
    items = read_jsonl(DEFAULT_WORKLOG_JSONL)
    found = False
    for it in items:
        if it.get("id") != wid:
            continue
        found = True
        outcome = args.outcome
        it["outcome"] = outcome
        if outcome == "discard":
            it["status"] = "discarded"
            it["promoted_paths"] = []
            it["defer_reason"] = ""
        elif outcome == "defer":
            if not args.reason:
                print("defer requires --reason", file=sys.stderr)
                return 2
            it["status"] = "deferred"
            it["defer_reason"] = args.reason
            it["promoted_paths"] = []
        else:
            # reference/script/skill/other
            promoted = args.promoted or []
            if not promoted:
                print(f"{outcome} requires at least one --promoted <path>", file=sys.stderr)
                return 2
            it["status"] = "promoted"
            it["promoted_paths"] = promoted
            it["defer_reason"] = ""
            if args.owner_skill:
                it.setdefault("meta", {})
                it["meta"]["owner_skill"] = args.owner_skill

        it.setdefault("meta", {})
        it["meta"]["triaged_at"] = utc_now()
        if args.note:
            it["meta"]["triage_note"] = args.note

    if not found:
        print(f"Unknown worklog_id: {wid}", file=sys.stderr)
        return 3

    write_jsonl(DEFAULT_WORKLOG_JSONL, items)
    update_state(last_progress_at=utc_now(), last_triaged_worklog=wid)
    print("OK")
    return 0

def close_gate_checks() -> Tuple[bool, List[Dict[str, Any]]]:
    """
    Returns (ok, problems[])
    problems: list of dicts with {code, message, fix}
    """
    problems: List[Dict[str, Any]] = []

    # worklog
    items = read_jsonl(DEFAULT_WORKLOG_JSONL)
    for it in items:
        if it.get("_parse_error"):
            problems.append({
                "code": "worklog_parse_error",
                "message": "Worklog contains an unparsable JSONL line.",
                "fix": "Fix or remove the invalid line in worklog/governance.jsonl."
            })
            continue
        status = it.get("status")
        if status in TERMINAL_WORKLOG_STATUSES:
            # validate terminal
            if status == "deferred" and not (it.get("defer_reason") or "").strip():
                problems.append({
                    "code": "worklog_defer_missing_reason",
                    "message": f"Deferred worklog item missing defer_reason: {it.get('id')}",
                    "fix": f"Re-triage: agentctl triage {it.get('id')} --outcome defer --reason '<why deferred>'"
                })
            if status == "promoted" and not it.get("promoted_paths"):
                problems.append({
                    "code": "worklog_promoted_missing_paths",
                    "message": f"Promoted worklog item missing promoted_paths: {it.get('id')}",
                    "fix": f"Re-triage with promoted paths: agentctl triage {it.get('id')} --outcome reference --promoted references/.."
                })
            continue

        # Any non-terminal item blocks close
        problems.append({
            "code": "worklog_unclosed_item",
            "message": f"Worklog item not in terminal state: {it.get('id')} (status={status})",
            "fix": f"Triage to discard/defer/promote: agentctl triage {it.get('id')} --outcome <discard|defer|reference|script|skill> ..."
        })

    # jobs
    reg = load_jobs()
    for job_id, job in reg.get("jobs", {}).items():
        st = job.get("status")
        if st == "running":
            problems.append({
                "code": "job_running",
                "message": f"Background job still running: {job_id} (tag={job.get('tag')}, pid={job.get('pid')})",
                "fix": "Either wait and poll, or stop it, then poll again."
            })
        if st == "finished":
            problems.append({
                "code": "job_unconsumed",
                "message": f"Background job finished but not consumed: {job_id} (tag={job.get('tag')})",
                "fix": f"Consume it: agentctl consume {job_id} --note '<what happened + next action>'"
            })

    ok = len(problems) == 0
    return ok, problems

def cmd_close(_: argparse.Namespace) -> int:
    ensure_dirs()
    ok, problems = close_gate_checks()
    report = {
        "ok": ok,
        "checked_at": utc_now(),
        "problems": problems,
    }
    update_state(last_close_at=utc_now(), last_close_ok=ok)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if ok else 1

def git_status_summary() -> Dict[str, Any]:
    # best-effort; no error if git absent
    try:
        p = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, check=False)
        if p.returncode != 0:
            return {"available": False}
        lines = [ln for ln in p.stdout.splitlines() if ln.strip()]
        return {"available": True, "dirty": len(lines) > 0, "changed": lines[:50], "changed_count": len(lines)}
    except Exception:
        return {"available": False}

def cmd_audit(args: argparse.Namespace) -> int:
    ensure_dirs()
    ok, problems = close_gate_checks()

    st = load_json(STATE_PATH, {})
    gs = git_status_summary()

    # Stall heuristic (optional): if there are problems and no progress for N minutes.
    stall_minutes = args.stall_minutes
    stalled = False
    last_prog = st.get("last_progress_at")
    if problems and last_prog:
        try:
            lp = dt.datetime.fromisoformat(last_prog.replace("Z", ""))
            delta = (dt.datetime.utcnow() - lp).total_seconds() / 60.0
            stalled = delta >= stall_minutes
        except Exception:
            stalled = False

    verdict = "PASS" if ok else "FAIL"
    if stalled and not ok:
        verdict = "FAIL_STALLED"
        problems.append({
            "code": "stalled_no_progress",
            "message": f"No recorded progress for >= {stall_minutes} minutes while gates are failing.",
            "fix": "Resume execution: poll jobs, consume results, triage/promote worklog items, then re-run close."
        })

    fix_instructions = []
    for pr in problems:
        fix_instructions.append(f"- {pr.get('code')}: {pr.get('fix')}")

    report = {
        "verdict": verdict,
        "checked_at": utc_now(),
        "close_gate_ok": ok,
        "problems": problems,
        "git": gs,
        "state_hint": {
            "last_progress_at": st.get("last_progress_at"),
            "last_job_id": st.get("last_job_id"),
            "last_worklog_id": st.get("last_worklog_id"),
            "last_run_log": st.get("last_run_log"),
        },
        "fix_instructions": "\n".join(fix_instructions) if fix_instructions else "No action needed.",
    }

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[AUDIT] {verdict}")
        if gs.get("available"):
            print(f"[AUDIT] git dirty: {gs.get('dirty')} (changed={gs.get('changed_count')})")
        if problems:
            print("[AUDIT] problems:")
            for pr in problems:
                print(f"  - {pr.get('code')}: {pr.get('message')}")
                print(f"    fix: {pr.get('fix')}")
        else:
            print("[AUDIT] no problems.")
        if report["fix_instructions"]:
            print("\n[Paste back to main agent as next steps]\n" + report["fix_instructions"])

    update_state(last_audit_at=utc_now(), last_audit_verdict=verdict)
    return 0 if verdict == "PASS" else 2

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="agentctl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=textwrap.dedent("""\
        agentctl: job registry + close gate + narrow auditor

        Commands:
          init        Create required folders/files.
          run         Run a foreground command; auto-capture failures into worklog.
          start       Start a background job and register it.
          poll        Poll all running jobs; marks ended ones as finished.
          consume     Mark a finished job as consumed and emit a summary blob.
          capture     Manually capture a trial-and-error item into worklog.
          triage      Close a worklog item (discard/defer/promote).
          close       Hard gate: fails if worklog/jobs are not closed.
          audit       Narrow auditor report (good to feed back to the agent).
        """)
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("capture")
    sp.add_argument("--kind", default="trial_error", choices=["trial_error", "repair", "note", "command_attempt"])
    sp.add_argument("--summary", required=True)
    sp.add_argument("--details", default="")
    sp.add_argument("--reusable", default="yes", help="yes/no")
    sp.add_argument("--tags", default="", help="comma-separated")
    sp.set_defaults(func=cmd_capture)

    sp = sub.add_parser("run")
    sp.add_argument("--tag", default="")
    sp.add_argument("--cwd", default="")
    sp.add_argument("--capture-always", action="store_true", help="capture even on success")
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_run)

    sp = sub.add_parser("start")
    sp.add_argument("--tag", default="")
    sp.add_argument("--cwd", default="")
    sp.add_argument("command", nargs=argparse.REMAINDER)
    sp.set_defaults(func=cmd_start)

    sp = sub.add_parser("poll")
    sp.set_defaults(func=cmd_poll)

    sp = sub.add_parser("consume")
    sp.add_argument("job_id")
    sp.add_argument("--note", default="", help="what happened + next action")
    sp.add_argument("--exit-code", default=None, help="optional integer")
    sp.set_defaults(func=cmd_consume)

    sp = sub.add_parser("triage")
    sp.add_argument("worklog_id")
    sp.add_argument("--outcome", required=True, choices=["discard", "defer", "reference", "script", "skill", "other"])
    sp.add_argument("--promoted", action="append", help="path you promoted to (repeatable)")
    sp.add_argument("--reason", default="", help="required for defer")
    sp.add_argument("--owner-skill", default="", help="optional pointer to owning skill doc")
    sp.add_argument("--note", default="", help="triage note")
    sp.set_defaults(func=cmd_triage)

    sp = sub.add_parser("close")
    sp.set_defaults(func=cmd_close)

    sp = sub.add_parser("audit")
    sp.add_argument("--json", action="store_true")
    sp.add_argument("--stall-minutes", type=int, default=30)
    sp.set_defaults(func=cmd_audit)

    return p

def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate "run/start" have a command after `--`
    if args.cmd in ("run", "start"):
        if not args.command:
            print("Missing command. Example: agentctl run --tag test -- pytest -q", file=sys.stderr)
            return 2
        # argparse includes leading '--' sometimes depending on invocation; strip if present
        if args.command and args.command[0] == "--":
            args.command = args.command[1:]

    return int(args.func(args))

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
