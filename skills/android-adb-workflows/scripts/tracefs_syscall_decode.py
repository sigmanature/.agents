#!/usr/bin/env python3

import argparse
import errno
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Optional


TRACE_RE = re.compile(
    r"^(?P<task>.+)-(?P<tid>\d+)\s+\[(?P<cpu>\d+)\]\s+\S+\s+"
    r"(?P<timestamp>\d+\.\d+):\s+"
    r"(?P<phase>sys_enter|sys_exit):\s+NR\s+(?P<nr>\d+)(?P<tail>.*)$"
)
ENTER_RE = re.compile(r"^\s+\((?P<args>[^)]*)\)(?P<trailer>.*)$")
EXIT_RE = re.compile(r"^\s*=\s*(?P<ret>\S+)(?P<trailer>.*)$")
QUOTED_PATH_RE = re.compile(r'"(?P<path>/(?:[^"\\]|\\.)*)"')
KEYED_PATH_PATTERNS = {
    "pathname": re.compile(r'\b(?:pathname|filename|path)=["\'](?P<path>(?:[^"\'\\]|\\.)+)["\']'),
    "oldpath": re.compile(r'\b(?:oldpath|oldname)=["\'](?P<path>(?:[^"\'\\]|\\.)+)["\']'),
    "newpath": re.compile(r'\b(?:newpath|newname)=["\'](?P<path>(?:[^"\'\\]|\\.)+)["\']'),
}
SYSCALL_DEFINE_RE = re.compile(r"^#define\s+(?P<symbol>__NR(?:3264)?_[A-Za-z0-9_]+)\s+(?P<expr>.+?)\s*$")

AT_FDCWD = -100
O_ACCMODE = 0x3

FALLBACK_SYSCALLS = {
    29: "ioctl",
    34: "mkdirat",
    35: "unlinkat",
    38: "renameat",
    46: "ftruncate",
    47: "fallocate",
    56: "openat",
    57: "close",
    62: "lseek",
    63: "read",
    64: "write",
    67: "pread64",
    68: "pwrite64",
    79: "newfstatat",
    82: "fsync",
    83: "fdatasync",
    215: "munmap",
    222: "mmap",
    226: "mprotect",
    276: "renameat2",
    437: "openat2",
}

OPEN_FLAG_BITS = {
    0x40: "O_CREAT",
    0x80: "O_EXCL",
    0x100: "O_NOCTTY",
    0x200: "O_TRUNC",
    0x400: "O_APPEND",
    0x800: "O_NONBLOCK",
    0x1000: "O_DSYNC",
    0x2000: "O_ASYNC",
    0x4000: "O_DIRECT",
    0x8000: "O_LARGEFILE",
    0x10000: "O_DIRECTORY",
    0x20000: "O_NOFOLLOW",
    0x40000: "O_NOATIME",
    0x80000: "O_CLOEXEC",
    0x100000: "O_SYNC",
    0x200000: "O_PATH",
    0x400000: "O_TMPFILE",
}
RENAMEAT2_FLAGS = {
    0x1: "RENAME_NOREPLACE",
    0x2: "RENAME_EXCHANGE",
    0x4: "RENAME_WHITEOUT",
}
UNLINKAT_FLAGS = {
    0x200: "AT_REMOVEDIR",
}
AT_FLAGS = {
    0x100: "AT_SYMLINK_NOFOLLOW",
    0x200: "AT_REMOVEDIR",
    0x400: "AT_SYMLINK_FOLLOW",
    0x800: "AT_NO_AUTOMOUNT",
    0x1000: "AT_EMPTY_PATH",
}
MMAP_PROT_FLAGS = {
    0x1: "PROT_READ",
    0x2: "PROT_WRITE",
    0x4: "PROT_EXEC",
    0x8: "PROT_SEM",
    0x01000000: "PROT_GROWSDOWN",
    0x02000000: "PROT_GROWSUP",
}
MMAP_FLAGS = {
    0x10: "MAP_FIXED",
    0x20: "MAP_ANONYMOUS",
    0x40: "MAP_32BIT",
    0x80: "MAP_GROWSDOWN",
    0x100: "MAP_DENYWRITE",
    0x200: "MAP_EXECUTABLE",
    0x400: "MAP_LOCKED",
    0x800: "MAP_NORESERVE",
    0x1000: "MAP_POPULATE",
    0x2000: "MAP_NONBLOCK",
    0x4000: "MAP_STACK",
    0x8000: "MAP_HUGETLB",
    0x10000: "MAP_SYNC",
    0x20000: "MAP_FIXED_NOREPLACE",
    0x40000: "MAP_UNINITIALIZED",
}
FALLOC_FLAGS = {
    0x01: "FALLOC_FL_KEEP_SIZE",
    0x02: "FALLOC_FL_PUNCH_HOLE",
    0x08: "FALLOC_FL_COLLAPSE_RANGE",
    0x10: "FALLOC_FL_ZERO_RANGE",
    0x20: "FALLOC_FL_INSERT_RANGE",
    0x40: "FALLOC_FL_UNSHARE_RANGE",
}
WHENCE_VALUES = {
    0: "SEEK_SET",
    1: "SEEK_CUR",
    2: "SEEK_END",
    3: "SEEK_DATA",
    4: "SEEK_HOLE",
}
SYSCALL_TABLE_CANDIDATES = (
    Path("/usr/include/asm-generic/unistd.h"),
    Path("/usr/include/aarch64-linux-gnu/asm/unistd.h"),
    Path("/usr/aarch64-linux-gnu/include/asm/unistd.h"),
)


