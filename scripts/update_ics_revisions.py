#!/usr/bin/env python3
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

ICS_PATH = Path("WoW-Calendar.ics")
STATE_PATH = Path("event-revisions.json")
MANUAL_EVENTS_PATH = Path("manual-events.json")


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


def load_manual_events():
    if not MANUAL_EVENTS_PATH.exists():
        return []
    data = json.loads(MANUAL_EVENTS_PATH.read_text(encoding="utf-8"))
    events = data.get("events", [])
    if not isinstance(events, list):
        raise ValueError("manual-events.json: 'events' must be a list")
    return events


def escape_ics_text(value):
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\r", "\\n")
        .replace("\n", "\\n")
    )


def date_to_ics(value):
    return datetime.strptime(value, "%Y-%m-%d").strftime("%Y%m%d")


def datetime_to_utc_ics(value):
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"Timed manual event requires timezone offset: {value}")
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def manual_event_to_lines(event):
    uid = event["uid"].strip()
    title = event["title"].strip()
    lines = ["BEGIN:VEVENT", f"UID:{uid}"]

    if event.get("start"):
        if not event.get("end"):
            raise ValueError(f"Manual event {uid}: timed event needs 'end'")
        lines.append(f"DTSTART:{datetime_to_utc_ics(event['start'])}")
        lines.append(f"DTEND:{datetime_to_utc_ics(event['end'])}")
    else:
        start_date = event["date"]
        end_date = event.get("end_date")
        if not end_date:
            end_date = (datetime.strptime(start_date, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        lines.append(f"DTSTART;VALUE=DATE:{date_to_ics(start_date)}")
        lines.append(f"DTEND;VALUE=DATE:{date_to_ics(end_date)}")

    lines.append(f"SUMMARY:{escape_ics_text(title)}")

    description = event.get("description") or event.get("note")
    if description:
        lines.append(f"DESCRIPTION:{escape_ics_text(description)}")
    if event.get("location"):
        lines.append(f"LOCATION:{escape_ics_text(event['location'])}")
    if event.get("source"):
        lines.append(f"URL:{event['source']}")

    category = event.get("category")
    if not category and event.get("type") == "patch":
        category = "Patch"
    if category:
        lines.append(f"CATEGORIES:{escape_ics_text(category)}")

    lines.append("X-WOW-SOURCE:MANUAL")
    lines.append("END:VEVENT")
    return lines


def sync_manual_events(lines):
    kept = []
    current = None

    for line in lines:
        if line == "BEGIN:VEVENT":
            current = [line]
            continue
        if current is not None:
            current.append(line)
            if line == "END:VEVENT":
                is_manual = any(item.upper() == "X-WOW-SOURCE:MANUAL" for item in current)
                if not is_manual:
                    kept.extend(current)
                current = None
            continue
        kept.append(line)

    if current is not None:
        kept.extend(current)

    while kept and kept[-1] == "":
        kept.pop()
    if not kept or kept[-1] != "END:VCALENDAR":
        raise ValueError("WoW-Calendar.ics is missing END:VCALENDAR")

    kept.pop()
    for event in load_manual_events():
        kept.extend(manual_event_to_lines(event))
    kept.append("END:VCALENDAR")
    return kept


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
    lines = sync_manual_events(lines)

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
