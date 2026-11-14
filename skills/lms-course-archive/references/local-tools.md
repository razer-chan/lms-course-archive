# 本地工具与统一清单

本包的 Python 脚本使用 Python 3.10+ 标准库，无 pip 依赖。`browser_capture.js` 是可选浏览器辅助代码，无第三方 JavaScript 依赖。脚本不会替你安装浏览器控制器、登录学校或发现完整课程范围。

以下命令从 `skills/lms-course-archive/` 执行，其他位置需使用脚本实际路径。`/path/to/...` 是示例，应替换为实际工作目录；下载原件、清单及令牌都放在已安装 skill 和待公开仓库之外。

| 工具 | 作用与边界 |
|---|---|
| [recording_archive.py](../scripts/recording_archive.py) | 已取得的官方字幕/音频入库、字幕转可读 TXT、逐场核验与录播报告；不联网、不转码、不转录 |
| [build_course_index.py](../scripts/build_course_index.py) | 合并明确的材料 inventory、材料结果清单及录播报告，重新核验文件并生成全课总目录；不下载、不修改原件 |
| [capture_receiver.py](../scripts/capture_receiver.py) | 可选的临时 localhost 文件接收器；不访问 LMS、不读取 Cookie |
| [browser_capture.js](../scripts/browser_capture.js) | 仅在宿主允许页面 fetch 时请求已观察的页面 URL，再显式传给接收器；不提供浏览器连接能力 |

`ffprobe` 可选，只用于媒体容器、音轨和时长核验；没有时明确记录较弱的检查等级。`yt-dlp` 不是以上脚本的依赖，也不被它们调用；若宿主允许且当前来源适合，可由接手 AI 另选它处理合法媒体下载，但不能因此扩大范围或替代完整性核验。全流程不做新的 ASR、本地或云端转录。

## 录播模块

完整 inventory、capture 字段、各资产 kind、多片段覆盖及错误恢复见 [录播文件合同](recording-files.md)。最短调用如下：

```sh
python3 scripts/recording_archive.py init --root /path/to/archive/EXAMPLE101/08_Recordings --inventory /path/to/work/recording-inventory.json
python3 scripts/recording_archive.py ingest --root /path/to/archive/EXAMPLE101/08_Recordings --capture /path/to/work/capture.json
python3 scripts/recording_archive.py finalize --root /path/to/archive/EXAMPLE101/08_Recordings
python3 scripts/recording_archive.py verify --root /path/to/archive/EXAMPLE101/08_Recordings
```

一次 `ingest` 处理一项资产，修改同一个录播归档的命令串行执行。`ingest` 可加 `--ffprobe auto`（默认）、`--ffprobe none` 或实际程序路径。保留 VTT/SRT 原件，生成可读 TXT；官方 DOM cue 只保留真实时间戳；无官方字幕才存原始音频。字幕有而获取受阻不等于没有字幕。

所有已声明的必需 `expected_parts` 保存并明确声明 `coverage_confirmed: true` 后，该场才可为 `success`。`expected_parts` 可以省略，脚本不能因此知道还有哪些片段；网页逐片段检查仍由操作者完成。`mark` 只允许 `pending/blocked/failed`，不能手动标成成功。`ingest` 退出 0 只表示这次操作成功，不代表整场或整课已完成；`finalize/verify` 退出 2 表示仍有未完成项或网页盘点声明不足，退出 1 表示输入或操作被拒绝。

`finalize` 生成 `README.md`、`recordings.md`、`recordings.csv`、`recordings.json`，续跑状态保存在 `.recording-archive.json`。总索引读取 `recordings.json`；不能只登记录播文件夹路径而省略 finalize。

## 全课 inventory：先列预期项

下面是混合材料与录播的结构示例。材料、录播使用同一 `course_key`；稳定身份不能用标题或页码代替。

