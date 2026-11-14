"""Synthetic-only helper tests; no browser, course files or credentials."""
import base64
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest
from unittest.mock import patch
import wave

SCRIPT = Path(__file__).parents[1] / "skills/lms-course-archive/scripts/recording_archive.py"
spec = importlib.util.spec_from_file_location("recording_archive", SCRIPT)
archive = importlib.util.module_from_spec(spec)
spec.loader.exec_module(archive)


class ArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="synthetic-recording-", dir=Path(__file__).parent)
        self.root = Path(self.temp.name) / "output"
        self.inputs = Path(self.temp.name)
        self.inventory = {"discovery": {"complete": True, "notes": "Synthetic two-row list"}, "courses": [{
            "key": "UNIT", "title": "Synthetic course", "source_url": "https://example.invalid/course?pwd=remove-me", "recordings": [
                {"recording_id": "one", "title": "Same title", "listed_start": "2026-11-01 10:00", "session_date": "2026-11-01", "duration_seconds": 20},
                {"recording_id": "two", "title": "Same title", "listed_start": "2026-11-02 10:00", "session_date": "2026-11-02"}]}]}
        archive.init_archive(self.root, self.write("inventory.json", self.inventory))

    def tearDown(self):
        self.temp.cleanup()

    def write(self, name, value):
        path = self.inputs / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        return path

    def payload(self, **extra):
        result = {"course_key": "UNIT", "recording_id": "one", "kind": "official_vtt", "coverage_confirmed": True,
                  "text": "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\n<v 老师>你好 &amp; 世界</v>\n",
                  "source": {"official": True, "method": "official player caption response"}}
        result.update(extra)
        return result

    def ingest(self, payload, ffprobe="none"):
        return archive.ingest(self.root, self.write("capture.json", payload), ffprobe)

    def test_vtt_chinese_speaker_exact_source_idempotence_and_missing_inventory(self):
        payload = self.payload()
        source = self.inputs / "download.vtt"
        data = b"\xef\xbb\xbf" + payload.pop("text").replace("\n", "\r\n").encode()
        source.write_bytes(data)
        payload["source_file"] = source.name
        result = self.ingest(payload)
        files = result["captures"]["main"]["files"]
        self.assertEqual((self.root / files[0]["path"]).read_bytes(), data)
        txt = (self.root / files[1]["path"]).read_text()
        self.assertIn("老师: 你好 & 世界", txt)
        self.assertIn("00:00:01.000 --> 00:00:03.000", txt)
        self.assertEqual(self.ingest(payload)["captures"]["main"]["files"], files)
        report = archive.report(self.root)
        self.assertFalse(report["complete"])
        self.assertEqual(report["counts"], {"success": 1, "blocked": 0, "failed": 0, "pending": 1})
        self.assertEqual(report["records"][0]["duration_difference_seconds"], -17)
        self.assertNotIn("pwd=", (self.root / "recordings.json").read_text())

    def test_srt_and_same_title_distinct_recordings(self):
        first = self.ingest(self.payload())
        second = self.ingest(self.payload(recording_id="two", kind="official_srt", text="1\n00:00:02,125 --> 00:00:04,000\n同学：问题\n"))
        self.assertNotEqual(first["captures"]["main"]["files"][0]["path"], second["captures"]["main"]["files"][0]["path"])
        txt = self.root / second["captures"]["main"]["files"][1]["path"]
        self.assertIn("00:00:02.125 --> 00:00:04.000", txt.read_text())
        self.assertIn("同学：问题", txt.read_text())
        self.assertTrue(archive.report(self.root)["complete"])

    def test_conflict_keeps_original_and_reports_failure(self):
        initial = self.ingest(self.payload())
        source = self.root / initial["captures"]["main"]["files"][0]["path"]
        original = source.read_bytes()
        with self.assertRaisesRegex(ValueError, "conflict"):
            self.ingest(self.payload(text="WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nChanged\n"))
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(archive.report(self.root)["counts"]["failed"], 1)
        self.assertEqual(self.ingest(self.payload())["status"], "success")

    def test_dom_has_only_official_start_no_invented_end(self):
        payload = self.payload(kind="official_dom_cues", cues=[{"start": "00:01:02", "speaker": "老师", "text": "中文原话"}])
        del payload["text"]
        row = self.ingest(payload)
        capture = row["captures"]["main"]
        self.assertNotIn("last_end_seconds", capture)
        txt = (self.root / capture["files"][1]["path"]).read_text()
        self.assertIn("[00:01:02.000]", txt)
        self.assertNotIn("-->", txt)

    def test_official_txt_exact_no_timestamps(self):
        row = self.ingest(self.payload(kind="official_text", text="老师：这是官方文本。\n"))
        data = (self.root / row["captures"]["main"]["files"][0]["path"]).read_text()
        self.assertEqual(data, "老师：这是官方文本。\n")
        with self.assertRaisesRegex(ValueError, "official"):
            self.ingest(self.payload(kind="official_text", text="hello", source={"method": "unknown"}))

    def audio(self):
        out = io.BytesIO()
        with wave.open(out, "wb") as sound:
            sound.setnchannels(1)
            sound.setsampwidth(2)
            sound.setframerate(8000)
            sound.writeframes(b"\0\0" * 800)
        return out.getvalue()

    def test_complete_audio_base64_and_absent_ffprobe(self):
        data = self.audio()
        payload = self.payload(kind="original_audio", body_base64=base64.b64encode(data).decode(), expected_bytes=len(data),
                               source={"method": "official audio response", "http_status": 206, "content_range": f"bytes 0-{len(data)-1}/{len(data)}"})
        del payload["text"]
        with patch.object(archive.shutil, "which", return_value=None):
            row = self.ingest(payload, ffprobe="auto")
        capture = row["captures"]["main"]
        self.assertTrue(capture["media_validation_unavailable"])
        self.assertTrue(capture["full_content_range_verified"])
        self.assertEqual(capture["validation_level"], "container_header_and_size_only")
        self.assertNotIn("media_duration_seconds", capture)
        self.assertEqual((self.root / capture["files"][0]["path"]).read_bytes(), data)
        archive.report(self.root)
        self.assertIn("media_validation_unavailable: true", (self.root / "recordings.md").read_text())

    def test_partial_ranges_missing_size_html_and_empty_rejected(self):
        data = self.audio()
        payload = self.payload(kind="original_audio", body_base64=base64.b64encode(data).decode(), expected_bytes=len(data),
                               source={"method": "official audio response", "http_status": 206, "content_range": f"bytes 1-{len(data)}/{len(data)+1}"})
        del payload["text"]
        with self.assertRaisesRegex(ValueError, "partial"):
            self.ingest(payload)
        del payload["expected_bytes"]
        payload["source"]["content_range"] = f"bytes 0-{len(data)-1}/{len(data)}"
        with self.assertRaisesRegex(ValueError, "expected_bytes"):
            self.ingest(payload)
        for kind in ("official_vtt", "official_srt", "official_text"):
            with self.assertRaisesRegex(ValueError, "HTML"):
                self.ingest(self.payload(kind=kind, text="<!DOCTYPE html><html>Login</html>"))
        bad = self.inputs / "audio.m4a"
        bad.write_text("<html>not audio</html>")
        payload = self.payload(kind="original_audio", source_file=bad.name)
        del payload["text"]
        with self.assertRaisesRegex(ValueError, "HTML"):
            self.ingest(payload)
        bad.write_bytes(b"")
        with self.assertRaisesRegex(ValueError, "empty"):
            self.ingest(payload)
        self.assertEqual(archive.report(self.root)["counts"]["success"], 0)

    def test_multiple_parts_need_explicit_coverage_and_expected_parts(self):
        other_root = self.inputs / "parts"
        self.inventory["courses"][0]["recordings"][0]["expected_parts"] = ["part-1", "part-2"]
        archive.init_archive(other_root, self.write("parts.json", self.inventory))
        self.root = other_root
        one = self.ingest(self.payload(asset_id="part-1", coverage_confirmed=True, duration_seconds=20))
        self.assertEqual(one["status"], "pending")
        two = self.ingest(self.payload(asset_id="part-2", coverage_confirmed=False, duration_seconds=10))
        self.assertEqual(two["status"], "pending")
        final = self.ingest(self.payload(asset_id="part-2", coverage_confirmed=True, duration_seconds=10))
        self.assertEqual(final["status"], "success")
        self.assertEqual(len(final["captures"]), 2)
        self.assertEqual(len(archive.report(self.root)["records"][0]["capture_summary"]["files"]), 4)
        markdown = (self.root / "recordings.md").read_text()
        self.assertIn("[part-1/official.vtt]", markdown)
        self.assertIn("[part-2/transcript.txt]", markdown)
        self.assertIn("source method: official player caption response", markdown)
        self.assertIn("duration difference: -17.0 s", markdown)
        self.assertIn("duration difference: -7.0 s", markdown)
        self.assertIn("last official caption end: 3.0 s", markdown)

    def test_local_audio_mark_status_and_tamper_detection(self):
        audio = self.inputs / "audio.wav"
        audio.write_bytes(self.audio())
        payload = self.payload(kind="original_audio", source_file=audio.name)
        del payload["text"]
        row = self.ingest(payload)
        archive.mark(self.root, "UNIT", "two", "blocked", "Recording access requires a login")
        report = archive.report(self.root)
        self.assertEqual(report["counts"]["blocked"], 1)
        (self.root / row["captures"]["main"]["files"][0]["path"]).write_bytes(b"changed")
        report = archive.report(self.root)
        self.assertEqual(report["counts"]["failed"], 1)
        self.assertFalse(report["complete"])

    def test_discovery_not_confirmed_and_no_coverage_assertion(self):
        row = self.ingest(self.payload(coverage_confirmed=False))
        self.assertEqual(row["status"], "pending")
        self.ingest(self.payload())
        self.ingest(self.payload(recording_id="two"))
        self.inventory["discovery"]["complete"] = False
        archive.init_archive(self.root, self.write("inventory.json", self.inventory))
        report = archive.report(self.root)
        self.assertTrue(report["complete_against_inventory"])
        self.assertFalse(report["complete"])

    def test_inventory_superset_keeps_captured_files_and_marks_new_rows_pending(self):
        original = self.ingest(self.payload())
        source = self.root / original["captures"]["main"]["files"][0]["path"]
        original_bytes = source.read_bytes()
        self.inventory["courses"][0]["recordings"].append({"recording_id": "late-found", "title": "Newly found", "listed_start": "Nov 3, 2026"})
        self.inventory["courses"].append({"key": "NEW", "title": "Added course", "source_url": "https://example.invalid/new", "recordings": []})
        merged = archive.init_archive(self.root, self.write("expanded.json", self.inventory))
        self.assertEqual(len(merged["records"]), 3)
        self.assertEqual(merged["records"][0]["captures"], original["captures"])
        self.assertEqual(merged["records"][0]["status"], "success")
        self.assertEqual(source.read_bytes(), original_bytes)
        self.assertEqual(merged["records"][-1]["status"], "pending")
        self.assertFalse(archive.report(self.root)["complete"])
        self.inventory["courses"][0]["recordings"].pop()
        with self.assertRaisesRegex(ValueError, "missing/conflicting"):
            archive.init_archive(self.root, self.write("missing.json", self.inventory))
        self.assertEqual(len(archive.load_state(self.root)["records"]), 3)
        self.inventory["courses"][0]["recordings"][0]["listed_start"] = "Changed identity metadata"
        with self.assertRaisesRegex(ValueError, "missing/conflicting"):
            archive.init_archive(self.root, self.write("changed.json", self.inventory))
        self.assertEqual(source.read_bytes(), original_bytes)

    def test_real_cli_end_to_end_exit_codes_and_reports(self):
        cli_root = self.inputs / "cli-output"
        def run(command, *args):
            return subprocess.run([sys.executable, "-B", str(SCRIPT), command, "--root", str(cli_root), *map(str, args)], capture_output=True, text=True)
        result = run("init", "--inventory", self.inputs / "inventory.json")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(run("finalize").returncode, 2)
        for rid in ("one", "two"):
            capture = self.write("cli-capture.json", self.payload(recording_id=rid))
            result = run("ingest", "--capture", capture)
            self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(run("finalize").returncode, 0)
        self.assertEqual(run("verify").returncode, 0)
        for filename in ("README.md", "recordings.md", "recordings.csv", "recordings.json"):
            self.assertTrue((cli_root / filename).is_file())
        bad = self.write("bad.json", self.payload(text="<html>Login</html>"))
        self.assertEqual(run("ingest", "--capture", bad).returncode, 1)
        self.assertEqual(run("verify").returncode, 2)

    def test_caption_code_markup_preserved_and_partial_caption_range_rejected(self):
        text = "WEBVTT\n\n00:00:01.000 --> 00:00:02.000\n<v 老师>Use <html> and <MyType>, <b>bold</b>, <00:00:01.500>later.</v>\n"
        payload = self.payload(text=text)
        payload["source"].update({"http_status": 206, "content_range": f"bytes 0-{len(text.encode())-1}/{len(text.encode())+10}"})
        with self.assertRaisesRegex(ValueError, "partial"):
            self.ingest(payload)
        payload["source"]["content_range"] = f"bytes 0-{len(text.encode())-1}/{len(text.encode())}"
        row = self.ingest(payload)
        txt = (self.root / row["captures"]["main"]["files"][1]["path"]).read_text()
        self.assertIn("老师: Use <html> and <MyType>, bold, later.", txt)
        dom = self.payload(recording_id="two", kind="official_dom_cues", cues=[{"start": 1, "text": "<html> is a code tag"}])
        del dom["text"]
        self.assertEqual(self.ingest(dom)["status"], "success")


if __name__ == "__main__":
    unittest.main()
