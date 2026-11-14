#!/usr/bin/env python3
"""Archive already-downloaded official recording captions/audio. Python 3.10+.

No network, browser control, speech recognition, or transcoding. Run serially.
All source files stay intact; differing existing archive files are never replaced.
"""
from __future__ import annotations

import argparse
import base64
import csv
from datetime import date, datetime, timezone
import hashlib
import html
import io
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit, urlunsplit


STATE = ".recording-archive.json"
KINDS = {"official_vtt", "official_srt", "official_text", "official_dom_cues", "original_audio"}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def required_text(obj, key):
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a nonempty string")
    return value


def positive_number(value, label, allow_zero=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f"{label} must be a finite number")
    if value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"{label} must be {'nonnegative' if allow_zero else 'positive'}")
    return value


def clean_url(value):
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("source_url must be an http(s) page URL without credentials")
    # Signed URLs, passcode parameters and fragments do not belong in the archive.
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def slug(text, limit=65):
    result = re.sub(r"[^\w.-]+", "_", str(text), flags=re.UNICODE).strip("._")[:limit]
    return result or "recording"


def identity(obj):
    if obj.get("recording_id"):
        return required_text(obj, "recording_id")
    return required_text(obj, "meeting_id") + "@" + required_text(obj, "listed_start")


def record_key(course_key, recording_id):
    return sha(json_bytes([course_key, recording_id]))[:20]


def safe_target(root, relative):
    root = Path(root).resolve()
    target = root / relative
    if not target.resolve().is_relative_to(root):
        raise ValueError("archive path escapes output root")
    for item in [target, *target.parents]:
        if item == root:
            break
        if item.is_symlink():
            raise ValueError("archive paths must not contain symlinks")
    return target


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".archive-", delete=False) as stream:
        temporary = Path(stream.name)
        stream.write(data)
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def save_state(root, state):
    atomic_write(safe_target(root, STATE), json_bytes(state))


def load_state(root):
    state = read_json(safe_target(root, STATE))
    if state.get("schema_version") != 1:
        raise ValueError("unsupported archive schema")
    return state


def init_archive(root, inventory_path):
    inventory = read_json(inventory_path)
    if not isinstance(inventory, dict) or not isinstance(inventory.get("courses"), list) or not inventory["courses"]:
        raise ValueError("inventory needs a nonempty courses[]")
    discovery = inventory.get("discovery", {})
    if not isinstance(discovery, dict) or not isinstance(discovery.get("complete", False), bool):
        raise ValueError("discovery.complete must be boolean")
    courses, records = [], []
    seen_courses, seen_records = set(), set()
    for course in inventory["courses"]:
        key = required_text(course, "key")
        if key in seen_courses:
            raise ValueError("duplicate course key")
        seen_courses.add(key)
        courses.append({"key": key, "title": required_text(course, "title"),
                        "source_url": clean_url(required_text(course, "source_url"))})
        if not isinstance(course.get("recordings"), list):
            raise ValueError("each course needs recordings[]; use [] for an observed empty list")
        for row in course["recordings"]:
            rid = identity(row)
            uid = record_key(key, rid)
            if uid in seen_records:
                raise ValueError("duplicate recording identity in course")
            seen_records.add(uid)
            session_date = row.get("session_date", "undated")
            if session_date != "undated":
                date.fromisoformat(session_date)
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", session_date):
                    raise ValueError("session_date must be YYYY-MM-DD")
            entry = {"key": uid, "course_key": key, "recording_id": rid,
                     "title": required_text(row, "title"), "listed_start": row.get("listed_start", ""),
                     "session_date": session_date, "source_url": clean_url(row.get("source_url", "")),
                     "status": "pending", "reason": "Not captured yet", "captures": {}, "coverage_confirmed": False}
            if "expected_parts" in row:
                parts = row["expected_parts"]
                if not isinstance(parts, list) or not parts or not all(isinstance(p, str) and p.strip() for p in parts) or len(parts) != len(set(parts)):
                    raise ValueError("expected_parts must be a nonempty list of unique asset IDs")
                entry["expected_parts"] = parts
            for field in ("meeting_id", "timezone", "detail_start", "notes"):
                if field in row:
                    if not isinstance(row[field], str):
                        raise ValueError(f"{field} must be a string")
                    entry[field] = row[field]
            if "duration_seconds" in row:
                entry["duration_seconds"] = positive_number(row["duration_seconds"], "duration_seconds", True)
            records.append(entry)
    state = {"schema_version": 1, "created_at": now(), "discovery": {
        "complete": discovery.get("complete", False), "notes": str(discovery.get("notes", ""))},
        "courses": courses, "records": records, "transcription_performed": False}
    target = safe_target(root, STATE)
    if target.exists():
        old = load_state(root)
        def immutable(row):
            return {k: v for k, v in row.items() if k not in {"status", "reason", "captures", "coverage_confirmed", "last_error", "updated_at"}}
        new_courses = {c["key"]: c for c in courses}
        new_records = {r["key"]: r for r in records}
        for course in old["courses"]:
            if course["key"] not in new_courses or course != new_courses[course["key"]]:
                raise ValueError("inventory must retain existing courses unchanged; missing/conflicting course")
        for row in old["records"]:
            if row["key"] not in new_records or immutable(row) != immutable(new_records[row["key"]]):
                raise ValueError("inventory must retain existing recordings unchanged; missing/conflicting recording")
        old_course_keys = {c["key"] for c in old["courses"]}
        old_record_keys = {r["key"] for r in old["records"]}
        old["courses"].extend(c for c in courses if c["key"] not in old_course_keys)
        old["records"].extend(r for r in records if r["key"] not in old_record_keys)
        old["discovery"] = state["discovery"]
        save_state(root, old)
        return old
    save_state(root, state)
    return state


