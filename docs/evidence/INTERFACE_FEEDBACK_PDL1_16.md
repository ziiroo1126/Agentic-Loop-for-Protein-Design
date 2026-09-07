# PD-L1：16 候选的界面几何反馈

归档日期：2026-09-06（Asia/Shanghai）。本轮在已完成的 16 个 ESMFold2 复合物上
补充结构化几何观察，复用原始预测，无新增 GPU 推理或候选筛选。

**16 个预测均有目标接触，11 个存在小于 2 Å 的跨链重原子对；没有候选同时接触全部
4 个指定热点。最高 ipTM 候选未保留原始设计中的任何接触残基对。** 这些结果提示
后续决策需要综合检查置信度、目标区域接触和设计一致性，尚不构成 binder 成功率、
实验结合或 Agent 增益的证据。

## 输入、协议与映射

输入为同一生成运行的 seeds 43–58，共 16 个 60 残基 binder，各自与同一 114 残基
PD-L1 片段进行 ESMFold2 回折。两批完成任务与冻结模型协议见
[原始复合物报告](ESMFOLD2_complex_pilot.md)。本轮完整覆盖全部候选，无重复或追加预测。

几何协议在本轮计算前确定：canonical protein 的跨链重原子距离严格小于 5 Å
计为接触，严格小于 2 Å 计为碰撞。接触统计唯一残基对，碰撞同时统计原子对和残基对。
距离约定参考 [DockQ 源码常量](https://github.com/wallnerlab/DockQ/blob/master/src/DockQ/constants.py)；
本轮没有计算 DockQ、能量或 MolProbity clashscore，也未设置接受阈值。

原始参考结构 `PDL1_truncated.pdb` 的 SHA-256 为
`a818a160b7f266fe810f1420c1a6af8be438a33b51c8e8dd19f80b5bc702459a`。
通过读取原始 author 编号、按任务顺序拼接固定片段并验证完整序列，得到：

| 原始热点 | 残基 | 回折目标中的序列位置，从 1 开始 |
| --- | --- | --- |
| B64 | ASN | 45 |
| B68 | PHE | 49 |
| B126 | ARG | 107 |
| B128 | THR | 109 |

不能直接把原始热点编号作为回折后的序列位置。上述热点是任务指定的稀疏残基集合，
不等于整个表位定义；本轮也没有规定必须接触全部 4 个热点才算有效。

## 全部候选

下表来自保存的坐标；ipTM 沿用原始 ESMFold2 输出。碰撞列的单位是跨链原子对。

| 生成 seed | ipTM | 接触残基对 | 接触热点数 | 碰撞原子对 |
| --- | --- | --- | --- | --- |
| 43 | 0.530 | 52 | 2/4 | 1 |
| 44 | 0.137 | 55 | 2/4 | 1 |
| 45 | 0.150 | 43 | 2/4 | 6 |
| 46 | 0.827 | 67 | 3/4 | 0 |
| 47 | 0.173 | 31 | 1/4 | 0 |
| 48 | 0.240 | 46 | 1/4 | 4 |
| 49 | 0.171 | 55 | 1/4 | 3 |
| 50 | 0.190 | 50 | 2/4 | 2 |
| 51 | 0.205 | 69 | 2/4 | 6 |
| 52 | 0.659 | 59 | 3/4 | 0 |
| 53 | 0.314 | 57 | 2/4 | 0 |
| 54 | 0.208 | 49 | 2/4 | 0 |
| 55 | 0.123 | 52 | 1/4 | 4 |
| 56 | 0.211 | 63 | 2/4 | 1 |
| 57 | 0.207 | 61 | 2/4 | 2 |
| 58 | 0.166 | 37 | 1/4 | 1 |

seed 46 接触原始热点 B64、B126、B128，距离 B68 的最近 binder 重原子为 7.42 Å。
它的 ipTM 为 0.827，但生成接触保留率为 **0%**，binder 自身对齐 RMSD 为 8.90 Å，
按 target 对齐后的 binder RMSD 为 25.23 Å。因此，高界面置信度并不确认原始设计构象。

seed 52 同样接触 3 个热点，遗漏 B128；它保留了 5/19 个生成接触残基对，即 **26.3%**。
binder 自身对齐 RMSD 为 1.97 Å，按 target 对齐后为 14.49 Å。这表明只检查 binder
自身形状也会遗漏它相对 target 的位置变化。以上是本开发批次的描述性发现。

## 几何计算对照

使用公开的 [4ZQK PD-1/PD-L1 实验结构](https://www.rcsb.org/structure/4ZQK)，
author chain B 为 PD-1，chain A 为 PD-L1。下载 CIF 共 380,653 字节，SHA-256 为
`f5012ba58d76099ba77ce992562e7df4afead12632678548d4d4f00c268b485f`。
文件通过命令级直连取得，没有使用订阅代理；本轮没有下载模型或修改共享环境。

| 几何对照 | 接触残基对 | 碰撞原子对 | 碰撞残基对 |
| --- | --- | --- | --- |
| 保存的实验坐标 | 56 | 0 | 0 |
| PD-1 平移 1000 Å | 0 | 0 | 0 |
| 将两个链的首个 C-alpha 强制重叠 | 27 | 33 | 8 |

对整个实验复合物施加旋转和平移后，接触与碰撞计数保持不变。对照均通过。
这些检查验证几何计算，不是 ESMFold2 对实验结构的预测测试；人为分离结构也不代表
经过实验测量的非结合蛋白。4ZQK 的构建体与本任务片段不同，本轮没有将任务热点直接
套用到这个对照上。

## 可复现输出与边界

分析目录：
[`20260905T155856Z-1e0cf7ae`](../../interaction-design-mvp/artifacts/interface-feedback/20260905T155856Z-1e0cf7ae/report.md)。
分析用时 **2.65 秒**，包括输入校验和几何对照；不是 GPU 推理时间。
环境为 Python 3.14.6、NumPy 2.5.2、Biotite 1.7.1，应用版本 0.1.0。

- [机器可读摘要](INTERFACE_FEEDBACK_PDL1_16_summary.json)：保留逐候选统计、热点距离、映射和设计一致性。
- [全部候选 CSV](INTERFACE_FEEDBACK_PDL1_16_candidates.csv)：可直接比较的计数与标记。
- [完整观察](../../interaction-design-mvp/artifacts/interface-feedback/20260905T155856Z-1e0cf7ae/observations.json)：包含完整接触列表和最近碰撞原子对。
- [使用说明与计算定义](../../interaction-design-mvp/docs/INTERFACE_FEEDBACK.md)。

从 `interaction-design-mvp/` 可创建一个独立的新分析：

```bash
.venv/bin/interaction-design complex feedback \
  artifacts/complex-assessments/20260905T144133Z-b01a8b0b \
  artifacts/complex-assessments/20260905T145716Z-464c8a4a \
  --control-structure .cache/controls/4ZQK.cif
```

几何分析只使用实际提供的蛋白重原子，不补全缺失侧链；零碰撞不表示完整物理质量合格。
当前所有 `acceptance` 均为 `null`，`binding_validated` 均为 `false`。后续应在独立目标
与适当预测器对照上制定筛选协议，再比较固定流程、反馈启发式与受预算约束的 Agent。

新增 21 项测试覆盖计数单位、严格距离边界、热点映射、刚体变换、输入变更拒绝、失败
记录和已完成输入不被改写。应用合计 74 项测试通过，Ruff 检查通过。
此前生成/单体证据中的 210 个文件及复合物证据中的 101 个文件均通过原有哈希校验。
wheel 与 sdist 已离线构建，wheel 在仓库外解包后通过导入、协议资源和 CLI 检查。
安装包副本、[验证记录](../../interaction-design-mvp/artifacts/interface-feedback/20260905T155856Z-1e0cf7ae/validation.json)
与本轮结果一起保存，由单独的
[证据清单](../../interaction-design-mvp/artifacts/interface-feedback/20260905T155856Z-1e0cf7ae/evidence-manifest.json)
封存，不修改此前的证据清单。
