# 公开实验数据评测：第一轮结果

日期：2026-09-06。状态：已完成公开数据接入、评分分析、六次独立宿主决策、
固定/单分数/启发式对照和实验终点敏感性分析。

**Agent 在 60 个筛选名额中选中 27 个公开实验阳性；启发式和 ESMFold2 ipSAE
各选中 26 个。当前证据不支持 Agent 稳定优于强基线。** 这轮增加的是具有真实
实验标签的可复现评测能力，没有新增 MolClaw 生成蛋白的实验验证。

## 数据与比较对象

来源：[Anthropic 公共 binder 数据集](https://huggingface.co/datasets/Anthropic/claude-protein-binder-design/tree/9e1b81696da46835e9e9cde9a3da976e0abc92ab)，
固定 revision `9e1b81696da46835e9e9cde9a3da976e0abc92ab`。
说明与所需表格通过直连缓存，共 10,766,331 字节；没有下载结构大包、权重或完整提示词。
复用本地 Python/PyArrow 环境，没有安装依赖。

导入器从原始 1,440 行设计汇总及 113,550 行预测表中，仅投影七个指定靶点的
630 行候选与 9,450 行预测；没有读取或输出序列列。每个候选有三种预测器、
每种五个种子。原始两张表的 SHA-256、来源版本及 PyArrow 版本保存在报告中。

PD-L1 的 90 个候选用于选择单分数基线，其余六个靶点共 540 个候选用于评估。
每个靶点固定 90 个候选、每策略选 10 个；该名额是回顾性筛选数量，不是实际实验支出。
特征为 ESMFold2 full 的五种子中位 ipTM、ipSAE、scDockQ，以及三个预测器
ipSAE 中位数的中位数。所有策略使用同一份保留六位小数的输入。
45/630 个候选缺少 scDockQ，保留为未知；其他三项完整。

主终点直接保留来源的 `binder_final`：两家实验室数据、重拟合规则及传感曲线复核的
综合判定。它不是某一家实验室的单独检测结果。各家实验室的敏感性终点仅保留
`binder/non_binder`，未表达、未测和不确定均不擅自改写为阴性。来源解释见
[数据说明](https://huggingface.co/datasets/Anthropic/claude-protein-binder-design/blob/9e1b81696da46835e9e9cde9a3da976e0abc92ab/data/docs/DATA_NOTES.md)。

## 同池、同名额结果

下表每个策略单元格为所选 10 个候选中的主终点阳性数。固定顺序来自与分数和标签
无关的匿名 ID 顺序，只运行一次，不等于随机策略的充分重复。随机期望列为
`10 × 该池阳性数 / 90`。

| 靶点 | 池内阳性/90 | 随机期望 | 固定 | 开发集最佳单分数 | EF2 ipSAE | Ensemble ipSAE | 启发式 | Agent |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| BBF-14 | 3 | 0.33 | 1 | 0 | 0 | 0 | 0 | 0 |
| EGFR | 10 | 1.11 | 0 | 1 | 4 | 2 | 4 | 5 |
| IL-7Ra | 49 | 5.44 | 5 | 7 | 7 | 7 | 6 | 6 |
| MBP | 0 | 0.00 | 0 | 0 | 0 | 0 | 0 | 0 |
| TREM2 | 72 | 8.00 | 8 | 10 | 10 | 10 | 10 | 10 |
| TrkA | 20 | 2.22 | 0 | 6 | 5 | 5 | 6 | 6 |
| 合计，60 个名额 | 154/540 | 17.11 | 14 | 24 | 26 | 24 | 26 | 27 |

开发集最佳单分数是 EF2 ipTM；选择规则在揭示评估结果前固定。启发式为四项特征的
靶点内秩百分位等权平均，缺失记为最低；并不将相关特征视为独立证据。
Agent 各池使用自己的可解释分数组合，完整理由和精确证据保存在原始响应中。

Agent 与启发式的平均精确率差为 **+1.67 个百分点**，全部来自 EGFR 多选中一个。
六靶点配对描述性 bootstrap 区间为 `[0, +5]` 个百分点；与开发集最佳单分数比较为
`+5` 个百分点，区间 `[-5, +20]`。每靶点仅一次 Agent 实现，区间不包含宿主随机性，
也不能解释成显著性检验或稳健优势。

| 实验终点 | 固定 | 开发集最佳单分数 | EF2 ipSAE | 启发式 | Agent |
|---|---:|---:|---:|---:|---:|
| 来源综合判定 | 14/60 | 24/60 | 26/60 | 26/60 | 27/60 |
| Adaptyv 已知判定 | 13/58 | 24/59 | 26/58 | 26/59 | 27/59 |
| Twist 已知判定 | 14/60 | 17/60 | 20/60 | 21/60 | 21/60 |

分母只包括该终点已知的所选候选，所选名单始终不变。Twist 终点下 Agent 与启发式持平。
这些终点的检测体系与判定规则不同，不能把差异解释成某家实验室对错。

## 分数是否能预测实验结果

| 公开复合物分数 | 宏平均 AUROC | 宏平均 AP | 有定义的靶点数 |
|---|---:|---:|---:|
| EF2 ipTM | 0.682 | 0.469 | 5 |
| EF2 ipSAE | 0.713 | 0.503 | 5 |
| EF2 scDockQ | 0.524 | 0.414 | 5 |
| Ensemble ipSAE | 0.721 | 0.510 | 5 |

MBP 主终点没有阳性，AUROC/AP 均不定义，保留为空。这里评分分析排除该项分数缺失的
候选并报告覆盖率：scDockQ 在六靶点有 507/540 个已知值，其余均为 540/540；
因此不同列不是严格相同的完整观测队列。开发集选基线则在全池上将缺失分数并列排末尾。
报告保留逐靶点值和描述性靶点 bootstrap 区间。

分数的效用依赖靶点：例如 Ensemble ipSAE 的 AUROC 在 BBF-14 为 0.460、
TREM2 为 0.947。当前结果支持继续研究跨靶点可靠性与分数校准，不能将某个高分直接
等同于实验结合。scDockQ 衡量与设计姿态的一致性，本来就不是实验结构准确度。

## 宿主决策与可复现性

六个新 Codex 子 Agent 使用 `fork_turns=none`，每个只收到自己的匿名请求及同一份
决策指令。请求不含靶点名称、序列、原始 UUID/名称/排名、设计模型身份或实验标签。
所有提交在揭示任何评估结果前锁定；提前调用报告的真实检查被拒绝。
子 Agent 的实际任务身份为 `/root/external_host_01` 至 `06`，精确模型版本和 token
用量未知，保留为空；身份声明不是密码学认证。本轮没有 Claude/DeepSeek 模型会话。

输入隐藏是访问协议，不是文件系统隔离。公开特征仍可能被重新识别，也无法排除模型
训练数据污染。研究者已经读过来源文章的靶点级结果，因此本轮为探索性、回顾性、
靶点分离分析，不声称原始盲测或预注册。上游候选已被筛选，同源设计相关，六靶点
独立样本数有限；没有测出新 GPU 节省、反馈迭代改进或前瞻性 binder 成功率。

新增导入、统计及协议测试 165 项，完整 Python 测试 **511 项通过**；Ruff lint/format
通过。测试覆盖缺失标签与分数、种子与身份连接、并列 AP/AUROC、匿名输入不随隐藏标签
变化、提交额度、精确证据、提前揭示、幂等重交、回执路径/内容变更及协议规则漂移。

- [机器可读报告](../../interaction-design-mvp/artifacts/external-benchmark-development/session/report.json)
- [结果图 PDF](../../interaction-design-mvp/artifacts/external-benchmark-development/figures/external_benchmark_summary.pdf)
- [完整证据包 ZIP](../../interaction-design-mvp/artifacts/external-benchmark-development/packages/molclaw-external-benchmark-evidence.zip)
- [运行与复现说明](../../interaction-design-mvp/docs/EXTERNAL_BENCHMARK.md)
- [冻结协议](../../interaction-design-mvp/config/external-benchmark-v1.json)
- [宿主输入与响应](../../interaction-design-mvp/artifacts/external-benchmark-development/host)
- [skill 的评测入口](../../plugins/molclaw/skills/molclaw/references/external-benchmark.md)

数据归原作者，遵循 CC BY 4.0；本轮改动为指定靶点/列投影、种子聚合、匿名化和统计分析。
保留上游引用与许可证。相关动机来源：[Anthropic 公告](https://www.anthropic.com/research/Claude-accelerates-protein-design)、
[Adaptyv 独立验证记录](https://www.adaptyvbio.com/blog/anthropic-1)。
