# ESMFold 本机实测

2026-09-05。复用已缓存的 `facebook/esmfold_v1`，在单张 NVIDIA L20 上完成两次真实单链预测。
本轮没有下载权重或依赖，没有修改已有模型环境。完整数值和校验和见
[运行摘要](ESMFOLD_smoke_summary.json)，复现接口见
[ESMFold 运行说明](../../interaction-design-mvp/docs/ESMFOLD.md)。

| 输入 | 长度 | 平均 Cα pLDDT（0–100） | pTM | 全长 Cα RMSD | 首次推理 |
| --- | ---: | ---: | ---: | ---: | ---: |
| ODesign 生成的 binder | 60 | 51.18 | 0.320 | 7.827 Å，相对设计骨架 | 1.062 秒 |
| 天然 PD-L1 片段 | 114 | 93.01 | 0.886 | 0.466 Å，相对参考 PDB 的 B20–133 | 1.112 秒 |

天然片段用于检查模型加载和指标计算，不是配对的 binder 实验对照。这里没有控制长度，
也未审查该天然结构是否出现在模型训练数据中，不能据此报告泛化准确率或筛选有效性。

## 资源和配置

- 复用 `/nvme-data3/yusen/micomamba/envs/nt/bin/python`：Python 3.10.0、
  PyTorch 2.7.1+cu118、Transformers 4.52.4。
- 权重快照 `75a3841ee059df2bf4d56688166c8fb459ddd97a`，约 8.44 GB。
  实际权重 SHA-256 为 `2ee07356b125d1e3e57503c204111fd7323347fc4735d41d3caac57c2a78e116`。
  校验与本地 HF 内容哈希一致；没有请求 Hub 做新一轮远端验证。
- 单张 L20，float32，TF32 关闭，batch 1，4 个 CPU 线程，attention chunk 128，
  配置默认 4 次 folding trunk pass。
- binder 模型加载和 GPU 传输约 9.52 秒，完整进程约 24.88 秒；1.06 秒只指同步后的
  首次模型推理，不含权重校验、加载、Python 导入和文件保存。
- binder 的 PyTorch 峰值分配约 13.33 GiB、峰值保留约 13.35 GiB；天然片段的峰值分配
  约 13.40 GiB。数值不包含 CUDA context 和其他进程，不能当作整卡占用峰值。

## 结果解释与校验

当前 binder 的单体回折置信度较低，与生成骨架有较大的全长偏差。这次检查没有为设计骨架
提供强支持。模型输入只有单条序列，没有 PD-L1 伙伴链，因此不能据此断言它必然不结合，
也没有把结果冒充为 AF3/PyRosetta 复合物评分。没有事后设定筛选阈值，`threshold_pass` 仍为空。

原始 Transformers 输出的 pLDDT 为 0–1；报告和 PDB B-factor 已明确转换到 0–100。
平均 Cα pLDDT 与按已有原子加权的平均 pLDDT 不同：后者分别为 49.46 和 88.06。
原始数值、原子掩码、未舍入坐标和 PAE 保存在 NPZ 中。

两份输出均独立核对了 PDB 序列、Cα 数量、坐标和 pLDDT。RMSD 使用全部匹配残基作一次
刚体对齐，不截断或剔除离群点，并由 Biotite 的独立实现复算。原始生成 manifest 的
15 个文件校验和保持不变。

首次严格加载因版本差异停止，失败日志已保留。核对 Transformers 源码后，仅允许当前版本中
未使用的辅助 contact head 与旧 absolute-position tensor 差异；加入钩子，若辅助 head 被调用
立即报错。两次成功预测均未触发该钩子，所有参与结构预测的参数正常加载。

## 本机产物

- [binder 预测 PDB](../../interaction-design-mvp/artifacts/20260905T031949Z-042eb8af/monomer_refolds/20260905T124211Z-3541ef47/prediction/binder.pdb)
- [binder 指标](../../interaction-design-mvp/artifacts/20260905T031949Z-042eb8af/monomer_refolds/20260905T124211Z-3541ef47/prediction/metrics.json)
- [天然 PD-L1 片段预测 PDB](../../interaction-design-mvp/artifacts/20260905T031949Z-042eb8af/monomer_refolds/20260905T124533Z-9c1f84fd/prediction/binder.pdb)
- [天然片段指标](../../interaction-design-mvp/artifacts/20260905T031949Z-042eb8af/monomer_refolds/20260905T124533Z-9c1f84fd/prediction/metrics.json)

这些 artifacts 链接适用于当前工作区，生成文件不进入 Git。上面的摘要随代码保存。