def find_record(state, payload):
    course = required_text(payload, "course_key")
    rid = identity(payload)
    matches = [r for r in state["records"] if r["course_key"] == course and r["recording_id"] == rid]
    if len(matches) != 1:
        raise ValueError("capture identity is not present exactly once in inventory")
    row = matches[0]
    for field in ("title", "listed_start", "meeting_id"):
        if field in payload and field in row and payload[field] != row[field]:
            raise ValueError(f"capture {field} does not match inventory")
    return row


def reject_html(data):
    prefix = data[:8192].decode("utf-8", errors="ignore").lstrip("\ufeff \t\r\n").lower()
    if re.match(r"<(?:!doctype\s+html|html\b|head\b|body\b|form\b)", prefix):
        raise ValueError("HTML/login/error page is not recording content")


def decode_text(data):
    reject_html(data)
    text = data.decode("utf-8-sig")
    if not text.strip() or "\x00" in text:
        raise ValueError("empty or binary text content")
    return text.replace("\r\n", "\n").replace("\r", "\n")


STAMP = r"(?:\d{2,}:)?\d{2}:\d{2}[.,]\d{3}"
TIMING = re.compile(rf"^({STAMP})\s+-->\s+({STAMP})(?:\s+.*)?$")


def seconds(stamp):
    if isinstance(stamp, (int, float)):
        return float(positive_number(stamp, "cue start", True))
    if not isinstance(stamp, str) or not re.fullmatch(r"(?:\d+:)?\d{2}:\d{2}(?:[.,]\d{1,3})?", stamp):
        raise ValueError("timestamp must be seconds or MM:SS[.mmm]/HH:MM:SS[.mmm]")
    values = [float(p) for p in stamp.replace(",", ".").split(":")]
    if values[-1] >= 60 or (len(values) == 3 and values[-2] >= 60):
        raise ValueError("invalid timestamp")
    return sum(number * (60 ** index) for index, number in enumerate(reversed(values)))


def display_time(value):
    millis = round(value * 1000)
    hours, remain = divmod(millis, 3600000)
    minutes, remain = divmod(remain, 60000)
    sec, millis = divmod(remain, 1000)
    return f"{hours:02d}:{minutes:02d}:{sec:02d}.{millis:03d}"


def readable_cue(text):
    text = re.sub(r"<v(?:\.[^\s>]*)?\s+([^>]+)>", lambda m: m[1] + ": ", text)
    # Remove only recognized caption markup, keeping literal code such as <html>
    # and <MyType>. The original bytes remain available if a token is ambiguous.
    text = re.sub(r"</?(?:b|i|u|ruby|rt|v)>|</?c(?:\.[^>\s]+)*>|</?lang(?:\s+[^>]+)?>", "", text)
    text = re.sub(rf"<{STAMP}>", "", text)
    return html.unescape(text).strip()