```json
{
  "courses": [{
    "key": "EXAMPLE101",
    "title": "示例课程",
    "source_url": "https://lms.example.invalid/course/example101"
  }],
  "discovery": {
    "complete": false,
    "notes": "范围：课程材料和录播；论坛未纳入本次任务。等待最后一次网页分页复核。"
  },
  "items": [{
    "course_key": "EXAMPLE101",
    "item_id": "content-123",
    "title": "第一周讲义",
    "section": "05_模块或周次",
    "kind": "file",
    "source_url": "https://lms.example.invalid/content/123"
  }],
  "recording_archives": [{
    "course_key": "EXAMPLE101",
    "path": "EXAMPLE101/08_Recordings"
  }]
}
```

`courses` 为非空数组，每门课的 `key/title/source_url` 必填，key 不重复。`items` 与 `recording_archives` 两个数组都必须显式提供；只做材料时后者为 `[]`，只做录播时前者为 `[]`，在 notes 说明范围。空数组不证明网站原本为空。

每个材料项的上述六个字段均为必填非空字符串；`kind` 可自定为 file、page、discussion、link 等。身份为 `course_key + item_id`。`discovery.complete` 只接受布尔值，省略则为 false；只有完成网页目录和分页核对后才设 true。它是操作者的声明，本地工具不能判断网站是否还有漏页。

保存范围内发现的全部预期项，而非只列下载成功项。范围外项目写范围说明，不伪装成 `not_available`。同一材料的 HTML、Markdown、图片或附件可以作为该项的多个 files；若分别登记为独立材料项，身份和计数定义需前后一致。

## 材料结果清单：真实文件与状态

`--materials` 接受 JSON 数组、`{"items": [...]}`，或扩展名为 `.jsonl` 的每行一个对象。输入使用 UTF-8，可带 BOM。下面的 bytes 和 SHA-256 必须替换为实际文件值；占位符不是可通过核验的数据。

```json
{
  "course_key": "EXAMPLE101",
  "item_id": "content-123",
  "status": "ok",
  "files": [{
    "path": "EXAMPLE101/05_模块或周次/第一周讲义.pdf",
    "bytes": 12345,
    "sha256": "填写真实文件的64位SHA256十六进制值"
  }],
  "note": "从已授权课程页面下载；保留原文件。"
}
```

| status | 使用含义 |
|---|---|
| `ok` | 已保存至少一个真实文件，且大小和哈希可核验；仅有 note 或来源 URL 不算归档成功 |
| `pending` | 尚未完成处理；inventory 中有、材料清单缺失的项自动保留为 pending |
| `failed` | 获取或本地文件核验失败；需记录实际原因 |
| `blocked` | 已知存在但当前能力或访问条件阻碍采集，例如官方正文只能取到一部分 |
| `needs_login` | 需要恢复正常登录后继续 |
| `needs_user` | 需要用户完成明确的操作或提供缺失信息 |
| `skipped` | 本次未取得；仍属未完成，不计为成功 |
| `not_available` | 来源明确不可用，必须有非空具体 note，例如页面明确说明文件已撤回；不计为下载成功 |

以上状态不会自动替操作者证明原因。403、404、空响应或请求上限不能直接作为“原本不可用”的证据。原页面明确为空且需要归档时，可以保存描述该观察的记录文件，而不是用无文件的 ok。

files 的 path 必须相对 `--root`，bytes 是非负整数，sha256 是 64 位十六进制。脚本重新读取文件核对大小和哈希；实际零字节、缺失、无法读取或不匹配时，有效状态改为 `failed`，同时保留 `declared_status` 和错误。非 ok 项已取得的 files 也会核验。清单存在未知身份、重复身份、非法元数据或路径时拒绝输入。课程与材料 source_url 仅接受无账号密码的 HTTP(S)，输出时去掉 query/fragment；必要身份另存稳定 item_id。自由备注和临时输入仍需操作者去敏。