@dataclass
class Field:
    name: str
    raw: Optional[str]
    value: Optional[int | str]
    display: str

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class PendingEnter:
    nr: int
    syscall: str
    fields: list[Field]
    raw_args: list[str]
    path_hints: dict[str, str]


@dataclass
class Event:
    source: str
    line_no: int
    task: str
    tid: Optional[int]
    cpu: Optional[int]
    timestamp: str
    phase: str
    nr: int
    syscall: str
    raw_line: str
    raw_args: list[str] = field(default_factory=list)
    fields: list[Field] = field(default_factory=list)
    raw_return: Optional[str] = None
    return_value: Optional[int | str] = None
    return_display: Optional[str] = None
    annotations: list[str] = field(default_factory=list)
    path_hints: dict[str, str] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "source": self.source,
            "line_no": self.line_no,
            "task": self.task,
            "tid": self.tid,
            "cpu": self.cpu,
            "timestamp": self.timestamp,
            "phase": self.phase,
            "nr": self.nr,
            "syscall": self.syscall,
            "raw_line": self.raw_line,
            "raw_args": self.raw_args,
            "fields": [field_item.to_json() for field_item in self.fields],
            "raw_return": self.raw_return,
            "return_value": self.return_value,
            "return_display": self.return_display,
            "annotations": self.annotations,
            "path_hints": self.path_hints,
        }


def _iter_lines(stream) -> Iterable[tuple[int, str]]:
    for line_no, line in enumerate(stream, start=1):
        yield line_no, line.rstrip("\n")


def _parse_hexish(raw_value: str, prefer_hex: bool) -> Optional[int]:
    token = raw_value.strip()
    if not token:
        return None
    if token.startswith("-"):
        body = token[1:]
        if body.startswith("0x"):
            return -int(body[2:], 16)
        base = 16 if prefer_hex and re.fullmatch(r"[0-9a-fA-F]+", body) else 10
        return int(token, base)
    if token.startswith("0x"):
        value = int(token, 16)
        digits = token[2:]
        if digits and value >= 1 << (len(digits) * 4 - 1):
            value -= 1 << (len(digits) * 4)
        return value
    if prefer_hex and re.fullmatch(r"[0-9a-fA-F]+", token):
        value = int(token, 16)
        bits = len(token) * 4
        if bits and value >= 1 << (bits - 1):
            value -= 1 << bits
        return value
    if re.fullmatch(r"\d+", token):
        return int(token, 10)
    if re.fullmatch(r"[0-9a-fA-F]+", token):
        value = int(token, 16)
        bits = len(token) * 4
        if bits and value >= 1 << (bits - 1):
            value -= 1 << bits
        return value
    return None


