# 可选本地整理工具与清单合同

`scripts/recording_archive.py` 使用 Python 3.10+ 标准库，只读取本地 JSON/文件。它不会登录网站、下载录播、请求网络、转码或转录。浏览器抓取由接手 AI 的工具完成；也可以不用本脚本，自己实现等价结果。

以下命令中的路径都是示例。`scripts/recording_archive.py` 相对于本 skill 文件夹，从别处调用时改用脚本的实际路径。下载文件、临时 JSON 和输出归档应放在 skill 文件夹以外的工作区；将示例中的 `./recordings-output` 等换成对应路径。串行执行修改命令，避免多个进程同时写同一 archive。

## 最短工作流程

```sh
python3 scripts/recording_archive.py --help
python3 scripts/recording_archive.py init --root ./recordings-output --inventory ./inventory.json
python3 scripts/recording_archive.py ingest --root ./recordings-output --capture ./capture.json
python3 scripts/recording_archive.py finalize --root ./recordings-output
python3 scripts/recording_archive.py verify --root ./recordings-output
```

一次 `ingest` 只处理一项资产，循环处理所有录播和片段。`init` 建目录清单，`finalize` 生成交付报告，`verify` 重新检查文件但不改写报告。先看命令返回的状态，不能只看进程退出码。

| 退出码 | 含义 |
|---|---|
| 0 | 命令执行成功；`ingest` 后仍可能 pending。`finalize/verify` 为 0 才表示本地 inventory 全部成功且已声明网页目录盘点完成 |
| 1 | 输入或操作被拒绝，需根据错误处理 |
| 2 | `finalize` 已生成报告或 `verify` 已检查，但归档仍有未完成项或尚未完成目录盘点 |

## inventory.json：网页盘点结果

```json
{
  "discovery": {
    "complete": true,
    "notes": "已检查指定课程的全部列表页及当前筛选条件；最终交付前再次复核"
  },
  "courses": [{
    "key": "EXAMPLE101",
    "title": "演示课程",
    "source_url": "https://lms.example.invalid/course/example101",
    "recordings": [{
      "recording_id": "session-2026-11-01-am",
      "title": "第一讲",
      "listed_start": "Nov 1, 2026 10:00 AM",
      "session_date": "2026-11-01",
      "timezone": "页面显示的时区原文",
      "source_url": "https://zoom.example.invalid/recording/example-session",
      "duration_seconds": 3600,
      "expected_parts": ["part-1-en", "part-2-en"]
    }]
  }]
}
```

课程必填 `key/title/source_url/recordings`。每场必填 `title` 和稳定的 `recording_id`；也可提供 `meeting_id + listed_start`，脚本会组合成身份。不同开场时间的同名会议应是不同场次，不能只用 meeting ID。标识只要求在课程内唯一。

有 recording_id 时，日期、列表时间、时区、来源 URL、详情显示时间 `detail_start`、备注 `notes`、时长及 `expected_parts` 都可选。不知道日期时脚本使用 `undated`，不猜时区。

`expected_parts` 是应覆盖的资产 ID，例如不同录播片段和所需字幕语言；同一片段的 VTT 与衍生 TXT 算一项资产。尚不清楚时可以不填，但不能省略网页逐片段检查。空 recordings 数组只能表示已实际检查为空的课程；遗漏 `discovery.complete` 默认 false。它是 AI 对网页检查的明确声明，脚本无法从本地文件验证网站有没有漏页。

`init` 可以重跑；同身份原记录保留文件和状态。最终盘点有新增场次时，用保留旧条目的扩充 inventory 再次初始化，不删除旧目录。已有条目发生实质冲突时需要核对，不以新数据静默替换旧归档。详见命令返回信息。

## capture.json：官方 VTT / SRT

```json
{
  "course_key": "EXAMPLE101",
  "recording_id": "session-2026-11-01-am",
  "asset_id": "part-1-en",
  "kind": "official_vtt",
  "source_file": "downloads/captions.vtt",
  "coverage_confirmed": false,
  "duration_seconds": 1800,
  "source": {
    "official": true,
    "method": "从已授权官方播放器下载完整字幕文件",
    "page_url": "https://zoom.example.invalid/recording/example-session",
    "mime_type": "text/vtt",
    "http_status": 200,
    "observed_at": "2026-11-14T10:00:00+08:00"
  }
}
```

`course_key + recording_id` 必须匹配 inventory。`asset_id` 默认 `main`；多片段、多语言或用户额外要求的音频使用各自稳定 ID。`kind` 为 `official_srt` 时解析 SRT；脚本保留源文件的精确字节，另外生成 `transcript.txt`，不改动原下载文件。

`source_file` 可以是绝对路径，也可相对于 capture JSON 所在目录。另可用 `body_base64` 传递精确字节，或 `text` 传递 Unicode；三者只能选一个。`text` 会以 UTF-8 编码并注明是文本重建，不声称与 HTTP 原始字节完全相同。`expected_bytes` 如提供，必须与实际字节数相符。脚本支持 UTF-8 / UTF-8 BOM；遇到其他编码时保留原件并调整解析方法。

每种 `official_*` 都要求 `source.official: true`；所有种类都要求 `source.method`，写清真实获取方式。脚本不能从文字本身判定官方来源，不要用该字段把自写摘要或 ASR 冒充官方内容。

### 场次完成与多资产