def caption_cues(data, kind):
    text = decode_text(data)
    if kind == "official_vtt" and not re.match(r"^WEBVTT(?:[ \t].*)?(?:\n|$)", text):
        raise ValueError("official_vtt must have a WEBVTT header")
    if kind == "official_srt" and text.startswith("WEBVTT"):
        raise ValueError("VTT supplied as SRT")
    cues = []
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.splitlines()
        if not lines:
            continue
        if kind == "official_vtt" and re.match(r"^(?:WEBVTT|NOTE(?:\s|$)|STYLE$|REGION$)", lines[0]):
            continue
        timings = [(n, TIMING.fullmatch(line.strip())) for n, line in enumerate(lines)]
        timings = [(n, match) for n, match in timings if match]
        if not timings:
            raise ValueError("unrecognized caption block; preserve source and inspect format")
        if len(timings) != 1 or timings[0][0] > 1:
            raise ValueError("ambiguous caption block")
        index, match = timings[0]
        start, end = seconds(match[1]), seconds(match[2])
        if end < start:
            raise ValueError("caption end precedes start")
        cue_text = readable_cue("\n".join(lines[index + 1:]))
        if not cue_text:
            raise ValueError("empty caption cue")
        cues.append({"start": start, "end": end, "text": cue_text})
    if not cues:
        raise ValueError("caption file has no cues")
    return cues


def dom_cues(payload):
    supplied = payload.get("cues")
    if not isinstance(supplied, list) or not supplied:
        raise ValueError("official_dom_cues requires nonempty cues[]")
    cues = []
    for cue in supplied:
        start = seconds(cue.get("start"))
        text = required_text(cue, "text")
        speaker = cue.get("speaker", "")
        if not isinstance(speaker, str):
            raise ValueError("cue speaker must be a string")
        # Keep exactly the observed start; no end is derived from the next cue.
        cues.append({"start": start, "text": (speaker + ": " if speaker else "") + text})
    return cues


def cues_txt(cues):
    blocks = []
    for cue in cues:
        timing = display_time(cue["start"])
        if "end" in cue:
            timing += " --> " + display_time(cue["end"])
        blocks.append("[" + timing + "]\n" + cue["text"])
    return ("\n\n".join(blocks) + "\n").encode("utf-8")


def source_bytes(payload, capture_path):
    fields = [k for k in ("source_file", "body_base64", "text") if k in payload]
    if len(fields) != 1:
        raise ValueError("supply exactly one of source_file, body_base64, text")
    if fields[0] == "source_file":
        path = Path(required_text(payload, "source_file")).expanduser()
        if not path.is_absolute():
            path = Path(capture_path).resolve().parent / path
        data = path.read_bytes()
    elif fields[0] == "body_base64":
        data = base64.b64decode(required_text(payload, "body_base64"), validate=True)
    else:
        if payload["kind"] == "original_audio":
            raise ValueError("audio cannot be supplied as text")
        data = required_text(payload, "text").encode("utf-8")
    if not data:
        raise ValueError("empty content")
    if "expected_bytes" in payload:
        expected = payload["expected_bytes"]
        if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0 or expected != len(data):
            raise ValueError("expected_bytes must equal the complete positive source byte count")
    return data, fields[0]


def audio_header(data):
    reject_html(data)
    if len(data) < 12:
        raise ValueError("audio is too short to identify a container")
    if data[4:8] == b"ftyp":
        return ".m4a", "ISO-BMFF"
    if data.startswith(b"RIFF") and data[8:12] == b"WAVE":
        return ".wav", "WAVE"
    if data.startswith(b"OggS"):
        return ".ogg", "Ogg"
    if data.startswith(b"fLaC"):
        return ".flac", "FLAC"
    if data.startswith(b"ID3") or (data[0] == 0xff and data[1] & 0xe0 == 0xe0):
        return ".mp3", "MPEG audio candidate"
    raise ValueError("unrecognized audio container; inspect the original download before adapting the helper")


