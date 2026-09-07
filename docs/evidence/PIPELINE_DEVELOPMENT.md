# 完整 binder 流程开发验证

2026-09-06，新任务已通过统一入口完成：任务与本地资源预检 → ODesign/OInvFold
生成 → ESMFold v1 单体检查 → 宿主候选选择 → ESMFold2 复合物与界面反馈 → 停止 →
可搬移结果包。新增 `pipeline prepare/run/observe/apply/export` 五个命令和对应
DeepSeek 原生工具，共享 skill 覆盖任务编写至导出。

## 本次真实执行

使用 PD-L1 开发目标、固定 60 aa binder、两个新种子 61/62。所有模型与数据来自
本地缓存；GPU 3 为 NVIDIA L20。完整任务在预检后冻结目标结构、任务、运行配置、
选择配置和实现源码。没有下载、代理传输或全局宿主配置更改。

| 阶段 | 实际工作 | 工具墙钟时间 |
| --- | --- | ---: |
| 生成 | ODesign/OInvFold，2 个新候选 | 107.88 s |
| 单体检查 | ESMFold v1，2 个候选 | 27.98 s |
| 准备选择 | 构建候选目录与冻结筛选会话 | 0.19 s |
| 复合物与反馈 | ESMFold2，seed 62，界面几何分析 | 61.45 s |

这些是执行器记录的阶段时间；不包含宿主思考、工具派发、预检与导出开销，也不是
整个交互的端到端耗时。生成、单体与复合物阶段分别只启动了 1 个模型进程。

新建且不继承上下文的 Codex collaboration 子 Agent 只收到共享 skill 快照和可见
request，任务为选择一个候选做开发验证。它选择 seed 62：单体与设计的 RMSD 为
4.446605 Å，而 seed 61 为 24.034691 Å；同时明确承认其置信度更低且存在一处生成
结构的链间残基碰撞。其完整数值证据与原始提交保存在结果的决策轨迹中。

本次宿主模型负责候选选择；达到 1 个评估配额后由执行器自动停止。没有测试自主
修改目标、重新设计、主动调参或基于新反馈追加生成。

| seed | 单体 pLDDT | 单体/设计 RMSD（Å） | 复合物评估 | ipTM |
| --- | ---: | ---: | --- | ---: |
| 61 | 79.130783 | 24.034691 | 未评估 | — |
| 62 | 57.149067 | 4.446605 | 已完成 | 0.183789 |

seed 62 的预测热点覆盖为 0.25，链间残基碰撞计数为 1；对齐 target 后的 binder
RMSD 为 16.991236 Å。当前诊断不能支持有效 binder 结论。所有接受标签仍为 null，
`binding_validated` 为 false；不以演示完成替代生物学或策略效果验证。

## 宿主与执行边界

5 种原生操作实际使用发布版 DSH `0.1.2-rc.1` 的 Cordis、ToolRuntime、官方本地
Bash/subprocess 服务加载 MolClaw 插件，再调用 Python CLI。包含恢复与重复提交的
8 次调用均完成，嵌套工具保留父调用身份。候选决策来自真实 Codex 子 Agent；没有
调用 DeepSeek 模型、启动完整 DSH profile/Web UI 或测试 Codex/Claude 全局安装。
宿主模型版本与 token 用量未知，记录为 null。

再次 `pipeline run` 复用全部已完成阶段；重复 `pipeline apply` 返回
`already_applied`。两次检查均未新增模型进程。失败或状态不明的步骤保留回执，
不会自动重跑；源码或输入不匹配时拒绝恢复。

## 交付和验证

- 完整 Python 测试 346 项通过，Ruff 检查与 56 文件格式检查通过。
- 原生桥接 25 项、官方 SDK 5 项检查通过；Codex 插件、共享 skill 和 Claude 严格
  manifest/skill 校验通过。
- 结果包包含 34 个受校验文件及 manifest：所有候选序列、生成结构、单体结构、
  已评估复合物结构/置信度数组、指标、任务、协议、决策轨迹与来源哈希。复制后的
  JSON/Markdown/CSV/FASTA 不包含服务器运行路径；未评估候选没有伪造复合物结果。
- 离线构建 Python wheel/sdist、DSH npm bundle 及三宿主插件 ZIP。wheel 在 checkout
  以外的临时目录加载了新命令与打包协议。构建跳过排除目录，避免遍历模型缓存。

[完整使用说明](../../interaction-design-mvp/docs/PIPELINE.md) ·
[实际报告](../../interaction-design-mvp/artifacts/pipeline-development/result-bundle-reviewed/report.md) ·
[结果 ZIP](../../interaction-design-mvp/artifacts/pipeline-development/packages/pdl1-pipeline-results.zip) ·
[机器可读摘要](PIPELINE_DEVELOPMENT_summary.json)

运行目录：`interaction-design-mvp/artifacts/pipelines/20260906T024755Z-5a814efd`。
开发证据与交付包：`interaction-design-mvp/artifacts/pipeline-development`。
复核后将导出完成清单改为最后发布，补充测试并再次导出；两版导出及执行时源码均保留。
原始和导出记录由该目录的 `evidence-manifest.json` 固定；历史证据保留原样。

此阶段完成工程流程演示。面向 PhD 的独立目标、受控策略对比、消融和生物学验证
仍是后续研究工作，不能据此声称 Agent 提高了命中率。