文件路径不能是绝对路径、`..`、Windows drive 或反斜杠路径，不能通过符号链接指向根目录外。总索引的四个输出文件名为保留名称，不可同时用作原件或输入清单。总索引验证字节身份，不替代 PDF/PPT 格式检测、网页正文完整性、论坛计数或文件原始来源核实；这些在采集阶段完成并记录。

## 生成总目录与对账

```sh
python3 scripts/build_course_index.py --help
python3 scripts/build_course_index.py --root /path/to/archive --inventory /path/to/work/inventory.json --materials /path/to/work/materials.jsonl
```

`--root` 必须已存在。inventory/materials 输入路径按命令的当前工作目录解释；材料 files.path 和 recording_archives.path 按归档根解释。录播清单内部的 files.path 仍相对该录播模块，索引会转换为相对总根目录的链接。只做录播可省略 `--materials`；若 inventory 有材料却省略材料清单，这些项目保持 pending。

总索引重新核对每个录播模块的课程归属、场次身份、总数、success、coverage_confirmed、expected_parts、实际 captures/files 以及全部文件大小和哈希；还要求录播模块声明网页盘点完成。不能仅相信模块的 `complete: true`。缺 `recordings.json` 会保留 pending 模块并输出部分报告；结构或身份冲突则是输入错误。没有 ffprobe 的验证限制会保留到报告，索引本身不追加音视频检测。

归档根目录生成四个文件：

- `00_总目录_README.md`：按课程分类的材料和录播链接、范围、计数与验证界限。
- `下载清单.csv`：逐材料/逐场录播的声明状态、核验状态、文件路径和说明；UTF-8 BOM。
- `归档清单.json`：输入范围、文件大小/哈希、核验结果及状态计数。
- `未完成与缺失.md`：未完成项目、盘点缺口和来源不可用项。

输出 JSON 规范化本工具支持的字段，并非输入 inventory 的逐字段副本；额外自定义字段不会自动保留。需要的详细采集证据应另外保存并登记文件，不能依赖总索引代存任意元数据。

| 退出码 | 含义 |
|---|---|
| `0` | 网页盘点已声明完成；材料全部为核验通过的 ok 或有理由的 not_available；预期录播模块均完整 |
| `2` | 已写报告，但仍有未完成项、未完成录播模块或盘点声明不足 |
| `1` | 参数、输入或 I/O 错误；不可将以前的报告或部分输出当成本次成功结果 |

退出 0 表示声明范围内完成对账与处理，不等于所有资源都下载成功；`not_available` 独立计数。每次采集后串行重建总索引，最终交付前复核网页并重验文件。报告采用各文件临时写入后替换，不是四个文件一起提交的文件系统事务。

## 可选内容传递桥接

先读 [浏览器适配与用户引导](browser-and-onboarding.md)。当前只允许 DOM 的 CUA evaluate 不适用此客户端。只有宿主允许页面 fetch 与 localhost 通信时才使用；正常文件下载不依赖它。

```sh
python3 scripts/capture_receiver.py --root /path/to/archive --origin https://lms.example.invalid --token-file /path/to/private-runtime/capture-session.json
```

实际参数只有：必填 `--root`、可重复的 `--origin`、`--token-file`；可选 `--port`（默认 0，由系统分配空闲端口）和 `--max-bytes`（默认 268435456，即每次请求 256 MiB）。origin 必须精确到协议、主机和可选端口，无路径、尾斜线或通配符。没有固定学校域名、端口或课程 ID。

接收器只监听 `127.0.0.1`，启动信息显示 URL、根目录、PID 和令牌文件路径，不显示令牌值。它为本次进程生成随机令牌；新的令牌文件必须在归档根目录外，不可已存在或为 symlink，POSIX 权限为 0600。令牌在该进程的多次显式写入中使用，到进程退出为止，不是每个请求各换一个。Windows 下应使用私有运行目录的文件权限。私下将文件值读入允许的工具运行环境，不打印或提交该文件。

