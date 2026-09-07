# 自适应宿主循环：开发案例与验证

日期：2026-09-07。此增量落实 [ALIGN 对比](../ALIGN_COMPARISON.md)中的连续复评、
复盘、过程展示和本地接入。当前研究仍处于开发阶段，未增加独立验证靶点。

## 可直接查看的产物

- [离线回放页面](adaptive-loop-pdl1/index.html)：浏览器直接打开，无需运行服务。
- [逐轮数据](adaptive-loop-pdl1/replay.json)、[原始账本](adaptive-loop-pdl1/ledger.json)、
  [报告](adaptive-loop-pdl1/report.json)和[导出文件指纹](adaptive-loop-pdl1/manifest.json)。
- [事先确定的案例范围与停止规则](ADAPTIVE_LOOP_CASE_PLAN.json)、
  [机器可读摘要](ADAPTIVE_LOOP_SUMMARY.json)、[本机 doctor 结果](ADAPTIVE_LOOP_DOCTOR.json)。
- [运行与安装指南](../../interaction-design-mvp/docs/ADAPTIVE.md)。

页面为最终完成后的结果浏览器，嵌入完整的已付费轨迹；切换轮次显示当时的指标。
这不是防止查看未来数据的访问控制界面，不可用于未知测试的决策输入。
进行中的导出不包含实验终点；完成后也不导出尚未获取的预测。

## 实际完成的闭环

当前主对话的 Codex 宿主完成 3 次 `observe → apply → observe → reflect`，再提交最终
名单。每次查询前写出待检验判断与可见分数引用；新结果出现后再撰写复盘。
没有调用子 Agent、额外 LLM API、GPU 推理或新的实验。宿主的具体模型标识未从运行
元数据独立确认，案例中记为 `null`；模型调用开销未从本次编码对话中单独计量。

案例开始前固定：使用 PD-L1 的全部 90 个已有候选，追加查询 **3 次**后结束，按已见
非空 ipSAE 的中位数降序取前 10，同分按候选 ID。目标名在查看本轮分数前确定，
没有依据新揭示结果挑选有利子集。来源数据此前已公开暴露，宿主也处于既有开发
上下文，因此不是独立或盲态模型实验。案例范围文件的指纹记录在来源元数据中。

| 轮次 | 查询候选（ID 末尾） | 新 seed ipSAE | 排名变化 | 本轮复盘 | 累计查询 |
| --- | --- | ---: | --- | --- | ---: |
| 初始 | 所有 90 个候选的 seed 0 | — | — | — | 90 |
| 1 | `4270b9a62` | 0.910319 | 10 → 7 | 支持保持前 10 的预期 | 91 |
| 2 | `ff6eb7a9b` | 0.904108 | 10 → 10 | 支持保持前 10；scDockQ 分歧未解决 | 92 |
| 3 | `0c7eb0d1f` | 0.899308 | 11 → 12 | 反驳追加后进入前 10 的预期 | 93 |

最终选中 10 个，来源综合实验标签 `binder_final` 命中 **5/10**。入选集合与初始
前 10 相同，只发生部分次序变化。总逻辑查询数 **93/270**，未使用的 177 次配额保留。
这没有显示筛选改善；没有预算匹配的策略比较，也不能把未使用的配额解释为实测
GPU 节省。`supported/refuted` 是对本轮具体预期的宿主复盘，不是结合能力判定。

本机原始会话、代码快照和独立动作文件位于
`interaction-design-mvp/artifacts/adaptive-loop-20260907/`，不加入 Git。
仓库中的约 132 KB 回放包只包含公开投影、已接受动作、已揭示汇总和来源指纹。

## 数据来源及变换

复用此前导入的 Anthropic `claude-protein-binder-design` 数据，固定 revision
`9e1b81696da46835e9e9cde9a3da976e0abc92ab`，许可 CC BY 4.0。
原始来源、文件指纹和许可链接保留在[来源记录](adaptive-loop-pdl1/source.json)。
数据链接：[Anthropic 数据集固定版本](https://huggingface.co/datasets/Anthropic/claude-protein-binder-design/tree/9e1b81696da46835e9e9cde9a3da976e0abc92ab)。

已有导入将候选匿名化，使用 `ef2full`、`1to1`、空 target form 的五个 seed；
字段映射为 `iptm_pae → iptm`、`ipsae_min → ipsae`，保留 `sc_dockq` 和缺失值。
本次仅按预定 PD-L1 范围截取输入，并按已接受查询导出观测；没有下载或重新计算分数。

## 软件验证

- Python 全量测试 **817 passed**；Ruff 通过。新增检查覆盖计划与复盘证据、未来值
  拒绝、缺失值、预算、重复提交、原子恢复、导出不提前揭示、HTML 文本转义、CLI、
  安装冲突和无推理预检。
- DeepSeek 插件的两份 Node 测试文件通过；Codex 插件结构、共享技能和 Claude
  插件严格校验通过。未重新启动 Claude/DeepSeek 模型会话。
- 在 `/tmp/molclaw-loop-setup-g0_xvsov/` 中验证 Codex、Claude、DeepSeek 的技能链接
  与跨工作目录调用；重复安装返回 `already_installed`。仓库与用户主目录的宿主配置
  未安装或更改。本机已有 pipeline 任务与资源通过同一预检函数；GPU 未探测。
- 离线构建 wheel，确认包含自适应模块和 HTML；将 wheel 解压到新的临时目录，
  从 `/tmp` 导入并完成合成输入的 prepare/run/report/export，获得 12 次逻辑查询。
  构建复用已有缓存中的 Hatchling，无依赖或模型下载。
- Firefox 实测上一轮/下一轮/滑块、90 个候选的初始状态、逐轮预算与复盘、最终
  10 个入选标记和报告显示时机。实测视口宽度为 1440、768、450 像素，未发生整页
  横向溢出；窄屏表格在容器内滚动。所有原始记录链接存在，页面未加载网络资源。
  截图另存 `/tmp/molclaw-loop-browser-740x1srj/`。外层沙箱内 Firefox 启动受限，
  浏览器检查通过自动审批后在沙箱外以独立临时配置完成。

## 边界与后续

新会话使用 `molclaw-adaptive-development-v2`，冻结复盘校验代码；旧 v1 会话保留
原样，不迁移。计划/复盘直接挂在同一事件账本，结果下降也不能撤销计费或删改记录。
Doctor 的路径与资源通过不等于宿主已发现技能、模型会话可用或 GPU 推理已验证。

可选独立评审及其对照、可靠性模型、协议冻结和新独立数据验证仍按
[研究目标](../RESEARCH_GOAL.md)推进。本次交付没有证明这些研究完成条件。