def audio_details(data, payload, transfer_kind, ffprobe):
    source = payload.get("source", {})
    status = source.get("http_status")
    content_range = source.get("content_range")
    if transfer_kind == "body_base64" and "expected_bytes" not in payload:
        raise ValueError("base64 audio requires expected_bytes")
    if transfer_kind == "body_base64" and status not in (200, 206):
        raise ValueError("base64 audio requires source.http_status 200 or 206")
    extension, container = audio_header(data)
    result = {"validation_level": "container_header_and_size_only", "container_hint": container,
              "media_validation_unavailable": True, "full_content_range_verified": bool(content_range),
              "expected_bytes_verified": "expected_bytes" in payload,
              "validation_note": "Container signature/bytes checked only; audio streams, duration and complete playback are unverified."}
    probe = shutil.which("ffprobe") if ffprobe == "auto" else (None if ffprobe == "none" else ffprobe)
    if not probe:
        return extension, result
    with tempfile.TemporaryDirectory(prefix="recording-probe-") as temporary:
        path = Path(temporary) / ("audio" + extension)
        path.write_bytes(data)
        process = subprocess.run([str(probe), "-v", "error", "-show_entries", "format=duration,format_name:stream=codec_type,duration",
                                  "-of", "json", str(path)], capture_output=True, text=True, timeout=60, check=False)
    if process.returncode:
        raise ValueError("ffprobe rejected audio: " + process.stderr.strip()[:400])
    report = json.loads(process.stdout)
    streams = report.get("streams", [])
    if not any(s.get("codec_type") == "audio" for s in streams) or any(s.get("codec_type") == "video" for s in streams):
        raise ValueError("download must contain an audio stream and no video streams")
    duration = report.get("format", {}).get("duration")
    if duration is None:
        duration = next((s["duration"] for s in streams if s.get("codec_type") == "audio" and "duration" in s), None)
    result.update({"validation_level": "ffprobe_audio_container", "media_validation_unavailable": False,
                   "media_duration_seconds": positive_number(float(duration), "probed duration"),
                   "format_name": report.get("format", {}).get("format_name", ""),
                   "validation_note": "ffprobe confirmed audio-only streams and duration; full playback/decoding was not tested."})
    return extension, result


def capture_files(payload, capture_path, ffprobe):
    kind = payload.get("kind")
    if kind not in KINDS:
        raise ValueError("unsupported capture kind")
    source = payload.get("source", {})
    if not isinstance(source, dict):
        raise ValueError("source must be an object")
    required_text(source, "method")
    if kind.startswith("official_") and source.get("official") is not True:
        raise ValueError("official captions require source.official=true and a source.method explaining their provenance")
    source_fields = {k: source[k] for k in ("method", "official", "mime_type", "http_status", "content_range", "observed_at", "note") if k in source}
    if "page_url" in source:
        source_fields["page_url"] = clean_url(source["page_url"])
    meta = {"kind": kind, "source": source_fields, "transcription_performed": False}
    if "duration_seconds" in payload:
        meta["source_duration_seconds"] = positive_number(payload["duration_seconds"], "asset duration_seconds", True)
    if kind == "official_dom_cues":
        cues = dom_cues(payload)
        # This JSON preserves the supplied official DOM export, not a fabricated VTT.
        files = {"official-cues.json": json_bytes(payload["cues"]), "transcript.txt": cues_txt(cues)}
        meta.update({"validation_level": "official_dom_start_times", "end_times_available": False})
    else:
        data, transfer = source_bytes(payload, capture_path)
        content_range = source.get("content_range")
        if source.get("http_status") == 206 and not content_range:
            raise ValueError("HTTP 206 content requires complete content_range")
        if content_range:
            match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", str(content_range), re.I)
            if not match or tuple(map(int, match.groups())) != (0, len(data) - 1, len(data)):
                raise ValueError("partial or inconsistent content_range; only a complete response is accepted")
        meta["full_content_range_verified"] = bool(content_range)
        meta["source_representation"] = "utf8_from_text" if transfer == "text" else "original_bytes"
        if kind == "original_audio":
            extension, details = audio_details(data, payload, transfer, ffprobe)
            files = {"audio" + extension: data}
            meta.update(details)
            return files, meta
        if kind == "official_text":
            decode_text(data)
            files = {"official.txt": data}
            meta.update({"validation_level": "official_text_nonempty", "timing_coverage": "unknown"})
            return files, meta
        cues = caption_cues(data, kind)
        files = {"official." + kind.removeprefix("official_"): data, "transcript.txt": cues_txt(cues)}
        meta.update({"validation_level": "official_caption_structure", "end_times_available": True})
    meta.update({"cue_count": len(cues), "first_start_seconds": min(c["start"] for c in cues),
                 "last_start_seconds": max(c["start"] for c in cues)})
    if all("end" in c for c in cues):
        meta["last_end_seconds"] = max(c["end"] for c in cues)
    return files, meta