def _unsigned_hex(value: int) -> str:
    if value < 0:
        value &= (1 << 64) - 1
    return hex(value)


def _format_dirfd(value: Optional[int]) -> str:
    if value is None:
        return "?"
    if value == AT_FDCWD:
        return "AT_FDCWD"
    return str(value)


def _decode_bitmask(value: Optional[int], names: dict[int, str], zero_name: Optional[str] = None) -> str:
    if value is None:
        return "?"
    if value == 0 and zero_name:
        return zero_name
    parts: list[str] = []
    remaining = value
    for bit, name in sorted(names.items()):
        if bit != 0 and remaining & bit == bit:
            parts.append(name)
            remaining &= ~bit
    if not parts and value == 0 and zero_name is None:
        return "0"
    if remaining:
        parts.append(_unsigned_hex(remaining))
    return "|".join(parts) if parts else (zero_name or "0")


def _decode_open_flags(value: Optional[int]) -> str:
    if value is None:
        return "?"
    access_mode = value & O_ACCMODE
    access_name = {
        0: "O_RDONLY",
        1: "O_WRONLY",
        2: "O_RDWR",
    }.get(access_mode, _unsigned_hex(access_mode))
    remaining = value & ~O_ACCMODE
    names = [access_name]
    for bit, name in sorted(OPEN_FLAG_BITS.items()):
        if remaining & bit == bit:
            names.append(name)
            remaining &= ~bit
    if remaining:
        names.append(_unsigned_hex(remaining))
    return "|".join(names)


def _decode_mmap_flags(value: Optional[int]) -> str:
    if value is None:
        return "?"
    names: list[str] = []
    sharing = value & 0x3
    if sharing == 0x1:
        names.append("MAP_SHARED")
    elif sharing == 0x2:
        names.append("MAP_PRIVATE")
    elif sharing == 0x3:
        names.append("MAP_SHARED_VALIDATE")
    remaining = value & ~0x3
    for bit, name in sorted(MMAP_FLAGS.items()):
        if remaining & bit == bit:
            names.append(name)
            remaining &= ~bit
    if remaining:
        names.append(_unsigned_hex(remaining))
    if not names:
        names.append("0")
    return "|".join(names)


def _path_text(label: str, raw_value: Optional[str], path_hint: Optional[str], value: Optional[int]) -> Field:
    if path_hint:
        display = path_hint
        normalized = path_hint
    elif value is None:
        display = "?"
        normalized = None
    else:
        display = _unsigned_hex(value)
        normalized = display
    return Field(label, raw_value, normalized, display)


def _fd_text(label: str, raw_value: Optional[str], value: Optional[int], fd_map: dict[int, str]) -> Field:
    if value is None:
        return Field(label, raw_value, None, "?")
    display = str(value)
    mapped_path = fd_map.get(value)
    if mapped_path:
        display = f"{value}<{mapped_path}>"
    return Field(label, raw_value, value, display)


def _int_text(label: str, raw_value: Optional[str], value: Optional[int], *, base: str = "dec") -> Field:
    if value is None:
        return Field(label, raw_value, None, "?")
    if base == "hex":
        display = _unsigned_hex(value)
    elif base == "hex+dec":
        display = f"{_unsigned_hex(value)} ({value})"
    elif base == "octal":
        display = oct(value)
    else:
        display = str(value)
    return Field(label, raw_value, value, display)


def _mask_text(label: str, raw_value: Optional[str], value: Optional[int], decoder) -> Field:
    if value is None:
        return Field(label, raw_value, None, "?")
    return Field(label, raw_value, value, f"{_unsigned_hex(value)} [{decoder(value)}]")


def _decode_errno(raw_return: str, value: Optional[int | str]) -> Optional[str]:
    if not isinstance(value, int):
        return None
    if value >= 0:
        return None
    error_name = errno.errorcode.get(-value)
    if not error_name:
        return None
    return f"{raw_return} ({error_name})"


