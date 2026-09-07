# PD-L1：16 候选生成与 ESMFold 回折基线

2026-09-05。已完成 seeds 43–58 的全部 16 个真实候选生成和单体回折，序列互不相同。
这是单靶标开发实验，尚未检验结合能力、跨靶标泛化或 Agent 决策收益。

seed 49 的 Cα pLDDT 为 **81.99**、相对设计的 Cα RMSD 为 **0.948 Å**；seed 48
分别为 **82.28** 和 **1.553 Å**。最高置信度的 seed 46 则为 **89.08** 和 **9.418 Å**。
这说明“模型对预测结构有信心”与“预测结构接近设计骨架”在本批次中并不一致。
低 RMSD 表示相对于计算设计的自洽性，不是相对于实验结构的准确率。

## 实验前固定的配置

- [任务](../../interaction-design-mvp/examples/baseline_pdl1_16.json)：PD-L1 原参考链 B20–133，热点 B64/B68/B126/B128；生成 60 残基 binder。
- [协议](../../interaction-design-mvp/config/baseline-pdl1-16.protocol.json)：seeds 43–58，每个种子一个骨架、一个 OInvFold 序列，温度 1.0、beam 开启；排除此前调试用 seed 42。
- ODesign 源码 `43944e930aea7dd74d2762f6cce7855471edae7b`，运行时上游工作区干净。
  ODesign 权重版本 `ab808f9e947fc065129e2ce2bc3fbcb95ef6b496`，OInvFold 版本 `e77a2b0200570757018751f33f06e32e2387413b`。
- ESMFold 快照 `75a3841ee059df2bf4d56688166c8fb459ddd97a`，float32，TF32 关闭，chunk 128，4 次 folding trunk pass。
  单模型加载、逐序列执行、batch size 1，CPU 线程数 4；仅输入 binder 序列。
- 全部候选进入报告，不在看到结果后设置通过阈值，所有 `threshold_pass` 均为空。
- 代表候选按 Cα pLDDT 降序、同分按候选 ID，取零基位置 `[0, 7, 15]`。
  这一规则在生成前保存；它用于覆盖高、中、低置信度，不表示选出最佳 binder。

冻结时间为 `2026-09-05T13:14:13.287860+00:00`；首次生成开始于 13:14:53 UTC。
协议文件 SHA-256：`7afba0b21444f46fc22ca3d460acc54fda2a3c23acc5321eec16e3cda8f51600`。
原任务文件 SHA-256：`28da13bca3889799ab32bb139f9ee2db9c7bcbcdb31a8b245213a580352560c3`。
运行 manifest 中的任务哈希使用解析路径、补齐默认值后的规范 JSON，和原文件字节哈希含义不同。
[冻结记录](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/freeze.json)与两份任务内容已核对，重试没有改变科学配置。

## 全部结果

pLDDT 取每个残基的 Cα 平均值，范围 0–100。RMSD 使用全部 60 个匹配残基的 Cα，
做一次刚体叠合，不裁剪末端、不排除离群点；已用 Biotite 独立复算。
推理时间为 CUDA 同步后的单次 forward 墙钟时间，不含模型加载和文件处理。

| Seed | Cα pLDDT | pTM | 相对设计 Cα RMSD（Å） | 推理（秒） |
| --- | ---: | ---: | ---: | ---: |
| 43 | 65.36 | 0.378 | 2.033 | 0.979 |
| 44 | 70.59 | 0.438 | 3.091 | 0.922 |
| 45 | 77.30 | 0.587 | 6.383 | 0.947 |
| 46 | 89.08 | 0.707 | 9.418 | 0.956 |
| 47 | 74.77 | 0.438 | 13.249 | 0.925 |
| 48 | 82.28 | 0.706 | 1.553 | 0.916 |
| 49 | 81.99 | 0.678 | 0.948 | 0.774 |
| 50 | 50.05 | 0.290 | 10.536 | 0.776 |
| 51 | 68.65 | 0.508 | 3.450 | 0.772 |
| 52 | 55.75 | 0.335 | 10.269 | 0.775 |
| 53 | 53.34 | 0.266 | 9.803 | 0.773 |
| 54 | 68.66 | 0.409 | 4.285 | 0.771 |
| 55 | 78.73 | 0.440 | 17.716 | 0.777 |
| 56 | 64.60 | 0.390 | 3.171 | 0.765 |
| 57 | 61.32 | 0.302 | 10.497 | 0.768 |
| 58 | 50.92 | 0.280 | 6.281 | 0.769 |

Cα pLDDT 中位数 **68.65**，RMSD 中位数 **6.332 Å**。所有 16 条序列唯一；
120 对等长序列的同位置一致性为 1.67%–31.67%，中位数 11.67%。生成骨架之间的成对
Cα RMSD 为 2.680–13.305 Å，中位数 10.444 Å。这些是描述性多样性指标，尚未进行
结构聚类，也不能将其解释为不同的结合模式。

## 结果如何解释

seed 48/49 在本批次中同时表现出较高单体置信度和较小设计偏差，值得后续探索。
这是看到结果后的观察，不应称为预先定义的筛选成功或成功率。旧 seed 42 的单次低置信度
结果也不足以断言这套生成流程普遍无效。