def ingest(root, capture_path, ffprobe="auto"):
    state, payload = load_state(root), read_json(capture_path)
    row = find_record(state, payload)
    try:
        files, capture = capture_files(payload, capture_path, ffprobe)
        asset_id = payload.get("asset_id", "main")
        if not isinstance(asset_id, str) or not asset_id.strip():
            raise ValueError("asset_id must be a nonempty string")
        if "coverage_confirmed" in payload and not isinstance(payload["coverage_confirmed"], bool):
            raise ValueError("coverage_confirmed must be boolean")
        folder = f"{slug(row['course_key'])}-{sha(row['course_key'].encode())[:6]}/{row['session_date']}/{slug(row['title'])}-{row['key']}/{slug(asset_id)}-{sha(asset_id.encode())[:8]}"
        targets = [(safe_target(root, folder + "/" + name), data) for name, data in files.items()]
        new_files = [{"path": p.relative_to(Path(root).resolve()).as_posix(), "bytes": len(data), "sha256": sha(data)} for p, data in targets]
        old_capture = row["captures"].get(asset_id)
        if old_capture and old_capture["files"] != new_files:
            raise ValueError("capture conflict: existing recording differs; preserve both externally and reconcile explicitly")
        for path, data in targets:
            if path.exists() and path.read_bytes() != data:
                raise ValueError("file conflict: differing file will not be overwritten")
        for path, data in targets:
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                # Exclusive creation prevents accidental replacement even on a raced write.
                with path.open("xb") as stream:
                    stream.write(data)
        capture["files"] = new_files
        capture["captured_at"] = (old_capture or {}).get("captured_at", now())
        capture["asset_id"] = asset_id
        expected = capture.get("source_duration_seconds")
        observed = capture.get("media_duration_seconds", capture.get("last_end_seconds"))
        capture["duration_difference_seconds"] = round(observed - expected, 3) if observed is not None and expected is not None else None
        capture["duration_difference_basis"] = "media_duration" if "media_duration_seconds" in capture else ("last_caption_end" if "last_end_seconds" in capture else "unavailable")
        row["captures"][asset_id] = capture
        # A new asset invalidates an earlier coverage assertion unless reconfirmed.
        coverage = payload.get("coverage_confirmed", row["coverage_confirmed"] if old_capture else False)
        missing = set(row.get("expected_parts", [])) - set(row["captures"])
        row.update({"coverage_confirmed": coverage, "status": "success" if coverage and not missing else "pending",
                    "reason": ("Missing expected assets: " + ", ".join(sorted(missing))) if missing else ("" if coverage else "Confirm all recording parts/languages have been covered"), "updated_at": now()})
        row.pop("last_error", None)
        save_state(root, state)
        return row
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError, TypeError, KeyError) as exc:
        row.update({"status": "failed", "reason": str(exc), "last_error": str(exc), "updated_at": now()})
        save_state(root, state)
        raise


def mark(root, course_key, recording_id, status, reason):
    state = load_state(root)
    row = find_record(state, {"course_key": course_key, "recording_id": recording_id})
    row.update({"status": status, "reason": reason, "updated_at": now()})
    save_state(root, state)
    return row


def capture_summary(row):
    captures = list(row["captures"].values())
    result = {"files": [f for c in captures for f in c["files"]],
              "kind": "; ".join(sorted({c["kind"] for c in captures})),
              "validation_level": "; ".join(sorted({c["validation_level"] for c in captures})),
              "media_validation_unavailable": any(c.get("media_validation_unavailable", False) for c in captures)}
    # Multi-part and multi-language timing is not blindly summed or aligned.
    if len(captures) == 1:
        for field in ("media_duration_seconds", "last_end_seconds"):
            if field in captures[0]:
                result[field] = captures[0][field]
    return result


