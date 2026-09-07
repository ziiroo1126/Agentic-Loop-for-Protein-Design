# ALIGN 与本项目：对比及可借鉴做法

日期：2026-09-07。本文保留比较与建议；下述顺序 1–3 已在后续获准实施，见
[实现与验证记录](evidence/ADAPTIVE_LOOP_DEVELOPMENT.md)。可选独立评审尚未实现，
现有研究协议的科学完成条件不因这些工程增量而改变。
ALPD（Agentic Loop for Protein Design）目前是讨论中的定位；代码仍使用 MolClaw 名称。

比较范围：ALIGN 的公开 README、Codex 技能、安装脚本及武汉案例评审日志；本地的
共享技能、pipeline/screen/campaign/adaptive 执行逻辑、结果导出与研究记录。
未安装或运行 ALIGN，也未据其展示页面独立测量图像质量。

## 判断

最值得借鉴的是把反馈组织成可检查的下一步行动，并让使用者看懂多轮过程。
本项目已经有真实计算、动作校验、成本记录和公开数据评测；这些能力应继续作为基础。
当前更具体的缺口是跨轮判断的复盘、连续工具决策的宿主体验和结果展示。

ALIGN 让编程 Agent 写绘图程序，通过独立视觉反馈继续修改，并交付可回放案例。
本项目的完整 pipeline 目前先生成固定候选池，再由宿主选择复合物评估对象；新 adaptive
原型逐次揭示已有预测，用于开发阶段的评估分配研究。两者的可执行动作范围不同。
来源：[ALIGN README](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN)、
[本项目 pipeline](../interaction-design-mvp/docs/PIPELINE.md)、
[自适应执行器](../interaction-design-mvp/src/interaction_design/adaptive.py)。

## 逐项比较

| 维度 | ALIGN 的做法 | 本项目当前情况 | 建议 |
| --- | --- | --- | --- |
| 用户入口 | 按绘画任务提供宿主技能 | 共享技能已覆盖任务编写、执行、选择和导出 | 保留共享核心，按任务组织使用入口 |
| 反馈到动作 | 审阅具体问题，修改后继续检查 | `screen` 支持评估或停止；`campaign` 是固定约束下的非 LLM 采样/停止；`adaptive` 支持逐次复评 | 先把复评动作接入完整宿主循环 |
| 跨轮记录 | wiki 保存重要判断、反驳和回退 | 已保存请求、提交、回执、结果；决策含理由与证据 | 增加简短的待检验判断及结果复盘，并链接已有记录 |
| 独立评审 | 新上下文看成图；主线采用不同模型家族 | 已有独立宿主选择案例，但没有固定的提议者—评审者协议 | 作为可选策略实验，不作为默认依赖 |
| 过程展示 | 版本、固定视图、工序与评审记录连在一起 | 导出结构、FASTA、CSV/JSON、Markdown 和决策轨迹 | 从现有导出包生成只读过程浏览页 |
| 安装 | 小脚本将完整技能目录链接到宿主目录 | 插件和启动脚本已有，接入步骤分散在说明中 | 增加本地安装入口和环境检查，复用已有依赖 |
| 验证 | 主要是少量作品的迭代案例 | 已有固定/启发式对照和公开标签评测，新增重复没有稳定优势 | 保留独立靶点、预算一致和预定终点的验证方式 |

ALIGN 的入口及案例见
[README](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN)；反馈与复盘规则见
[Codex 技能](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN/blob/main/skills_codex/reference-art-loop-codex/SKILL.md)；
安装实现见 [install.sh](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN/blob/main/tools/install.sh)。
本项目对应实现见 [共享技能](../plugins/molclaw/skills/molclaw/SKILL.md)、
[候选选择](../interaction-design-mvp/src/interaction_design/selection.py)、
[导出器](../interaction-design-mvp/src/interaction_design/exporting.py) 和
[重复实验](evidence/BENCHMARK_REPETITIONS.md)。

## 最值得实施的增量

### 1. 把复评变成一个会检查先前判断的循环

建议在现有受约束动作周围组织以下步骤：

```text
读取当前可见观察
    → 列出影响当前选择的主要疑点
    → 说明哪一次允许的评估能提供相关信息
    → 执行动作，记录成本
    → 对照新观察，复盘先前判断
    → 更新下一步选择或提交最终名单
```

例如，在开发回放中，某候选接近入选边界且已见预测存在分歧，策略可以说明为何为其
追加一次预测；新结果揭示后，记录排名及不确定性的变化。这个记录描述要检验的判断，
不保证追加预测必然改善结果。

可以先增加一个简短的决策摘要：可见证据引用、当前问题、所选动作、预期能澄清什么、
实际观察，以及判断被支持、被反驳还是仍不确定。只记录可核对的决策摘要，无需保存
冗长的模型思考文本。摘要引用执行器的真实字段，模型解释与原始测量分开保存。

现有 `SelectionDecision` 已有 `reason` 和 `evidence`，无需另建一套平行日志；先在新
开发会话中补充复盘产物，再决定是否升级提交协议。已有冻结记录保持原样。
来源：[选择协议](../interaction-design-mvp/src/interaction_design/selection.py)、
[决策轨迹导出](../interaction-design-mvp/src/interaction_design/exporting.py)。

