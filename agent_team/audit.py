"""Append-only, tamper-evident audit trail of the agent team -> outputs/audit_trail.json

Every step is written the moment it happens:
- each team session: model, database, prompt-file fingerprints, every agent's tools, limits;
- each run (one ticket, or the Boss's review): start and end, with usage;
- every tool call (full arguments), result (full) and error;
- every hand-off between agents (who asked whom, the task, the full answer), and refusals;
- the Boss's final outcome.

The file is never wiped or rewritten:
- a new entry is inserted just before the closing `]}` under an exclusive file lock, so earlier entries are untouched and the file is valid JSON after every write;
- one entry per line, so it is easy to grep;
- each entry carries `seq`, `prev_hash` and `hash` = sha256(prev_hash + the entry's JSON). Changing, deleting or reordering any entry breaks the chain, and `python -m agent_team.audit verify` reports where.

If the trail can't be written, the step fails ("no audit, no action").
Privacy: email addresses and card-length digit runs are masked.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

HW5 = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = 1
GENESIS = "0" * 64
MAX_FIELD = 20_000          # longest single string kept per field (full tool results fit comfortably)
CLOSING = b"\n]}\n"
HEADER = (
    '{"schema_version": 1, '
    '"description": "Append-only audit trail of the Campus Customs agent team: every session, run, tool call, '
    'tool result, hand-off between agents and final decision, written as it happens. Written by agent_team/audit.py.", '
    '"integrity": "Each entry has seq, prev_hash and hash = sha256(prev_hash + entry JSON without hash, keys sorted). '
    'Verify with: python -m agent_team.audit verify", '
    '"entries": ['
)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_KEYLIKE = re.compile(r"\b(?:sk|pk|rk)-[A-Za-z0-9_\-]{12,}")


def _secret_values() -> list[str]:
    """Actual secret values from the root .env, so they can be scrubbed even if one slips into a result."""
    try:
        from dotenv import dotenv_values
        vals = dotenv_values(HW5.parent / ".env").values()
    except Exception:
        return []
    return [v for v in vals if v and len(v) >= 8]
_CARD = re.compile(r"\b\d{12,19}\b")


class AuditError(RuntimeError):
    """The audit trail couldn't be written or is damaged."""


_SECRETS = _secret_values()


def audit_path() -> Path:
    return Path(os.environ.get("AUDIT_TRAIL_PATH") or HW5 / "outputs" / "audit_trail.json")


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="milliseconds")


def _clean(value: Any) -> Any:
    """Mask emails / card numbers and cap very long strings, recursively."""
    if isinstance(value, str):
        for secret in _SECRETS:
            value = value.replace(secret, "[secret]")
        value = _KEYLIKE.sub("[secret]", _CARD.sub("[number]", _EMAIL.sub("[email]", value)))
        return value if len(value) <= MAX_FIELD else value[:MAX_FIELD] + f"... [cut, {len(value)} chars]"
    if isinstance(value, dict):
        return {str(k): _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _clean(str(value))


def _hash(prev_hash: str, entry: dict) -> str:
    body = json.dumps(entry, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)
    return hashlib.sha256((prev_hash + body).encode()).hexdigest()


def _last_line(f, end: int) -> bytes:
    """The bytes between the last newline before `end` and `end`."""
    pos, buf = end, b""
    while pos > 0:
        step = min(65536, pos)
        pos -= step
        f.seek(pos)
        buf = f.read(step) + buf
        nl = buf.rfind(b"\n")
        if nl != -1:
            return buf[nl + 1:]
    return buf


def append(entry: dict) -> dict:
    """Write one entry at the end of the trail and return it (with seq and hashes)."""
    path = audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.touch(exist_ok=True)  # never truncates an existing file
        with open(path, "r+b") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                if size == 0:
                    f.write(HEADER.encode() + CLOSING)
                    f.flush()
                    size = f.tell()
                f.seek(size - len(CLOSING))
                if f.read(len(CLOSING)) != CLOSING:
                    raise AuditError(f"{path} doesn't end with ']}}': it may be damaged. It was left untouched; "
                                     "move it aside to start a new trail.")
                end = size - len(CLOSING)
                last = _last_line(f, end)
                if last.startswith(b'{"seq"'):
                    prev = json.loads(last)
                    seq, prev_hash, sep = prev["seq"] + 1, prev["hash"], b",\n"
                else:
                    seq, prev_hash, sep = 1, GENESIS, b"\n"
                record = {"seq": seq, "time": now_iso(), **_clean(entry), "prev_hash": prev_hash}
                record["hash"] = _hash(prev_hash, record)
                line = json.dumps(record, ensure_ascii=False, default=str).encode()
                f.seek(end)  # overwrite only the closing "]}" (4 bytes) and re-add it after the new entry
                f.write(sep + line + CLOSING)
                f.flush()
                os.fsync(f.fileno())
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)
    except AuditError:
        raise
    except OSError as exc:
        raise AuditError(f"Couldn't write the audit trail {path}: {exc}") from exc
    return record


def verify(path: Path | None = None) -> dict:
    """Re-check the whole chain. Returns {"ok", "entries", "problem"}."""
    path = path or audit_path()
    if not path.exists():
        return {"ok": True, "entries": 0, "problem": None}
    try:
        entries = json.loads(path.read_text(encoding="utf-8"))["entries"]
    except (ValueError, KeyError) as exc:
        return {"ok": False, "entries": 0, "problem": f"not valid audit JSON: {exc}"}
    prev_hash = GENESIS
    for i, e in enumerate(entries, start=1):
        if e.get("seq") != i:
            return {"ok": False, "entries": len(entries), "problem": f"entry {i} has seq {e.get('seq')}"}
        if e.get("prev_hash") != prev_hash:
            return {"ok": False, "entries": len(entries), "problem": f"seq {i}: prev_hash doesn't match entry {i - 1}"}
        body = {k: v for k, v in e.items() if k != "hash"}
        if _hash(prev_hash, body) != e.get("hash"):
            return {"ok": False, "entries": len(entries), "problem": f"seq {i}: contents were changed after writing"}
        prev_hash = e["hash"]
    return {"ok": True, "entries": len(entries), "problem": None}


def prompt_fingerprints(prompt_dir: Path) -> dict[str, str]:
    """sha256 of each agent's prompt file, so an auditor knows which instructions were in force."""
    return {p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(prompt_dir.glob("*.md"))}


if __name__ == "__main__":
    if sys.argv[1:] != ["verify"]:
        sys.exit("usage: python -m agent_team.audit verify")
    result = verify()
    print(f"{audit_path()}: {result['entries']} entries, " +
          ("chain OK" if result["ok"] else f"BROKEN: {result['problem']}"))
    sys.exit(0 if result["ok"] else 1)