def _parse_path_hints(trailer: str, syscall: str) -> dict[str, str]:
    hints: dict[str, str] = {}
    for key, pattern in KEYED_PATH_PATTERNS.items():
        match = pattern.search(trailer)
        if match:
            hints[key] = bytes(match.group("path"), "utf-8").decode("unicode_escape")
    if hints:
        return hints
    quoted_paths = [bytes(match.group("path"), "utf-8").decode("unicode_escape") for match in QUOTED_PATH_RE.finditer(trailer)]
    if syscall in {"openat", "openat2", "mkdirat", "newfstatat", "unlinkat"} and quoted_paths:
        hints["pathname"] = quoted_paths[0]
    elif syscall in {"renameat", "renameat2"}:
        if quoted_paths:
            hints["oldpath"] = quoted_paths[0]
        if len(quoted_paths) > 1:
            hints["newpath"] = quoted_paths[1]
    return hints


def _build_field(
    syscall: str,
    raw_args: list[str],
    arg_values: list[Optional[int]],
    path_hints: dict[str, str],
    fd_map: dict[int, str],
) -> list[Field]:
    def value_at(index: int) -> Optional[int]:
        return arg_values[index] if index < len(arg_values) else None

    def raw_at(index: int) -> Optional[str]:
        return raw_args[index] if index < len(raw_args) else None

    if syscall == "openat":
        return [
            Field("dirfd", raw_at(0), value_at(0), _format_dirfd(value_at(0))),
            _path_text("pathname", raw_at(1), path_hints.get("pathname"), value_at(1)),
            _mask_text("flags", raw_at(2), value_at(2), _decode_open_flags),
            _int_text("mode", raw_at(3), value_at(3), base="octal"),
        ]
    if syscall == "openat2":
        return [
            Field("dirfd", raw_at(0), value_at(0), _format_dirfd(value_at(0))),
            _path_text("pathname", raw_at(1), path_hints.get("pathname"), value_at(1)),
            _int_text("how_ptr", raw_at(2), value_at(2), base="hex"),
            _int_text("size", raw_at(3), value_at(3), base="hex+dec"),
        ]
    if syscall == "close":
        return [_fd_text("fd", raw_at(0), value_at(0), fd_map)]
    if syscall in {"read", "write"}:
        return [
            _fd_text("fd", raw_at(0), value_at(0), fd_map),
            _int_text("buf", raw_at(1), value_at(1), base="hex"),
            _int_text("count", raw_at(2), value_at(2), base="hex+dec"),
        ]
    if syscall in {"pread64", "pwrite64"}:
        return [
            _fd_text("fd", raw_at(0), value_at(0), fd_map),
            _int_text("buf", raw_at(1), value_at(1), base="hex"),
            _int_text("count", raw_at(2), value_at(2), base="hex+dec"),
            _int_text("offset", raw_at(3), value_at(3), base="hex+dec"),
        ]
    if syscall in {"fsync", "fdatasync", "ftruncate"}:
        fields = [_fd_text("fd", raw_at(0), value_at(0), fd_map)]
        if syscall == "ftruncate":
            fields.append(_int_text("length", raw_at(1), value_at(1), base="hex+dec"))
        return fields
    if syscall == "newfstatat":
        return [
            Field("dirfd", raw_at(0), value_at(0), _format_dirfd(value_at(0))),
            _path_text("pathname", raw_at(1), path_hints.get("pathname"), value_at(1)),
            _int_text("statbuf", raw_at(2), value_at(2), base="hex"),
            _mask_text("flags", raw_at(3), value_at(3), lambda item: _decode_bitmask(item, AT_FLAGS, "0")),
        ]
    if syscall == "lseek":
        return [
            _fd_text("fd", raw_at(0), value_at(0), fd_map),
            _int_text("offset", raw_at(1), value_at(1), base="hex+dec"),
            Field("whence", raw_at(2), value_at(2), WHENCE_VALUES.get(value_at(2), str(value_at(2)) if value_at(2) is not None else "?")),
        ]
    if syscall in {"renameat", "renameat2"}:
        fields = [
            Field("olddirfd", raw_at(0), value_at(0), _format_dirfd(value_at(0))),
            _path_text("oldpath", raw_at(1), path_hints.get("oldpath"), value_at(1)),
            Field("newdirfd", raw_at(2), value_at(2), _format_dirfd(value_at(2))),
            _path_text("newpath", raw_at(3), path_hints.get("newpath"), value_at(3)),
        ]
        if syscall == "renameat2":
            fields.append(_mask_text("flags", raw_at(4), value_at(4), lambda item: _decode_bitmask(item, RENAMEAT2_FLAGS, "0")))
        return fields
    if syscall == "unlinkat":
        return [
            Field("dirfd", raw_at(0), value_at(0), _format_dirfd(value_at(0))),
            _path_text("pathname", raw_at(1), path_hints.get("pathname"), value_at(1)),
            _mask_text("flags", raw_at(2), value_at(2), lambda item: _decode_bitmask(item, UNLINKAT_FLAGS, "0")),
        ]
    if syscall == "mkdirat":
        return [
            Field("dirfd", raw_at(0), value_at(0), _format_dirfd(value_at(0))),
            _path_text("pathname", raw_at(1), path_hints.get("pathname"), value_at(1)),
            _int_text("mode", raw_at(2), value_at(2), base="octal"),
        ]
    if syscall == "fallocate":
        return [
            _fd_text("fd", raw_at(0), value_at(0), fd_map),
            _mask_text("mode", raw_at(1), value_at(1), lambda item: _decode_bitmask(item, FALLOC_FLAGS, "0")),
            _int_text("offset", raw_at(2), value_at(2), base="hex+dec"),
            _int_text("length", raw_at(3), value_at(3), base="hex+dec"),
        ]
    if syscall == "mmap":
        return [
            _int_text("addr", raw_at(0), value_at(0), base="hex"),
            _int_text("length", raw_at(1), value_at(1), base="hex+dec"),
            _mask_text("prot", raw_at(2), value_at(2), lambda item: _decode_bitmask(item, MMAP_PROT_FLAGS, "PROT_NONE")),
            _mask_text("flags", raw_at(3), value_at(3), _decode_mmap_flags),
            _fd_text("fd", raw_at(4), value_at(4), fd_map),
            _int_text("offset", raw_at(5), value_at(5), base="hex+dec"),
        ]
    if syscall == "munmap":
        return [
            _int_text("addr", raw_at(0), value_at(0), base="hex"),
            _int_text("length", raw_at(1), value_at(1), base="hex+dec"),
        ]
    if syscall == "mprotect":
        return [
            _int_text("addr", raw_at(0), value_at(0), base="hex"),
            _int_text("length", raw_at(1), value_at(1), base="hex+dec"),
            _mask_text("prot", raw_at(2), value_at(2), lambda item: _decode_bitmask(item, MMAP_PROT_FLAGS, "PROT_NONE")),
        ]
    if syscall == "ioctl":
        return [
            _fd_text("fd", raw_at(0), value_at(0), fd_map),
            _int_text("request", raw_at(1), value_at(1), base="hex"),
            _int_text("arg", raw_at(2), value_at(2), base="hex"),
        ]
    return [
        _int_text(f"arg{index}", raw_value, value, base="hex")
        for index, (raw_value, value) in enumerate(zip(raw_args, arg_values))
    ]