当前研究目标固定候选池及评估模型。重新生成、改变长度或设计约束属于后续单独版本，
不混入当前复评实验。来源：[研究目标](RESEARCH_GOAL.md)。

### 2. 将过程证据变成用户可以浏览的结果

建议为已有导出包增加只读页面，显示任务约束、候选及其评估状态、逐轮决策、当时
可见的信息、新揭示的结果和累计成本。原始结构、指标和决策记录从页面直接定位。

先用已有开发案例做一个完整回放，再做固定策略与宿主策略的并排比较。优先复用已保存
结果，不重新运行模型。页面同时显示改善、退步、未知和未评估状态；在最终完成后的
结果浏览器中可以展示完整结果，供决策使用的回放界面则必须遵守当轮的信息边界。

这属于结果展示增量，可以作为静态导出交付，无需把独立 Web 服务变成项目的前置条件。
本项目已经导出 `decision_trace.json`、`metrics.csv`、候选结构及 Markdown 报告，
可以在这些产物上实现。来源：[导出器](../interaction-design-mvp/src/interaction_design/exporting.py)。

### 3. 将接入步骤收敛成一个本地入口

ALIGN 的安装脚本链接完整技能目录，并在遇到已有不同目标时停止覆盖。可借鉴这种
小而明确的接入方式，为本项目提供安装入口及只读环境检查。
来源：[安装脚本](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN/blob/main/tools/install.sh)。

检查范围应明确区分：Python CLI 是否可调用、宿主是否发现技能、该宿主的模型会话
是否验证过，以及 GPU 模型资源是否就绪。资源不齐时仍允许使用已有结果回放。
接入操作不隐式下载大模型，遵守本服务器优先缓存、优先直连的规则。

保留本项目现有的共享技能和统一 Python CLI；不同宿主只处理发现、工具传输和取消
等差异。不要为了模仿目录外观而复制多份科学执行逻辑。

### 4. 将独立评审作为可测量的可选机制

可以让提议者给出动作及证据，再由评审者检查该动作是否回答当前疑点、是否忽视
矛盾信息或重复已有评估。两者接收相同的已揭示观察；评审者不能获得未来预测和标签，
也不能绕开执行器。评审意见只是建议，科学指标仍由工具计算。

开发阶段先比较单决策者、同家族独立评审和跨家族评审。预先明确科学计算配额，另行
记录模型调用成本和延迟；若对照没有匹配总调用预算，应明确评审效果与额外计算混杂。
是否触发评审也属于策略，需在新的独立评测前冻结。

ALIGN 的武汉评审日志保留了分数轨迹、剩余问题及未通过的结束状态；README 同时说明
不同运行的条件并不完全匹配，计划中的部分对照尚未完成。它提供了机制的可检查案例，
尚不足以证明跨家族评审在本项目中必然更好。
来源：[原始评审日志](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN/blob/main/examples/qingming-wuhan/wiki/review-log.md)、
[案例范围](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN#three-worked-examples)。

## 需要按科学任务重新设计的部分

- **回退的含义。** 可以停止沿用一个判断或策略；已经获得的有效预测和消耗的计算
  仍应保留并计费，不能因结果不好将其撤销，以免形成选择性报告。
- **判断标准。** 图像审阅中的视觉判断可用于创作反馈；结构置信度和 LLM 意见不能
  替代实验结合判定。独立评测的主要指标、纳入规则和停止规则应提前确定。
- **跨任务记忆。** 单次任务内可以积累已观察的问题；测试结果揭示后总结出的经验
  不能再进入同一轮未知测试。跨靶点规则需要开发/测试分离。
- **工程简化。** ALIGN 技能中的艺术创作约束包括避免哈希和防御性框架。我们应按
  实际故障成本审视检查，保留候选身份、输入版本、恢复幂等、预算和未来信息校验。
  精确引用一个分数可以防止编造数字，但不能单独证明该推理或选择有效。

这些是本项目任务差异带来的取舍。依据包括
[ALIGN 技能](https://github.com/wanshuiyin/ALIGN-Agentic-Loop-Image-GeneratioN/blob/main/skills_codex/reference-art-loop-codex/SKILL.md)、
[本项目筛选协议](../interaction-design-mvp/docs/SCREENING.md) 和 [研究目标](RESEARCH_GOAL.md)。

## 建议顺序与验收

| 顺序 | 工作 | 可检查的完成条件 |
| --- | --- | --- |
| 1 | 让自适应复评进入统一 CLI 和共享技能，补决策复盘 | 宿主按当前观察完成连续动作；每次查询记账；最终选择可从记录重建 |
| 2 | 已有结果的只读回放和一个完整案例 | 无新增推理即可浏览逐轮信息、决策与成本，原始文件链接可用 |
| 3 | 本地安装入口及环境检查 | 在新的项目目录发现技能、调用已有 CLI，并区分资源缺失与宿主未配置 |
| 4 | 可选独立评审及对照 | 用相同信息边界比较，报告筛选效果、额外调用开销和失败案例 |

新可靠性模型、协议冻结和新的独立验证仍按 [RESEARCH_GOAL.md](RESEARCH_GOAL.md) 推进。
界面、安装便利性和评审话术的改进不能代替这些研究完成条件。
