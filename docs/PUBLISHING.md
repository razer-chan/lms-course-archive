# 分享与维护

仓库：[razer-chan/lms-course-archive](https://github.com/razer-chan/lms-course-archive)

安装目录：[skills/lms-course-archive](https://github.com/razer-chan/lms-course-archive/tree/main/skills/lms-course-archive)

直接分享下面的话术即可。若复制到你自己的仓库，请保留目录结构并相应替换链接。

```text
https://github.com/razer-chan/lms-course-archive/tree/main/skills/lms-course-archive
请安装并使用这个 skill，引导我登录并归档指定课程的资料和录播。
之后结合这些资料，只读检查我提供的作业，给出中肯估分和优先修改建议。
```

接收者不需要先知道 LTI、CDP 或 API。安装只是载入流程和脚本；浏览器控制能力和课程登录仍由接收者自己的环境提供。具体安装步骤在 skill 的 `references/installation.md`，浏览器配置与登录引导在 `references/browser-and-onboarding.md`。已有本地课程资料和稿件时可直接审阅，不需要浏览器。

仓库主页链接也能分享，但安装请求需补充 skill 路径 `skills/lms-course-archive`，否则安装器可能把仓库根目录误当 skill。不要把 `SKILL.md` 的 raw 单文件链接当成整个包，因为脚本和引用文档也需要一起安装。

更新时保留 skill 名称与目录，发布新的 commit/tag。已安装同名版本的用户应让 Codex 检查差异后更新；不要让安装器悄悄覆盖用户自改内容。需要固定版本时可以分享对应 tag 或 commit 的目录链接，具体格式由当前安装器处理。

每次发布新版本后，可以用实际目录链接安装到临时位置，核对文件与当前提交一致。新的使用者应先在自己的浏览器中试跑少量样本；具体环境和已验证范围见 [验证记录](VALIDATION.md)。

公开包只包含代码、说明和合成测试。正常使用时把课程下载放在仓库以外；`.gitignore` 只是减少误加入的机会，不能自动识别所有个人资料。

发布审阅能力时，保留网页只读、禁止上传/代交、用户原文件不变和无转录边界。仅另存报告，不生成改写稿；正常阅读附带的已读状态允许。分数是带证据条件的估计，不是教师成绩。更新公开包前检查私人课程资料、稿件与报告未进入 Git。