def _load_syscall_table() -> dict[int, str]:
    table = dict(FALLBACK_SYSCALLS)
    symbols: dict[str, str] = {}
    for path in SYSCALL_TABLE_CANDIDATES:
        if not path.is_file():
            continue
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                match = SYSCALL_DEFINE_RE.match(line.strip())
                if not match:
                    continue
                symbol = match.group("symbol")
                expr = match.group("expr").split("/*", 1)[0].strip()
                symbols[symbol] = expr
        break

    resolved: dict[str, int] = {}

    def resolve(symbol: str) -> Optional[int]:
        if symbol in resolved:
            return resolved[symbol]
        expr = symbols.get(symbol)
        if expr is None:
            return None
        if expr.isdigit():
            resolved[symbol] = int(expr)
            return resolved[symbol]
        if expr in symbols:
            value = resolve(expr)
            if value is not None:
                resolved[symbol] = value
            return value
        return None

    for symbol in sorted(symbols):
        if not symbol.startswith("__NR_") or symbol.startswith("__NR3264_"):
            continue
        value = resolve(symbol)
        if value is None or value in table:
            continue
        table[value] = symbol[len("__NR_") :]
    return table


def _thread_key(task: str, tid: Optional[int]) -> str:
    return f"{task}:{tid}" if tid is not None else task


