#!/usr/bin/env python3
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ICS_PATH = Path("WoW-Calendar.ics")
STATE_PATH = Path("event-revisions.json")


def now_utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_state():
    if not STATE_PATH.exists():
        return {"version": 1, "events": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
        data.setdefault("version", 1)
        data.setdefault("events", {})
        return data
    except Exception:
        return {"version": 1, "events": {}}


def semantic_fingerprint(lines):
    semantic = []
    for line in lines:
        upper = line.upper()
        if upper.startswith("DTSTAMP:"):
            continue
        if upper.startswith("LAST-MODIFIED:"):
            continue
        if upper.startswith("SEQUENCE:"):
            continue
        semantic.append(line)
    payload = "\n".join(semantic).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def revise_event(lines, state_events, stamp):
    uid = None
    for line in lines:
        if line.upper().startswith("UID:"):
            uid = line[4:].strip()
            break
    if not uid:
        return lines, False

    fingerprint = semantic_fingerprint(lines)
    previous = state_events.get(uid, {})
    previous_fp = previous.get("fingerprint")
    if previous_fp == fingerprint:
        sequence = int(previous.get("sequence", 1))
        modified = previous.get("last_modified", stamp)
    else:
        sequence = int(previous.get("sequence", 0)) + 1
        modified = stamp

    cleaned = []
    for line in lines:
        upper = line.upper()
        if upper.startswith("DTSTAMP:") or upper.startswith("LAST-MODIFIED:") or upper.startswith("SEQUENCE:"):
            continue
        cleaned.append(line)

    output = []
    inserted = False
    for line in cleaned:
        output.append(line)
        if line.upper().startswith("UID:") and not inserted:
            output.append(f"DTSTAMP:{modified}")
            output.append(f"LAST-MODIFIED:{modified}")
            output.append(f"SEQUENCE:{sequence}")
            inserted = True

    state_events[uid] = {
        "fingerprint": fingerprint,
        "sequence": sequence,
        "last_modified": modified,
    }
    return output, previous_fp != fingerprint


def main():
    text = ICS_PATH.read_text(encoding="utf-8-sig")
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    state = load_state()
    state_events = state["events"]
    stamp = now_utc_stamp()

    result = []
    event = None
    revised = 0
    total = 0

    for line in lines:
        if line == "BEGIN:VEVENT":
            event = [line]
            continue
        if event is not None:
            event.append(line)
            if line == "END:VEVENT":
                total += 1
                event, changed = revise_event(event, state_events, stamp)
                if changed:
                    revised += 1
                result.extend(event)
                event = None
            continue
        result.append(line)

    if event is not None:
        result.extend(event)

    # Remove stale state entries for events no longer present.
    live_uids = {
        line[4:].strip()
        for line in result
        if line.upper().startswith("UID:")
    }
    state["events"] = {uid: value for uid, value in state_events.items() if uid in live_uids}

    output = "\r\n".join(result).rstrip("\r\n") + "\r\n"
    ICS_PATH.write_text(output, encoding="utf-8", newline="")
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Processed {total} VEVENTs; revised {revised}.")


if __name__ == "__main__":
    main()
