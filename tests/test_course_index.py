#!/usr/bin/env python3
"""Synthetic, offline tests for the published course index CLI."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "skills/lms-course-archive/scripts/build_course_index.py"
OUTPUTS = ("00_总目录_README.md", "下载清单.csv", "归档清单.json", "未完成与缺失.md")


class CourseIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / "archive"
        self.root.mkdir()
        self.inventory = {
            "courses": [{"key": "SYNTH101", "title": "合成课程", "source_url": "https://lms.example.invalid/course"}],
            "discovery": {"complete": True, "notes": "Synthetic test. Scope excludes forums; exclusion is not download success."},
            "items": [], "recording_archives": [],
        }
        self.materials = []
        self.inventory_path = self.base / "inventory.json"
        self.materials_path = self.base / "materials.json"

    def item(self, item_id="lesson-1"):
        value = {"course_key": "SYNTH101", "item_id": item_id, "title": "中文课件 " + item_id, "section": "课程材料", "kind": "page", "source_url": "https://lms.example.invalid/item/" + item_id}
        self.inventory["items"].append(value)
        return value

    def file(self, path="材料/中文页.html", content=b"<html>Synthetic source only</html>"):
        actual = self.root / path
        actual.parent.mkdir(parents=True, exist_ok=True)
        actual.write_bytes(content)
        return {"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()}

    def material(self, item_id="lesson-1", status="ok", files=None, note=""):
        value = {"course_key": "SYNTH101", "item_id": item_id, "status": status, "files": files if files is not None else [], "note": note}
        self.materials.append(value)
        return value

    def recording(self, status="success", complete=True):
        folder = "SYNTH101/08_Recordings"
        self.inventory["recording_archives"].append({"course_key": "SYNTH101", "path": folder})
        file = self.file(folder + "/session/audio.wav", b"RIFFsynthetic-audio")
        file["path"] = "session/audio.wav"
        data = {"courses": [{"key": "SYNTH101"}], "discovery": {"complete": True}, "complete": complete, "complete_against_inventory": complete, "total_recordings": 1, "records": [{"course_key": "SYNTH101", "recording_id": "session-1", "title": "合成录播", "listed_start": "Nov 1, 2026 10:00 AM", "status": status, "reason": "synthetic reason" if status != "success" else "", "coverage_confirmed": True, "expected_parts": ["main"], "captures": {"main": {"kind": "original_audio", "media_validation_unavailable": True, "files": [file]}}}]}
        path = self.root / folder / "recordings.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path, data

    def run_cli(self, expected=0, jsonl=False, omit_materials=False):
        self.inventory_path.write_text(json.dumps(self.inventory), encoding="utf-8")
        if jsonl:
            self.materials_path = self.base / "materials.jsonl"
            self.materials_path.write_text("\n".join(json.dumps(item) for item in self.materials) + "\n", encoding="utf-8")
        else:
            self.materials_path.write_text(json.dumps({"items": self.materials}), encoding="utf-8")
        args = [sys.executable, str(SCRIPT), "--root", str(self.root), "--inventory", str(self.inventory_path)]
        if not omit_materials:
            args += ["--materials", str(self.materials_path)]
        result = subprocess.run(args, text=True, capture_output=True)
        self.assertEqual(result.returncode, expected, result.stdout + result.stderr)
        return result

    def report(self):
        return json.loads((self.root / "归档清单.json").read_text())

    def test_material_only_archive_checks_actual_bytes_and_preserves_source(self):
        self.item()
        file = self.file()
        self.material(files=[file])
        before = (self.root / file["path"]).read_bytes()
        self.run_cli()
        self.assertTrue(self.report()["complete"])
        self.assertEqual(self.report()["material_counts"]["ok"], 1)
        self.assertTrue(self.report()["materials"][0]["files"][0]["verified"])
        self.assertEqual((self.root / file["path"]).read_bytes(), before)
        self.assertTrue(all((self.root / name).is_file() for name in OUTPUTS))
        self.assertIn("%E4%B8%AD%E6%96%87", (self.root / OUTPUTS[0]).read_text())
        self.assertIn("scope exclusions", (self.root / OUTPUTS[0]).read_text())

    def test_jsonl_missing_inventory_item_stays_pending(self):
        self.item()
        self.item("lesson-2")
        self.material(files=[self.file()])
        self.run_cli(2, jsonl=True)
        self.assertEqual([item["status"] for item in self.report()["materials"]], ["ok", "pending"])
        self.assertIn("lesson-2", (self.root / "未完成与缺失.md").read_text())

    def test_ok_without_archive_file_is_failed(self):
        self.item()
        self.material()
        self.run_cli(2)
        item = self.report()["materials"][0]
        self.assertEqual((item["declared_status"], item["status"]), ("ok", "failed"))

    def test_empty_file_with_matching_hash_is_failed_and_preserved(self):
        self.item()
        file = self.file(content=b"")
        self.material(files=[file])
        self.run_cli(2)
        item = self.report()["materials"][0]
        self.assertEqual((item["declared_status"], item["status"]), ("ok", "failed"))
        self.assertFalse(item["files"][0]["verified"])
        self.assertEqual(item["files"][0]["observed_bytes"], 0)
        self.assertEqual((self.root / file["path"]).read_bytes(), b"")

    def test_source_urls_drop_query_and_fragment_without_changing_input(self):
        item = self.item()
        self.inventory["courses"][0]["source_url"] = "https://lms.example.invalid/course?passcode=synthetic-secret#session-token"
        item["source_url"] = "https://lms.example.invalid/item?resource=123&token=synthetic-token#temporary-secret"
        self.material(files=[self.file()])
        self.run_cli()
        report = self.report()
        self.assertEqual(report["courses"][0]["source_url"], "https://lms.example.invalid/course")
        self.assertEqual(report["materials"][0]["source_url"], "https://lms.example.invalid/item")
        self.assertEqual(report["materials"][0]["item_id"], "lesson-1")
        for name in OUTPUTS:
            content = (self.root / name).read_text()
            for secret in ("synthetic-secret", "session-token", "synthetic-token", "temporary-secret"):
                self.assertNotIn(secret, content)
        self.assertIn("synthetic-secret", self.inventory_path.read_text())

    def test_non_http_or_userinfo_source_urls_are_invalid(self):
        item = self.item()
        self.material(files=[self.file()])
        for value in ("file:///tmp/private", "javascript:alert(1)", "https://user:password@lms.example.invalid/course", "https://@lms.example.invalid/course", "https://lms.example.invalid:invalid/course"):
            for target in (self.inventory["courses"][0], item):
                with self.subTest(value=value, target=target.get("item_id", "course")):
                    original = target["source_url"]
                    target["source_url"] = value
                    self.run_cli(1)
                    target["source_url"] = original

    def test_markdown_file_links_escape_labels_and_encode_targets(self):
        self.item()
        self.material(files=[self.file("材料/lecture [x](injected).html")])
        self.run_cli()
        readme = (self.root / OUTPUTS[0]).read_text()
        self.assertIn(r"[lecture \[x\](injected).html]", readme)
        self.assertIn("lecture%20%5Bx%5D%28injected%29.html", readme)

    def test_tampered_or_missing_files_cannot_complete(self):
        for state in ("tampered", "missing"):
            with self.subTest(state=state):
                self.inventory["items"] = []
                self.materials = []
                self.item()
                file = self.file()
                self.material(files=[file])
                target = self.root / file["path"]
                if state == "tampered":
                    target.write_bytes(b"wrong bytes")
                else:
                    target.unlink()
                self.run_cli(2)
                self.assertEqual(self.report()["material_counts"]["failed"], 1)

    def test_not_available_requires_reason_and_is_not_counted_downloaded(self):
        self.item()
        item = self.material(status="not_available")
        self.run_cli(1)
        self.assertFalse((self.root / "归档清单.json").exists())
        item["note"] = "The synthetic source explicitly reports this retired item unavailable."
        self.run_cli()
        self.assertEqual(self.report()["material_counts"]["not_available"], 1)
        self.assertEqual(self.report()["material_counts"]["ok"], 0)
        self.assertIn("not_available", (self.root / "未完成与缺失.md").read_text())

    def test_non_success_statuses_are_separately_counted(self):
        for status in ("failed", "blocked", "pending", "skipped", "needs_login", "needs_user"):
            self.item(status)
            self.material(status, status=status, note="synthetic outstanding action")
        self.run_cli(2)
        for status in ("failed", "blocked", "pending", "skipped", "needs_login", "needs_user"):
            self.assertEqual(self.report()["material_counts"][status], 1)

    def test_unknown_and_duplicate_material_ids_are_invalid(self):
        self.item()
        self.material("unknown", status="blocked")
        self.run_cli(1)
        self.materials = []
        self.material(status="blocked")
        self.material(status="pending")
        self.run_cli(1)

    def test_duplicate_inventory_ids_are_invalid(self):
        self.item()
        self.item()
        self.run_cli(1)

    def test_path_escape_and_external_symlink_are_rejected(self):
        outside = self.base / "outside.txt"
        outside.write_bytes(b"external secret synthetic")
        self.item()
        item = self.material(files=[{"path": "../outside.txt", "bytes": 25, "sha256": "0" * 64}])
        self.run_cli(1)
        (self.root / "linked.txt").symlink_to(outside)
        item["files"][0]["path"] = "linked.txt"
        self.run_cli(1)
        self.assertEqual(outside.read_bytes(), b"external secret synthetic")

    def test_reserved_output_cannot_overwrite_source(self):
        self.item()
        file = self.file("00_总目录_README.md", b"source bytes")
        self.material(files=[file])
        self.run_cli(1)
        self.assertEqual((self.root / file["path"]).read_bytes(), b"source bytes")

    def test_discovery_false_blocks_completion(self):
        self.item()
        self.material(files=[self.file()])
        self.inventory["discovery"]["complete"] = False
        self.run_cli(2)
        self.assertFalse(self.report()["complete"])

    def test_recordings_only_reports_weaker_media_validation(self):
        path, data = self.recording()
        before = path.read_bytes()
        self.run_cli(omit_materials=True)
        report = self.report()
        self.assertTrue(report["complete"])
        self.assertEqual(report["recording_count"], 1)
        self.assertEqual(report["recording_archives"][0]["media_validation_unavailable_count"], 1)
        self.assertIn("完整播放未验证", (self.root / OUTPUTS[0]).read_text())
        self.assertEqual(path.read_bytes(), before)

    def test_missing_expected_recording_archive_stays_pending(self):
        self.inventory["recording_archives"] = [{"course_key": "SYNTH101", "path": "SYNTH101/08_Recordings"}]
        self.run_cli(2, omit_materials=True)
        self.assertEqual(self.report()["recording_archives"][0]["status"], "pending")

    def test_recording_complete_flag_cannot_hide_blocked_or_missing_assets(self):
        path, data = self.recording(status="blocked", complete=True)
        self.run_cli(2)
        self.assertEqual(self.report()["recording_archives"][0]["records"][0]["status"], "blocked")
        data["records"][0]["status"] = "success"
        data["records"][0]["expected_parts"].append("missing-part")
        path.write_text(json.dumps(data), encoding="utf-8")
        self.run_cli(2)
        self.assertEqual(self.report()["recording_archives"][0]["records"][0]["status"], "failed")

    def test_recording_file_hash_is_rechecked(self):
        self.recording()
        (self.root / "SYNTH101/08_Recordings/session/audio.wav").write_bytes(b"changed")
        self.run_cli(2)
        record = self.report()["recording_archives"][0]["records"][0]
        self.assertEqual(record["status"], "failed")
        self.assertTrue(record["validation_errors"])

    def test_recording_wrong_course_or_external_file_is_invalid(self):
        path, data = self.recording()
        data["records"][0]["course_key"] = "OTHER"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.run_cli(1)
        data["records"][0]["course_key"] = "SYNTH101"
        data["records"][0]["captures"]["main"]["files"][0]["path"] = "../../outside.wav"
        path.write_text(json.dumps(data), encoding="utf-8")
        self.run_cli(1)

    def test_module_scope_must_be_explicit(self):
        del self.inventory["recording_archives"]
        self.run_cli(1)


if __name__ == "__main__":
    unittest.main()