下面的变量由适配器从私有运行环境和当前页面观察结果提供，不是应粘贴进聊天的凭据。加载 [browser_capture.js](../scripts/browser_capture.js) 本身不发送请求。

```js
const cap = createLmsCaptureClient({
  authorizedPageFetch: true,
  receiver: privateSession.receiver,
  token: privateSession.token,
  observedUrls: [observedListUrl, observedDownloadUrl],
  credentials: "same-origin",
  fileCredentials: "same-origin"
});

const pages = await cap.all(observedListUrl, {maxPages: 100});
if (!pages.complete) throw new Error(pages.reason);
// 检查并去除传输凭据、签名下载参数等，再显式保存需要的内容。
const file = await cap.file(observedDownloadUrl);
const saved = await cap.save("EXAMPLE101/05_模块或周次/handout.pdf", file.blob);
// 记录 saved 的 path、bytes、sha256；另行核对文件类型及 inventory 身份。
await cap.close(); // 在全部下载和写入完成后执行。
```

`authorizedPageFetch: true` 是调用者确认已核对宿主许可，不会授予许可。客户端要求已观察的同源入口 URL；`observe(url)` 仅登记真实观察结果，不用于扫描猜测端点。请求凭据模式可选 `same-origin/include/omit`，根据当前流程验证，不假设一种模式适合所有站点。

| 方法 | 实际行为 |
|---|---|
| `getJSON(url)` | GET 已登记的同源 URL；拒绝重定向、206 和不可解析 JSON，返回 data 与去查询参数的 source |
| `all(url, options)` | 默认读取 `results` 与 `paging.nextPage`；需要时在确认当前结构后传 `items(data)`、`next(data)`、`expectedTotal`。默认最多 100 页，可设 1–1000；循环、上限、错误、结构变化或总数不符返回 complete:false |
| `file(url)` | 请求已登记的同源下载入口，可跟随正常文件服务重定向；CORS/认证限制仍适用。206 或带 Content-Range 的响应必须覆盖整个 Blob，片段会被拒绝 |
| `save(path, body)` / `saveJSON(path, value)` | 显式保存到接收器；不自动保存下载内容。JSON 需先按归档范围去敏，助手不会自动识别任意字段里的秘密 |
| `exists(path)` | 返回文件存在、大小和哈希，不返回内容；只存在不等于身份和内容匹配 |
| `close()` | 请求关停接收器并清除客户端令牌；之后不能继续保存 |

已确认只有一页的 JSON 可以明确使用 `next: () => null`，不要用它掩盖未知分页结构。`all.complete` 只说明指定分页规则耗尽，`file.complete` 只说明收到的响应范围完整，都不证明课程无遗漏或文件语义有效。

接收器常规请求要求精确 Origin、Host 和 `X-Capture-Token`。`POST /save` 使用编码后的相对路径 `X-Capture-Path` 和原始 body；同字节重复保存返回 `already_identical`，不同内容返回 409，无覆盖模式。`GET /exists?path=...` 仅返回校验元数据；`POST /shutdown` 使用空 body 关停。POST 需要有效 Content-Length；超限、部分 body、超时、目录穿越和 symlink 被拒绝。原子硬链接创建避免覆盖，文件系统不支持时返回失败。运行期间保持输出目录私有，避免其他进程同时改动其目录结构。

所有发往接收器的请求使用 `credentials: "omit"`，不将 LMS Cookie 发送给 localhost。CORS 和私有网络预检响应不代表浏览器必然允许通信，更不授权其他网站。禁止为绕过限制而扩大 origin 或关闭浏览器保护。

完成或失败后关停自己启动的接收器，可用 `await cap.close()`、Ctrl-C 或终止该进程。正常退出会等待活动写入结束并清理令牌文件；强制退出可能留下私有令牌文件和 `.capture-*` 临时文件，需私下清理，不复用旧令牌。来源摘要去掉 URL query/fragment，但正文、自由备注和临时 payload 仍需检查，不公开 Cookie、passcode、令牌或签名媒体 URL。
