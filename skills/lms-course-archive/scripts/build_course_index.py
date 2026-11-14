#!/usr/bin/env python3
"""Build a local LMS index from an explicit inventory and verified manifests.

Python 3.10+, standard library only. No network, downloads, transcription, or
changes to source files. Exit 0: reconciled; 2: partial reports; 1: invalid input.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.parse import quote, urlsplit, urlunsplit


STATUSES = ("ok", "failed", "blocked", "pending", "skipped", "not_available", "needs_login", "needs_user")
OUTPUTS = ("00_总目录_README.md", "下载清单.csv", "归档清单.json", "未完成与缺失.md")


class InputError(ValueError):
    pass


def obj(value, label):
    if not isinstance(value, dict):
        raise InputError(f"{label} 必须是 JSON 对象")
    return value


def array(value, label):
    if not isinstance(value, list):
        raise InputError(f"{label} 必须是数组")
    return value


def string(value, label, allow_empty=False):
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        raise InputError(f"{label} 必须是{'非空' if not allow_empty else ''}字符串")
    return value


def source_url(value, label):
    value = string(value, label)
    try:
        parts = urlsplit(value)
        # Accessing port also rejects malformed port/IPv6 syntax.
        parts.port
        valid = parts.scheme in ("http", "https") and bool(parts.hostname) and parts.username is None and parts.password is None
    except ValueError as exc:
        raise InputError(f"{label} 不是有效的 HTTP(S) 来源 URL") from exc
    if not valid or any(character.isspace() or ord(character) < 32 for character in value):
        raise InputError(f"{label} 必须是无 userinfo 的 HTTP(S) URL，且不能含空白或控制字符")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError(f"无法读取 JSON {path}: {exc}") from exc


def local_path(root, relative, label):
    value = string(relative, label)
    path = Path(value)
    # Reject Windows separators/drive prefixes on every platform as well.
    if path.is_absolute() or ".." in path.parts or "\\" in value or re.match(r"^[A-Za-z]:", value):
        raise InputError(f"{label} 必须是根目录内的相对路径: {value}")
    result = root / path
    try:
        result.resolve().relative_to(root.resolve())
    except (ValueError, OSError, RuntimeError) as exc:
        raise InputError(f"{label} 越界或包含指向外部的符号链接: {value}") from exc
    return result


def file_checks(root, files, label, archive_root=None):
    """Validate metadata before touching content; invalid bytes are report failures."""
    checked, errors = [], []
    for index, value in enumerate(array(files, label)):
        entry = obj(value, f"{label}[{index}]")
        path = local_path(archive_root or root, entry.get("path"), f"{label}[{index}].path")
        relative = path.relative_to(root).as_posix()
        if path.resolve() in {(root / name).resolve() for name in OUTPUTS}:
            raise InputError(f"原件不能使用总索引保留文件名: {relative}")
        size = entry.get("bytes")
        digest = entry.get("sha256")
        if type(size) is not int or size < 0:
            raise InputError(f"{label}[{index}].bytes 必须是非负整数")
        if not isinstance(digest, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", digest):
            raise InputError(f"{label}[{index}].sha256 必须是 64 位十六进制值")
        saved = {"path": relative, "bytes": size, "sha256": digest.lower(), "verified": False}
        if not path.is_file():
            errors.append(f"文件缺失或不是普通文件: {relative}")
        else:
            try:
                actual_size = path.stat().st_size
                hasher = hashlib.sha256()
                with path.open("rb") as handle:
                    for block in iter(lambda: handle.read(1024 * 1024), b""):
                        hasher.update(block)
                saved["observed_bytes"] = actual_size
                saved["observed_sha256"] = hasher.hexdigest()
                saved["verified"] = actual_size > 0 and actual_size == size and hasher.hexdigest() == digest.lower()
                if actual_size == 0:
                    errors.append(f"文件为空，不能算归档成功；空白源页面需要非空说明文件: {relative}")
                elif not saved["verified"]:
                    errors.append(f"文件大小或 SHA-256 不符: {relative}")
            except OSError as exc:
                errors.append(f"文件无法读取: {relative}: {exc}")
        checked.append(saved)
    return checked, errors


def material_entries(path):
    if path is None:
        return []
    if path.suffix.lower() == ".jsonl":
        result = []
        try:
            for line_no, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
                if line.strip():
                    result.append(obj(json.loads(line), f"材料清单第 {line_no} 行"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise InputError(f"无法读取 JSONL {path}: {exc}") from exc
        return result
    data = read_json(path)
    if isinstance(data, dict):
        data = data.get("items")
    return array(data, "materials / materials.items")


def identity(entry, label):
    obj(entry, label)
    return string(entry.get("course_key"), label + ".course_key"), string(entry.get("item_id"), label + ".item_id")


def validate_inventory(inventory):
    obj(inventory, "inventory")
    courses = {}
    for course in array(inventory.get("courses"), "inventory.courses"):
        obj(course, "course")
        key = string(course.get("key"), "course.key")
        if key in courses:
            raise InputError(f"重复课程键: {key}")
        courses[key] = {field: string(course.get(field), f"course.{field}") for field in ("key", "title", "source_url")}
        courses[key]["source_url"] = source_url(course["source_url"], "course.source_url")
    if not courses:
        raise InputError("inventory.courses 不能为空")
    discovery = obj(inventory.get("discovery", {}), "inventory.discovery")
    if type(discovery.get("complete", False)) is not bool:
        raise InputError("discovery.complete 必须是布尔值")
    discovery = {"complete": discovery.get("complete", False), "notes": string(discovery.get("notes", ""), "discovery.notes", True)}
    if "items" not in inventory or "recording_archives" not in inventory:
        raise InputError("inventory 必须明确提供 items 与 recording_archives 数组；不在范围内的模块用 [] 并在 discovery.notes 说明")
    items = {}
    for item in array(inventory["items"], "inventory.items"):
        key = identity(item, "inventory.item")
        if key[0] not in courses or key in items:
            raise InputError(f"未知课程或重复材料身份: {key}")
        items[key] = {field: string(item.get(field), f"item.{field}") for field in ("course_key", "item_id", "title", "section", "kind", "source_url")}
        items[key]["source_url"] = source_url(item["source_url"], "item.source_url")
    archives, seen = [], set()
    for entry in array(inventory["recording_archives"], "inventory.recording_archives"):
        obj(entry, "recording_archive")
        key = string(entry.get("course_key"), "recording_archive.course_key")
        path = string(entry.get("path"), "recording_archive.path")
        if key not in courses or (key, path) in seen:
            raise InputError(f"未知课程或重复录播归档: {(key, path)}")
        seen.add((key, path))
        archives.append({"course_key": key, "path": path})
    return list(courses.values()), discovery, items, archives


def reconcile_materials(root, expected, manifest):
    observed = {}
    for entry in manifest:
        key = identity(entry, "material")
        if key not in expected or key in observed:
            raise InputError(f"未知或重复材料身份: {key}")
        status = entry.get("status")
        if status not in STATUSES:
            raise InputError(f"材料 {key} 状态不支持: {status}")
        note = string(entry.get("note", ""), f"材料 {key} note", True)
        if status == "not_available" and not note.strip():
            raise InputError(f"not_available 必须有具体理由: {key}")
        files, errors = file_checks(root, entry.get("files", []), f"材料 {key} files")
        if status == "ok" and not files:
            errors.append("声明 ok 但没有归档文件；空白源页面也需要保存记录文件")
        observed[key] = {**expected[key], "declared_status": status, "status": "failed" if errors else status, "note": note, "files": files, "validation_errors": errors}
    return [observed.get(key, {**item, "declared_status": None, "status": "pending", "note": "inventory 中有此项，但材料清单缺少记录", "files": [], "validation_errors": []}) for key, item in expected.items()]


def reconcile_recordings(root, expected):
    results, seen_ids, seen_paths = [], set(), set()
    for entry in expected:
        archive_root = local_path(root, entry["path"], "recording_archive.path")
        if archive_root.resolve() in seen_paths:
            raise InputError(f"重复指向同一录播归档目录: {entry['path']}")
        seen_paths.add(archive_root.resolve())
        report_path = local_path(archive_root, "recordings.json", "录播清单路径")
        result = {**entry, "manifest_path": report_path.relative_to(root).as_posix(), "status": "pending", "complete": False, "records": [], "issues": [], "media_validation_unavailable_count": 0}
        if not report_path.is_file():
            result["issues"].append("预期录播 recordings.json 缺失，尚未完成录播归档")
            results.append(result)
            continue
        data = obj(read_json(report_path), str(report_path))
        manifest_courses = array(data.get("courses"), "录播 courses")
        if not manifest_courses:
            raise InputError(f"录播清单必须声明所属课程: {entry['path']}")
        for course in manifest_courses:
            if obj(course, "录播 course").get("key") != entry["course_key"]:
                raise InputError(f"录播清单课程与 inventory 不一致: {entry['path']}")
        discovery = obj(data.get("discovery"), "录播 discovery")
        records = array(data.get("records"), "录播 records")
        if data.get("total_recordings") != len(records):
            result["issues"].append("录播 total_recordings 与逐场记录数不一致")
        for record in records:
            obj(record, "录播 record")
            rid = string(record.get("recording_id"), "录播 recording_id")
            key = (entry["course_key"], rid)
            if record.get("course_key") != entry["course_key"] or key in seen_ids:
                raise InputError(f"录播课程不符或重复录播身份: {key}")
            seen_ids.add(key)
            declared = record.get("status")
            if declared not in ("success", "blocked", "failed", "pending"):
                raise InputError(f"未知录播状态: {key}: {declared}")
            captures = obj(record.get("captures", {}), "录播 captures")
            files, errors, unavailable = [], [], False
            for asset_id, capture in captures.items():
                obj(capture, f"录播 capture {asset_id}")
                checked, found = file_checks(root, capture.get("files", []), f"录播 {key}/{asset_id} files", archive_root)
                files.extend(checked)
                errors.extend(found)
                if not checked:
                    errors.append(f"录播资产 {asset_id} 没有文件")
                unavailable = unavailable or capture.get("media_validation_unavailable") is True
            parts = array(record.get("expected_parts", []), "录播 expected_parts")
            if any(not isinstance(part, str) or not part for part in parts):
                raise InputError(f"录播 expected_parts 必须包含非空字符串: {key}")
            if declared == "success":
                if record.get("coverage_confirmed") is not True:
                    errors.append("录播未确认全部必需片段覆盖")
                if not files:
                    errors.append("录播声明 success 但没有文件")
                missing = sorted(set(parts) - captures.keys())
                if missing:
                    errors.append("缺少预期录播资产: " + ", ".join(missing))
            result["records"].append({"course_key": key[0], "recording_id": rid, "title": string(record.get("title"), "录播 title"), "listed_start": string(record.get("listed_start", ""), "录播 listed_start", True), "declared_status": declared, "status": "failed" if errors else declared, "reason": string(record.get("reason", ""), "录播 reason", True), "files": files, "validation_errors": errors, "media_validation_unavailable": unavailable})
            result["media_validation_unavailable_count"] += int(unavailable)
        if discovery.get("complete") is not True:
            result["issues"].append("录播网页目录尚未声明完成盘点")
        if data.get("complete") is not True or data.get("complete_against_inventory") is not True:
            result["issues"].append("录播模块报告尚未完成")
        if any(record["status"] != "success" for record in result["records"]):
            result["issues"].append("录播存在未成功场次或本地文件校验失败")
        result["complete"] = not result["issues"]
        result["status"] = "ok" if result["complete"] else "incomplete"
        results.append(result)
    return results


def escape(value):
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def link(path, label=None):
    label = escape(label or Path(path).name).replace("[", "\\[").replace("]", "\\]")
    return f"[{label}]({quote(path, safe='/')})"


def render(report):
    counts = report["material_counts"]
    lines = ["# 课程归档总目录", "", f"目录对账完成：{'是' if report['complete'] else '否（存在未完成项或盘点未完成）'}。", "", "完成仅表示 inventory 声明范围内的材料已保存并通过本地大小/SHA-256 校验，或被逐项注明来源不可用，且预期录播模块完成。它不证明网站没有漏页，也不表示全部资源均已下载或媒体已完整播放。", "", f"材料 {len(report['materials'])} 项：" + "；".join(f"{status} {count}" for status, count in counts.items()) + "。", f"预期录播模块 {len(report['recording_archives'])} 个；录播 {report['recording_count']} 场；未完成模块 {sum(not value['complete'] for value in report['recording_archives'])} 个。", "", "not_available 是有理由的来源不可用记录，不是下载成功；skipped 是未完成。范围排除（scope exclusions）仅描述未纳入 inventory 的内容，不能记为已下载、ok 或 not_available。", "", f"范围与盘点说明：{escape(report['discovery']['notes']) or '未填写'}", f"网页目录盘点声明：{'完成' if report['discovery']['complete'] else '未完成'}。该声明由操作者提供，本地脚本无法验证网页。", "", "只生成索引，不联网、不转录、不复制或更改原件。没有 ffprobe 的音频仅有文件头/大小校验；即使 ffprobe 可用也不等于完整播放验证。", "", f"关联清单：{link('下载清单.csv')} · {link('归档清单.json')} · {link('未完成与缺失.md')}", ""]
    incomplete = ["# 未完成与缺失", "", "not_available 单列记录来源不可用，不等于下载成功；skipped 仍属未完成。", ""]
    if not report["discovery"]["complete"]:
        incomplete += ["- 网页目录盘点未完成。", ""]
    rows = []
    for course in report["courses"]:
        lines += [f"## {escape(course['key'])} — {escape(course['title'])}", "", "| 分类 / 标题 | 状态 | 文件 | 说明 |", "|---|---|---|---|"]
        for item in report["materials"]:
            if item["course_key"] != course["key"]:
                continue
            note = "; ".join([item["note"], *item["validation_errors"]]).strip("; ")
            links = " · ".join(link(value["path"]) for value in item["files"])
            lines.append(f"| {escape(item['section'])} / {escape(item['title'])} | {item['status']} | {links} | {escape(note)} |")
            rows.append(["material", course["key"], item["item_id"], item["title"], item["section"], item["kind"], item["declared_status"] or "", item["status"], item["source_url"], "; ".join(value["path"] for value in item["files"]), note])
            if item["status"] != "ok":
                incomplete.append(f"- {escape(course['key'])} / {escape(item['title'])} [{item['status']}]: {escape(note)}")
        lines.append("")
        for archive in report["recording_archives"]:
            if archive["course_key"] != course["key"]:
                continue
            lines += [f"### 录播：{escape(archive['path'])}", "", f"状态：{archive['status']}；{link(archive['manifest_path'], '录播 JSON 清单')}。", ""]
            if archive["issues"]:
                incomplete.append(f"- {escape(course['key'])} / {escape(archive['path'])}: {escape('; '.join(archive['issues']))}")
            for record in archive["records"]:
                note = "; ".join([record["reason"], *record["validation_errors"]]).strip("; ")
                if record["media_validation_unavailable"]:
                    note += ("; " if note else "") + "音轨、时长与完整播放未验证（无 ffprobe）"
                files = " · ".join(link(value["path"]) for value in record["files"])
                lines.append(f"- {escape(record['title'])} — {escape(record['listed_start'])} [{record['status']}]：{files}。{escape(note)}")
                rows.append(["recording", course["key"], record["recording_id"], record["title"], "录播", "recording", record["declared_status"], record["status"], "", "; ".join(value["path"] for value in record["files"]), note])
                if record["status"] != "success":
                    incomplete.append(f"- {escape(course['key'])} / {escape(record['title'])} [{record['status']}]: {escape(note)}")
            lines.append("")
    if len(incomplete) == 4:
        incomplete += ["无未完成项、缺失项或来源不可用项。"]
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer)
    writer.writerow(["module", "course_key", "item_id", "title", "section", "kind", "declared_status", "status", "source_url", "files", "note"])
    # Spreadsheet programs can execute formula-like cells; keep the CSV inert.
    for row in rows:
        writer.writerow(["'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value for value in row])
    return {OUTPUTS[0]: "\n".join(lines) + "\n", OUTPUTS[1]: "\ufeff" + csv_buffer.getvalue(), OUTPUTS[2]: json.dumps(report, ensure_ascii=False, indent=2) + "\n", OUTPUTS[3]: "\n".join(incomplete) + "\n"}


def build(root, inventory_path, materials_path=None):
    root = root.resolve()
    if not root.is_dir():
        raise InputError(f"归档根目录不存在或不是目录: {root}")
    for name in OUTPUTS:
        target = root / name
        if target.is_symlink() or (target.exists() and not target.is_file()):
            raise InputError(f"报告输出路径不是安全的普通文件: {target}")
        if target == inventory_path.resolve() or (materials_path is not None and target == materials_path.resolve()):
            raise InputError(f"报告不能覆盖输入清单: {target}")
    courses, discovery, expected, archive_paths = validate_inventory(read_json(inventory_path))
    materials = reconcile_materials(root, expected, material_entries(materials_path))
    archives = reconcile_recordings(root, archive_paths)
    complete = discovery["complete"] and all(item["status"] in ("ok", "not_available") for item in materials) and all(archive["complete"] for archive in archives)
    report = {"schema_version": 1, "complete": complete, "completion_basis": "Explicit inventory reconciliation, user/agent discovery assertion, and freshly checked local file sizes/SHA-256; not proof of website coverage or full media playback.", "discovery": discovery, "courses": courses, "material_counts": {status: sum(item["status"] == status for item in materials) for status in STATUSES}, "materials": materials, "recording_count": sum(len(archive["records"]) for archive in archives), "recording_archives": archives, "transcription_performed": False}
    outputs = render(report)
    for name, content in outputs.items():
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=root, prefix=".course-index-", delete=False) as handle:
                temporary = Path(handle.name)
                handle.write(content)
            os.replace(temporary, root / name)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return report


def main(argv=None):
    class Parser(argparse.ArgumentParser):
        def error(self, message):
            self.print_usage(sys.stderr)
            self.exit(1, f"error: {message}\n")

    parser = Parser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True, help="课程归档根目录；所有产物路径相对此目录")
    parser.add_argument("--inventory", type=Path, required=True, help="明确预期课程、材料与录播模块的 JSON")
    parser.add_argument("--materials", type=Path, help="材料 JSON 数组、{items: [...]} 或 JSONL；缺项保留 pending")
    args = parser.parse_args(argv)
    try:
        report = build(args.root, args.inventory, args.materials)
    except (InputError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"complete": report["complete"], "material_counts": report["material_counts"], "recording_count": report["recording_count"], "reports": list(OUTPUTS)}, ensure_ascii=False))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    sys.exit(main())