取得第一段不代表整场完成。最后一个必需资产保存后，只有在网页上检查过该场所有片段/语言，才在该 capture 中设置 `coverage_confirmed: true`；可以重跑同一份文件的 ingest 来确认覆盖。脚本要求这个声明，并检查所有 expected_parts 已保存，才把该场设为 success。

如果添加新的资产，未同时重新确认覆盖时会清除原覆盖声明。没有 expected_parts 的场次也必须显式确认。字幕存在但无法取全时，用 blocked 标出；即使已有音频备份，也不能声明字幕覆盖完整。

## 官方 TXT 与面板 cue

官方直接下载的纯文本使用 `kind: "official_text"`，其余与 VTT 示例相同；保存为 `official.txt`，不人为补时间戳。

从完整官方面板导出的字幕使用 `official_dom_cues`，以 `cues` 替代 source_file / body_base64 / text：

```json
{
  "course_key": "EXAMPLE101",
  "recording_id": "session-2026-11-01-am",
  "asset_id": "part-2-en",
  "kind": "official_dom_cues",
  "coverage_confirmed": true,
  "cues": [
    {"start": "00:00:05", "speaker": "讲者", "text": "演示文本；实际使用时填入原始官方文字。"},
    {"start": 12.5, "speaker": "讲者", "text": "下一条原文。"}
  ],
  "source": {
    "official": true,
    "method": "完整官方字幕面板导出；已确认非局部虚拟滚动窗口",
    "page_url": "https://zoom.example.invalid/recording/example-session"
  }
}
```

保留 `official-cues.json` 和可读 `transcript.txt`。start 可为非负秒数或 MM:SS / HH:MM:SS，可带小数。只记录实际开始时间，不由下一条字幕推算结束时间。需要覆盖证据的是整个官方面板，不是示例中的两条 cue。

## 原始音频

确认没有官方字幕后，将 capture 改为 `kind: "original_audio"`，使用原始下载音频的 source_file，并在 source.method 说明已检查字幕入口及播放器。常见 M4A、WAV、MP3、Ogg、FLAC 容器可识别；未知格式应检查并适配，不盲目改扩展名。

可选 `--ffprobe auto`（默认从 PATH 查找）、`--ffprobe none` 或 `--ffprobe /实际程序路径`。缺少 ffprobe 时记录 `container_header_and_size_only` 和 `media_validation_unavailable: true`；这表示已保存并检查文件头/大小，音轨、时长、完整可播放性没有被验证。安装转录软件不是解决方式。

如果使用完整音频响应的 base64，必须提供 `expected_bytes`，并注明 source.http_status 为 200 或 206。206 还必须提供类似 `bytes 0-999/1000` 的 content_range，范围应正好覆盖全部解码后的字节。脚本拒绝不完整范围，不自动拼接猜测的音频片段；字幕字节来源如带范围，也检查其完整性。原生浏览器下载可没有 HTTP 元数据，完整性需由正常下载完成状态及本地验证共同支持。

`duration_seconds` 可填当前资产的播放器时长，便于比较。多片段或多语言不要把整场时长误当每个资产的时长，也不要将不同语言字幕的时长相加。

## 失败、恢复与校验

```sh
python3 scripts/recording_archive.py mark --root ./recordings-output --course-key EXAMPLE101 --recording-id session-2026-11-01-am --status blocked --reason "官方字幕存在，但当前工具尚无法取得完整正文"
```

`mark` 接受 pending / blocked / failed，不允许靠手动 mark 生成 success。保存了错误文件、HTML、空内容、部分字节范围或不匹配的长度时，ingest 拒绝，并保留已存在的归档。相同身份和相同内容可重复运行；同资产 ID 内容不同时报冲突，不覆盖。核对冲突来源后再决定保留哪些版本，不能删旧文件来掩盖冲突。

字幕过短、尾部早于视频结束不自动算失败；保留源文件、记录 cue 数量和时间范围，在网页端检查差值原因。TXT 没时间戳时，时长覆盖就是未知。

`finalize/verify` 都重新核对所有登记文件的字节数与 SHA-256，缺失或被修改的文件会阻止“全部完成”。ffprobe 成功只表示容器、音轨和时长通过检查，并不等于完整解码/人工试听过。

## 输出及将来合并接口

```text
recordings-output/
  README.md
  recordings.md
  recordings.csv
  recordings.json
  .recording-archive.json
  课程键-短哈希/
    日期/
      标题-稳定身份哈希/
        资产ID-短哈希/
          official.vtt 或 official.srt / official.txt / official-cues.json
          transcript.txt（有结构化字幕时）
          audio.m4a 等（该资产为原始音频时）
```

`.recording-archive.json` 用于续跑，活动任务中不要手动替换它。报告中的文件路径相对于归档根目录，移动整个目录后仍可关联。

JSON 保留 courses、records、各场 captures、来源、files 的 path/bytes/sha256、验证等级、时间范围、错误及状态计数。`complete_against_inventory` 只表示已登记项全部成功；`complete` 还要求 discovery.complete 为 true。这仍是以网页盘点真实无遗漏为前提，不是脚本自动发现了学校所有录播。

与其他课程材料模块合并时，以 `course_key + recording_id` 关联，将此录播根目录挂到课程总索引，保留该清单为录播数据源。不要把显示标题当唯一键，不要重命名后丢失路径映射，不要将 blocked/failed 从总数中删除。

脚本会去掉保存来源 URL 的 query 和 fragment，但自由文字字段无法保证自动去敏。输入 capture 的临时文件也可能仍含敏感数据；不要把密码、Cookie、令牌、签名媒体 URL 放进 source.method、备注、ID 或交付包。