seed 46 的置信度最高，但回折结构偏离原设计；seed 55 的 pLDDT 为 78.73，RMSD
达 17.716 Å。因此仅按 pLDDT 排序不能保证设计骨架自洽。一个可检验的后续问题是：
结合置信度、设计偏差和多样性分配复合物评估预算，能否优于固定排序。
当前数据只能提出这个问题，还没有证明该策略有效，也没有验证 LLM Agent 的必要性。

为检查较大 RMSD 是否来自明显的骨架断裂，事后计算了原设计相邻 Cα 距离，所有值在
3.741–3.843 Å；未发现明显 Cα 连续性异常。该检查没有参与筛选，不能替代全原子几何、
能量或界面评估，详见[诊断记录](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/geometry_diagnostic.json)。

## 资源与失败成本

运行使用共享服务器 GPU 2（单张 NVIDIA L20），ESMFold 使用现有 Python 3.10 / Torch
2.7.1+cu118 / Transformers 4.52.4 环境。PyTorch 每个候选的峰值分配约 13.33 GiB，
包含驻留模型和推理张量，不包含 CUDA context 和其他进程。

| 阶段 | 外部进程墙钟时间（秒） |
| --- | ---: |
| 首次生成：环境故障、零候选 | 286.27 |
| 原配置重试：16 个生成完成 | 355.70 |
| ESMFold 整批：16 个回折完成 | 39.86 |
| 合计（包含失败） | 681.83 |

合计约 **11.36 分钟**，不含预检哈希、诊断、报告和阶段间空闲时间。ESMFold 模型加载及
GPU 转移共 10.18 秒，16 次 forward 合计
13.36 秒；它们是整批进程时间的组成部分，不能再次相加。
共享设备上的墙钟耗时不是独占 GPU 吞吐或 GPU 利用时间。

首次生成因较长 `TMPDIR` 导致 `torch_shm_manager: Invalid argument`，数据加载进程停滞。
用单元素 CPU 共享张量复现后，仅终止本实验的进程组，将临时目录改为短路径后成功。
286.27 秒失败成本从 3600 秒生成预算中扣除；累计生成 641.97 秒，ESMFold 使用 900 秒
上限中的 39.86 秒。[恢复记录](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/runtime_recovery.json)及失败日志保留。
本轮没有下载模型、安装依赖或修改共享环境及代理设置；打包也复用本地缓存。

## 后续复合物评估

冻结规则选出 **seeds 46 / 54 / 50**。其[AF3 输入与选择记录](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/generation/20260905T132100Z-31f93b7a/monomer_batches/20260905T132814Z-b9fd64c4/complex_followup/selection.json)
已准备：目标链待搜索 MSA，binder 使用 query-only，不把生成几何作为模板。
**AF3 与 PyRosetta 尚未执行**，AF3 运行/采样协议仍需在真实部署前配置。
没有为了改善结果展示，将固定代表候选替换成 seeds 48/49。
后续可额外评估 48/49，但应单独标记为探索性追加候选。

这轮工程成果可以表述为“实现可追溯的蛋白 binder 生成与离线单体评估流程，完成
16 候选 PD-L1 基线并分析置信度与设计自洽性的差异”。目前不宜表述为已发现有效 binder、
提高结合成功率或实现经过验证的自主设计 Agent。

## 产物与复现入口

- [随代码保存的全精度指标、序列、版本与校验和](PDL1_16_baseline_summary.json)。
- [本机完整报告](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/generation/20260905T132100Z-31f93b7a/monomer_batches/20260905T132814Z-b9fd64c4/report.md)、[CSV](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/generation/20260905T132100Z-31f93b7a/monomer_batches/20260905T132814Z-b9fd64c4/candidates.csv)、[FASTA](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/generation/20260905T132100Z-31f93b7a/monomer_batches/20260905T132814Z-b9fd64c4/candidates.fasta)、[成对多样性矩阵](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/generation/20260905T132100Z-31f93b7a/monomer_batches/20260905T132814Z-b9fd64c4/diversity.npz)。
- [seed 49 单体结构](../../interaction-design-mvp/artifacts/pilot16-20260905T131413Z-555d0edd/generation/20260905T132100Z-31f93b7a/monomer_batches/20260905T132814Z-b9fd64c4/executions/20260905T133004Z-d9b5e42c/output/candidate0006/binder.pdb)；原设计 CIF 路径与哈希见摘要。
- [批量 CLI、结果复用和失败重试范围](../../interaction-design-mvp/docs/ESMFOLD.md)、[ODesign 环境与生成步骤](../../interaction-design-mvp/docs/ODESIGN_SETUP.md)。

完整结构和运行 artifacts 被 Git 忽略，目前保存在本机；这些本机链接不随新克隆出现。
JSON 摘要、协议、任务和实现可随代码保存；保存了种子与环境不代表跨硬件保证逐位复现。
工程验证：41 项 Python 测试、Ruff 通过；离线构建 wheel/sdist，检查两份 ESMFold worker
均被打包，并在源码目录之外验证 wheel 导入及 monomer CLI。实际输入/输出校验和、
序列、置信度量纲和 RMSD 已检查；测试通过不等于分子功能验证。
