# DeepSeek Harness 原生适配记录

日期：2026-09-06。用户指定官方 `deepseek-ai/deepseek-harness` 后，MolClaw 已增加原生 npm bundle 与 Cordis 工具入口。共享 skill、Codex 和 Claude Code 配置继续保留。

原生工具为 `molclaw_screen_prepare`、`molclaw_screen_observe`、`molclaw_screen_apply`，另提供 `molclaw_help`。插件通过宿主注册的 `bash` 工具调用 Python CLI，保留宿主权限检查、取消信号、父调用与根调用关联。完整 Submission 通过 stdin 传入，原执行器继续负责证据、候选身份、预算与回执校验。

源码研究固定在提交 `d347e703908d0406b7a7ef80e3a0e594d86b2215`，其版本为 `0.1.3-alpha.1`。测试针对实际发布的 `0.1.2-rc.1` 工具模块，Cordis 为 `4.0.2`、Schemastery 为 `3.18.2`；没有假定主分支和发布包完全相同。原生格式参见[官方 bundle 教程](https://github.com/deepseek-ai/deepseek-harness/blob/d347e703908d0406b7a7ef80e3a0e594d86b2215/docs/user/develop/basic/publish.md)。

实际集成使用官方 SystemPrompt、ToolRuntime、Bash 工具、本地 Bash 与 subprocess provider，直接通过 Cordis `ctx.plugin` 加载 MolClaw。没有 mock 这些执行模块，也没有启动模型提供者。确定性脚本从既有 PD-L1 16 候选中选择前两个 seeds 43/44，观察 2 个已保存的复合物结果后到达配额并完成报告。actor 标记为 `deepseek-harness-scripted-validation`；不是 DeepSeek 模型的选择。

10 项工具链检查全部通过：原生挂载、嵌套 Bash 的宿主拒绝策略、预取消不执行、真实输出 schema 校验、未选择结果不可见、过期请求拒绝、stdin 提交与幂等重交、配额停止、嵌套调用身份保留、卸载注销工具。

另外修复了 Python CLI 默认 SIGTERM 时的 worker 清理：主线程会终止其 worker 进程组，保存 signal、退出码和时间，再抛出 `SystemExit(143)`，让外层保存失败回执。自定义 handler、SIG_IGN 和其他线程的语义保持原样。合成 parent→worker→grandchild 检查确认进程停止与外层回执；SIGKILL 和主动离开该进程组的后代不在这一保证中。

| 验证 | 结果 |
| --- | --- |
| Python 完整测试 | 231 项通过 |
| 原生桥接单元测试 | 17 项通过 |
| 官方 defineTool / Schemastery 检查 | 4 项通过 |
| 官方执行模块与真实 Python CLI | 10 项通过 |
| Codex / Claude Code 插件及 skill 格式 | 全部通过 |
| npm 包搬移 | 解包到 /tmp 后由官方 Cordis 加载并调用 help 成功 |
| Python 分发包 | 离线构建；wheel 仓库外安装、入口与资源检查通过 |

没有新增 GPU 推理、DeepSeek 模型调用或模型权重下载。测试没有启动完整 DSH profile/Web UI，没有验证该应用的自动 skill 发现或操作系统文件隔离。Node `22.17.1` 完成此次模块级检查；完整 Harness 启动仍需符合所用发布版本的环境要求。本次记录不支持策略收益或生物学结合结论。

- [可随代码保存的完整结果](DEEPSEEK_HARNESS_ADAPTER_summary.json)
- [DeepSeek 接入说明](../../plugins/molclaw/skills/molclaw/references/deepseek-harness.md)
- [集成验证脚本](../../interaction-design-mvp/scripts/check_deepseek_plugin.mjs)
- [本机原始记录与分发包](../../interaction-design-mvp/artifacts/deepseek-adapter-development/)

下一步在选定宿主中配置实际模型与加载插件，验证完整会话；之后补齐自然语言任务到生成、筛选与结果导出的统一 skill 流程。原始 artifacts 不随 Git 分发，安装时需要已有 MolClaw CLI、模型环境与输入任务。