def _apply_exit_annotations(
    event: Event,
    pending: Optional[PendingEnter],
    fd_map: dict[int, str],
) -> None:
    if pending is None or not isinstance(event.return_value, int):
        return
    return_value = event.return_value
    if pending.syscall in {"openat", "openat2"} and return_value >= 0:
        path_value = pending.path_hints.get("pathname")
        if path_value:
            fd_map[return_value] = path_value
            event.annotations.append(f"learned fd {return_value} -> {path_value}")
    elif pending.syscall == "close" and return_value == 0:
        fd_field = next((field_item for field_item in pending.fields if field_item.name == "fd" and isinstance(field_item.value, int)), None)
        if fd_field is not None and fd_field.value in fd_map:
            previous = fd_map.pop(fd_field.value)
            event.annotations.append(f"closed fd {fd_field.value} ({previous})")
    elif pending.syscall in {"renameat", "renameat2"} and return_value == 0:
        old_path = pending.path_hints.get("oldpath")
        new_path = pending.path_hints.get("newpath")
        if old_path and new_path:
            rewrites = 0
            for file_descriptor, existing_path in list(fd_map.items()):
                if existing_path == old_path:
                    fd_map[file_descriptor] = new_path
                    rewrites += 1
                elif existing_path.startswith(old_path.rstrip("/") + "/"):
                    fd_map[file_descriptor] = new_path.rstrip("/") + existing_path[len(old_path.rstrip("/")) :]
                    rewrites += 1
            if rewrites:
                event.annotations.append(f"updated {rewrites} fd path mapping(s) for rename")
    elif pending.syscall == "unlinkat" and return_value == 0:
        deleted_path = pending.path_hints.get("pathname")
        if deleted_path:
            touched = 0
            for file_descriptor, existing_path in list(fd_map.items()):
                if existing_path == deleted_path:
                    fd_map[file_descriptor] = f"{existing_path} [unlinked]"
                    touched += 1
            if touched:
                event.annotations.append(f"marked {touched} mapped path(s) as unlinked")


