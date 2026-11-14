# 分享与维护

仓库：[razer-chan/lms-course-archive](https://github.com/razer-chan/lms-course-archive)

安装目录：[skills/lms-course-archive](https://github.com/razer-chan/lms-course-archive/tree/main/skills/lms-course-archive)

直接分享下面的话术即可。若复制到你自己的仓库，请保留目录结构并相应替换链接。

```text
这是一个把 LMS 课程资料和 Zoom 录播保存到本地的 skill。
在 Codex 中发送：

请用 skill-installer 安装这个 skill：
https://github.com/razer-chan/lms-course-archive/tree/main/skills/lms-course-archive

装好后给它你的课程链接，它会检查浏览器、需要时引导登录，
下载可访问资料，按课程分类，并给你完整清单。
录播优先官方字幕；无字幕保存原始音频，不转录。
```

接收者不需要先知道 LTI、CDP 或 API。安装只是载入流程和脚本；浏览器控制能力和课程登录仍由接收者自己的环境提供。README 和 skill 会引导处理这些前置条件。

仓库主页链接也能分享，但安装请求需补充 skill 路径 `skills/lms-course-archive`，否则安装器可能把仓库根目录误当 skill。不要把 `SKILL.md` 的 raw 单文件链接当成整个包，因为脚本和引用文档也需要一起安装。

更新时保留 skill 名称与目录，发布新的 commit/tag。已安装同名版本的用户应让 Codex 检查差异后更新；不要让安装器悄悄覆盖用户自改内容。需要固定版本时可以分享对应 tag 或 commit 的目录链接，具体格式由当前安装器处理。

每次发布新版本后，可以用实际目录链接安装到临时位置，核对文件与当前提交一致。新的使用者应先在自己的浏览器中试跑少量样本；具体环境和已验证范围见 [验证记录](VALIDATION.md)。

公开包只包含代码、说明和合成测试。正常使用时把课程下载放在仓库以外；`.gitignore` 只是减少误加入的机会，不能自动识别所有个人资料。
