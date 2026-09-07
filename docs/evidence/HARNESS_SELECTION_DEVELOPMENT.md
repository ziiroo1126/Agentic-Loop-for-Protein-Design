# 宿主 Agent 候选选择开发记录

日期：2026-09-06。已用真实 Codex collaboration 子 Agent 完成两轮候选选择。结构评估使用既有 PD-L1 16 候选结果回放，本阶段没有新增 GPU 推理。

宿主读取 `screen observe` 的可见状态，按 skill 提交 JSON；执行器检查请求校验值、候选范围、数值证据和配额，再揭示选中结果。生成、ESMFold v1 单体与 ESMFold2 复合物的原始运行沿用此前封存记录。

| 策略 | 两轮选择的种子顺序 | 观察数 | 停止原因 |
| --- | --- | ---: | --- |
| fixed | 43, 44 → 45, 46 | 4 | candidate_limit_reached |
| heuristic | 46, 48 → 49, 45 | 4 | candidate_limit_reached |
| harness | 49, 48 → 43, 44 | 4 | candidate_limit_reached |

第一轮子 Agent 优先选择单体回折与设计结构一致性较好的 49、48；观察复合物后继续选择 43、44。第二轮响应引用了已揭示的 ipTM 和目标对齐后 binder RMSD，也引用未评估候选的可见前置指标。该轨迹证明决策接口可以读取反馈，不能证明反馈改变了选择或带来了收益。

| 宿主选择顺序 | ipTM | 热点覆盖 | 碰撞残基对 | 目标对齐后 binder RMSD (Å) |
| --- | ---: | ---: | ---: | ---: |
| 49 | 0.171267 | 0.25 | 2 | 17.634808 |
| 48 | 0.240238 | 0.25 | 2 | 7.193282 |
| 43 | 0.529753 | 0.50 | 1 | 14.658002 |
| 44 | 0.137052 | 0.50 | 1 | 19.471889 |

信息边界：决策子 Agent 以 `fork_turns: none` 启动，仅被授权读取 skill、协议说明及两次可见请求，并写响应文件；没有执行科学工具。父 Agent 曾读取开发集完整结果，因此这次记录属于开发链路验证。访问限制来自任务指令，未构造操作系统文件隔离。

来源记录为 `codex-collaboration` / `/root/host_decision`。运行接口未披露精确模型修订、系统提示全文及 token 用量，相应字段留空，不能据此构成可重复的模型性能基准。两轮原始响应与应用记录一致，重复提交返回 `already_applied`，未增加工具调用。

回放工具耗时只表示本地提取/校验结果，不包括模型决策、历史生成/回折成本或 GPU 占用；不能解释为推理节省。所有候选 acceptance 为空，binding_validated 为 false。没有校准通过阈值、独立目标对照、消融或实验结合验证。

工程验证：完整 Python 测试 220 项通过，Ruff 检查与格式检查通过；离线构建 wheel/sdist，将 wheel 安装到临时目录后在仓库外验证入口与模型/几何协议资源。Codex、Claude Code 插件格式及搬移后的启动脚本通过检查。尚未在 Codex 应用内安装插件，未运行 Claude Code 或 DeepSeek 模型会话。

- [可随代码保存的完整摘要](HARNESS_SELECTION_DEVELOPMENT_summary.json)
- [宿主 skill / 插件包](../../plugins/molclaw/README.md)
- [screen 协议与命令](../../interaction-design-mvp/docs/SCREENING.md)
- [本机原始输入、响应、来源与验证目录](../../interaction-design-mvp/artifacts/harness-selection-development/)
- [已有真实采样闭环](CONTROLLER_DEVELOPMENT.md)

原始 artifacts 被 Git 忽略，本机链接供当前工作区复核；新克隆可读取摘要与代码，需要准备自己的模型环境和已完成输入后执行。下一阶段补齐自然语言任务到生成/筛选/导出的完整 skill 路径，并在选定宿主中验证加载，再开展独立目标上的策略比较。