def _parse_event(
    source: str,
    line_no: int,
    raw_line: str,
    syscall_table: dict[int, str],
    pending_by_thread: dict[str, PendingEnter],
    fd_paths_by_thread: dict[str, dict[int, str]],
) -> Optional[Event]:
    match = TRACE_RE.match(raw_line)
    if not match:
        return None
    task = match.group("task").strip()
    tid = int(match.group("tid"))
    cpu = int(match.group("cpu"))
    timestamp = match.group("timestamp")
    phase = match.group("phase")
    nr = int(match.group("nr"))
    syscall = syscall_table.get(nr, f"nr_{nr}")
    tail = match.group("tail")
    thread = _thread_key(task, tid)
    fd_map = fd_paths_by_thread.setdefault(thread, {})

    if phase == "sys_enter":
        enter_match = ENTER_RE.match(tail)
        if not enter_match:
            return None
        raw_args = [token.strip() for token in enter_match.group("args").split(",") if token.strip()]
        arg_values = [_parse_hexish(token, prefer_hex=True) for token in raw_args]
        trailer = enter_match.group("trailer")
        path_hints = _parse_path_hints(trailer, syscall)
        fields = _build_field(syscall, raw_args, arg_values, path_hints, fd_map)
        pending_by_thread[thread] = PendingEnter(nr, syscall, fields, raw_args, path_hints)
        return Event(
            source=source,
            line_no=line_no,
            task=task,
            tid=tid,
            cpu=cpu,
            timestamp=timestamp,
            phase="enter",
            nr=nr,
            syscall=syscall,
            raw_line=raw_line,
            raw_args=raw_args,
            fields=fields,
            path_hints=path_hints,
        )

    exit_match = EXIT_RE.match(tail)
    if not exit_match:
        return None
    raw_return = exit_match.group("ret")
    trailer = exit_match.group("trailer")
    return_value: Optional[int | str] = _parse_hexish(raw_return, prefer_hex=False)
    pending = pending_by_thread.pop(thread, None)
    fields = pending.fields if pending else []
    event = Event(
        source=source,
        line_no=line_no,
        task=task,
        tid=tid,
        cpu=cpu,
        timestamp=timestamp,
        phase="exit",
        nr=nr,
        syscall=syscall,
        raw_line=raw_line,
        raw_args=pending.raw_args if pending else [],
        fields=fields,
        raw_return=raw_return,
        return_value=return_value,
        return_display=_decode_errno(raw_return, return_value) or raw_return,
        path_hints=pending.path_hints if pending else _parse_path_hints(trailer, syscall),
    )
    if pending and pending.nr != nr:
        event.annotations.append(f"pending enter mismatch: saw {pending.syscall}/NR {pending.nr}")
    _apply_exit_annotations(event, pending, fd_map)
    if syscall == "mmap" and isinstance(return_value, int) and return_value >= 0:
        event.return_display = _unsigned_hex(return_value)
    return event


def _format_event(event: Event) -> str:
    who = f"{event.task}-{event.tid}" if event.tid is not None else event.task
    if event.phase == "enter":
        fields_text = ", ".join(f"{field_item.name}={field_item.display}" for field_item in event.fields)
        raw_text = ", ".join(event.raw_args)
        suffix = f" raw=({raw_text})" if raw_text else ""
        return f"{event.timestamp} {who} enter {event.syscall}({fields_text}){suffix}"
    annotations = f" [{' ; '.join(event.annotations)}]" if event.annotations else ""
    return f"{event.timestamp} {who} exit  {event.syscall} -> {event.return_display}{annotations}"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Decode Android arm64 tracefs raw_syscalls sys_enter/sys_exit lines, "
            "including best-effort fd->path correlation for oat/vdex forensics."
        )
    )
    parser.add_argument("trace", nargs="*", type=Path, help="Trace text file(s). Reads stdin when omitted or when '-' is used.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of text.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    syscall_table = _load_syscall_table()
    pending_by_thread: dict[str, PendingEnter] = {}
    fd_paths_by_thread: dict[str, dict[int, str]] = {}
    events: list[Event] = []
    skipped_lines = 0

    inputs = args.trace or [Path("-")]
    for path in inputs:
        if str(path) == "-":
            source = "<stdin>"
            for line_no, raw_line in _iter_lines(sys.stdin):
                event = _parse_event(source, line_no, raw_line, syscall_table, pending_by_thread, fd_paths_by_thread)
                if event is None:
                    skipped_lines += 1
                    continue
                events.append(event)
            continue

        source = str(path)
        with path.open("r", encoding="utf-8", errors="replace") as stream:
            for line_no, raw_line in _iter_lines(stream):
                event = _parse_event(source, line_no, raw_line, syscall_table, pending_by_thread, fd_paths_by_thread)
                if event is None:
                    skipped_lines += 1
                    continue
                events.append(event)

    if args.json:
        json.dump(
            {
                "events": [event.to_json() for event in events],
                "skipped_lines": skipped_lines,
                "syscall_table_size": len(syscall_table),
            },
            sys.stdout,
            indent=2,
        )
        sys.stdout.write("\n")
    else:
        for event in events:
            print(_format_event(event))
        if skipped_lines:
            print(f"# skipped_lines={skipped_lines}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