def report(root, write=True):
    state = load_state(root)
    rows, counts = [], {s: 0 for s in ("success", "blocked", "failed", "pending")}
    course_lookup = {c["key"]: c for c in state["courses"]}
    for record in state["records"]:
        row = dict(record)
        row["course_title"] = course_lookup[row["course_key"]]["title"]
        row["course_source_url"] = course_lookup[row["course_key"]]["source_url"]
        capture = capture_summary(row)
        row["capture_summary"] = capture
        errors = []
        for entry in capture.get("files", []):
            try:
                data = safe_target(root, entry["path"]).read_bytes()
                if len(data) != entry["bytes"] or sha(data) != entry["sha256"]:
                    errors.append(entry["path"] + ": content changed")
            except (ValueError, OSError) as exc:
                errors.append(entry["path"] + ": " + str(exc))
        if row["status"] == "success" and not capture.get("files"):
            errors.append("no captured files")
        if row["status"] == "success" and (not row["coverage_confirmed"] or set(row.get("expected_parts", [])) - set(row["captures"])):
            errors.append("recording coverage is not confirmed or expected assets are missing")
        if errors:
            row["status"], row["reason"] = "failed", "; ".join(errors)
        expected = row.get("duration_seconds")
        observed = capture.get("media_duration_seconds", capture.get("last_end_seconds"))
        row["duration_difference_seconds"] = round(observed - expected, 3) if observed is not None and expected is not None else None
        row["duration_difference_basis"] = "media_duration" if "media_duration_seconds" in capture else ("last_caption_end" if "last_end_seconds" in capture else "unavailable")
        # A subtitle ending early is reported, not silently extended or marked as absent speech.
        counts[row["status"]] += 1
        rows.append(row)
    result = {"schema_version": 1, "generated_at": now(), "discovery": state["discovery"],
              "complete_against_inventory": counts["success"] == len(rows),
              "complete": state["discovery"]["complete"] and counts["success"] == len(rows),
              "discovery_completeness_basis": "User/agent assertion from full source-list pagination; the local helper cannot verify the website.",
              "counts": counts, "total_recordings": len(rows), "courses": state["courses"],
              "media_validation_unavailable_count": sum(bool(r["capture_summary"].get("media_validation_unavailable")) for r in rows),
              "transcription_performed": False, "records": rows}
    if write:
        atomic_write(safe_target(root, "recordings.json"), json_bytes(result))
        fields = ["course_key", "title", "recording_id", "listed_start", "timezone", "status", "reason", "kind", "validation_level", "media_validation_unavailable", "duration_seconds", "duration_difference_seconds", "duration_difference_basis", "source_url", "files"]
        stream = io.StringIO(newline="")
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        lines = ["# Recording archive", "", "Complete: " + ("yes" if result["complete"] else "no"),
                 "", f"Inventory: {len(rows)}; " + "; ".join(f"{k}: {v}" for k, v in counts.items()),
                 "", "Only official captions and original audio are included. No transcription was performed.",
                 "", "Audio without ffprobe has header/size checks only. Caption duration gaps are observations, not inferred missing speech.",
                 "", "| Course / recording | Start | Status | Validation | Files | Source | Duration difference (s) | Reason |",
                 "|---|---|---|---|---|---|---|---|"]
        def md(value):
            return str(value if value is not None else "").replace("|", "\\|").replace("\n", " ").replace("\r", " ")
        for row in rows:
            capture = row["capture_summary"]
            flat = {field: row.get(field, capture.get(field, "")) for field in fields}
            flat["source_url"] = row["source_url"] or row["course_source_url"]
            flat["files"] = "; ".join(f["path"] for f in capture.get("files", []))
            writer.writerow(flat)
            links = " ".join(f"[{md(asset_id + '/' + Path(f['path']).name)}]({quote(f['path'], safe='/')})"
                             for asset_id, asset in row["captures"].items() for f in asset["files"])
            source = flat["source_url"]
            values = [row["course_key"] + " / " + row["title"], row["listed_start"], row["status"], capture.get("validation_level", ""),
                      links, f"[source]({source})" if source else "", row["duration_difference_seconds"], row["reason"]]
            lines.append("| " + " | ".join(md(v) for v in values) + " |")
        lines.extend(["", "## Asset details", "", "Each asset keeps its own source and timing evidence; language variants and segments are not combined into a guessed duration."])
        for row in rows:
            lines.extend(["", "### " + md(row["course_key"] + " / " + row["title"] + " — " + row["listed_start"] + " (" + row["recording_id"] + ")"), ""])
            if not row["captures"]:
                lines.append("No assets saved. " + md(row["status"] + ": " + row["reason"]))
            for asset_id, asset in row["captures"].items():
                timing = []
                if "media_duration_seconds" in asset:
                    timing.append(f"media duration: {asset['media_duration_seconds']} s")
                elif asset["kind"] == "original_audio":
                    timing.append("media duration: unavailable")
                if "last_end_seconds" in asset:
                    timing.append(f"last official caption end: {asset['last_end_seconds']} s")
                elif "last_start_seconds" in asset:
                    timing.append(f"last official cue start: {asset['last_start_seconds']} s (no observed end)")
                elif asset["kind"] == "official_text":
                    timing.append("caption timing: unavailable in raw TXT")
                difference = asset.get("duration_difference_seconds")
                timing.append("duration difference: " + (f"{difference} s ({asset['duration_difference_basis']})" if difference is not None else "unavailable (no comparable per-asset duration)"))
                details = [asset_id, asset["kind"], "source method: " + asset["source"]["method"],
                           "validation: " + asset["validation_level"], *timing]
                if "source_duration_seconds" in asset:
                    details.append(f"listed asset duration: {asset['source_duration_seconds']} s")
                if asset.get("media_validation_unavailable"):
                    details.append("media_validation_unavailable: true — audio streams, duration and playback remain unverified")
                elif asset["kind"] == "original_audio":
                    details.append("full playback/decoding not tested")
                lines.append("- " + "; ".join(md(value) for value in details) + ".")
        atomic_write(safe_target(root, "recordings.csv"), stream.getvalue().encode("utf-8-sig"))
        atomic_write(safe_target(root, "recordings.md"), ("\n".join(lines) + "\n").encode())
        readme = """# Recording archive

See [recordings.md](recordings.md), [recordings.csv](recordings.csv), or [recordings.json](recordings.json) for the complete inventory and per-record status.

Official VTT/SRT bytes are kept alongside a readable transcript.txt. official.txt keeps downloaded TXT bytes. official-cues.json contains an exported official player transcript; its transcript.txt preserves only observed start times, without invented end times. Audio files are original bytes, never transcribed or transcoded.

The inventory, source-list discovery assertion, local byte hashes, validation levels, failures and pending items are separate evidence. A complete inventory archive does not imply every audio file received full playback validation. When ffprobe is unavailable, media_validation_unavailable is true; container signature/size checks do not prove a complete or decodable audio stream. ffprobe checks streams/duration, not full playback.

Duration differences are observed duration or last official caption end minus the listed duration. A short caption span may be legitimate and is not filled in. DOM starts alone do not establish coverage. Page URLs omit query strings/fragments; authentication secrets are not needed for this local archive.

Re-run the helper's finalize command to refresh reports and verify every registered file's size and SHA-256. Exit code 2 means the report is incomplete against the declared discovery/inventory. Files are grouped by course/date/stable recording identity. Source files elsewhere are not changed.
"""
        atomic_write(safe_target(root, "README.md"), readme.encode())
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("init", "ingest", "mark", "finalize", "verify"):
        item = sub.add_parser(command)
        item.add_argument("--root", type=Path, required=True)
        if command == "init":
            item.add_argument("--inventory", type=Path, required=True)
        elif command == "ingest":
            item.add_argument("--capture", type=Path, required=True)
            item.add_argument("--ffprobe", default="auto", help="auto (PATH), none (explicitly weaker checks), or executable path")
        elif command == "mark":
            item.add_argument("--course-key", required=True)
            item.add_argument("--recording-id", required=True)
            item.add_argument("--status", choices=("blocked", "failed", "pending"), required=True)
            item.add_argument("--reason", required=True)
    args = parser.parse_args(argv)
    root = args.root.expanduser().resolve()
    try:
        if args.command == "init":
            result = init_archive(root, args.inventory)
            print(json.dumps({"inventory_records": len(result["records"]), "root": str(root)}))
        elif args.command == "ingest":
            result = ingest(root, args.capture, args.ffprobe)
            print(json.dumps({"recording_id": result["recording_id"], "status": result["status"], "validation_level": capture_summary(result)["validation_level"]}))
        elif args.command == "mark":
            result = mark(root, args.course_key, args.recording_id, args.status, args.reason)
            print(json.dumps({"recording_id": result["recording_id"], "status": result["status"]}))
        else:
            result = report(root, write=args.command == "finalize")
            print(json.dumps({k: result[k] for k in ("complete", "complete_against_inventory", "counts", "media_validation_unavailable_count")}))
            return 0 if result["complete"] else 2
        return 0
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError, TypeError, KeyError) as exc:
        print("Error: " + str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
