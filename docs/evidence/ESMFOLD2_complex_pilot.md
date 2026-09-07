# ESMFold2：PD-L1 16 候选复合物评估

2026-09-05。已完成标准版 ESMFold2 本地接入和全部 16 个候选的真实复合物回折。
当前 CLI 默认使用 ESMFold2；AF3/PyRosetta 为另行配置的可选协议。没有运行 AF3 或 PyRosetta。

## 实验设置

复用既有 seeds 43–58 的 60 残基 binder，以及同一 114 残基 PD-L1 片段。
先运行原定高/中/低单体置信度代表 seeds 46/54/50，随后以相同配置补齐其余 13 个候选。
每个候选只运行一次，没有按复合物分数挑选样本或重试随机种子。两批的模型文件、软件版本、worker 哈希和精度经汇总脚本核对一致。

- 标准 ESMFold2 + ESMC-6B；全部权重和 CCD 使用缓存，无新增下载。
- Binder 为 A，target 为 B；仅序列输入，无外部 MSA 或结构模板。
- seed 1、20 folding loops、100 diffusion steps、1 diffusion sample、LM dropout 0.3。
- 折叠参数 float32，保留上游 bfloat16 autocast；ESMC bfloat16；关闭 TF32。
- 共享服务器 GPU 7，NVIDIA L20；批内模型只加载一次、候选依次执行。
- 固定模型版本见[协议](../../interaction-design-mvp/config/esmfold2-ppi.protocol.json)。

## 全部结果

按 ESMFold2 ipTM 降序列出；pLDDT 为 binder 残基均值，0–100。RMSD 均相对原生成结构，单位 Å。
“target 对齐后”表示只对齐靶蛋白，再计算 binder 位置误差；不能解释成 DockQ 或实验亲和力。

| 生成 seed | ipTM | Binder pLDDT | Binder 单独对齐 RMSD | Target 对齐后 binder RMSD |
| --- | --- | --- | --- | --- |
| 46 | 0.8270 | 83.85 | 8.898 | 25.232 |
| 52 | 0.6593 | 63.27 | 1.974 | 14.491 |
| 43 | 0.5298 | 63.03 | 8.872 | 14.658 |
| 53 | 0.3137 | 44.16 | 10.726 | 20.200 |
| 48 | 0.2402 | 61.98 | 1.840 | 7.193 |
| 56 | 0.2115 | 49.37 | 2.702 | 21.313 |
| 54 | 0.2078 | 50.56 | 11.518 | 20.735 |
| 57 | 0.2070 | 46.52 | 9.567 | 14.812 |
| 51 | 0.2046 | 40.33 | 8.994 | 13.848 |
| 50 | 0.1895 | 43.70 | 5.298 | 17.462 |
| 47 | 0.1726 | 55.75 | 12.969 | 26.414 |
| 49 | 0.1713 | 56.87 | 1.224 | 17.635 |
| 58 | 0.1663 | 42.92 | 6.254 | 14.411 |
| 45 | 0.1500 | 50.88 | 7.559 | 15.303 |
| 44 | 0.1371 | 49.20 | 3.165 | 19.472 |
| 55 | 0.1234 | 47.22 | 4.848 | 20.509 |

本开发集暴露了置信度与原设计一致性的差异：seed 46 的 ipTM 最高（0.827），但 binder 单独对齐 RMSD 为 8.90 Å，target 对齐后为 25.23 Å。
seed 52 的 ipTM 为 0.659，binder 单独对齐 RMSD 为 1.97 Å，但 target 对齐后仍为 14.49 Å。
这些结果可用于设计后续筛选与预算实验，尚不能认定任一候选通过结合验证。
同一候选在 ESMFold v1 单体和 ESMFold2 复合物条件下的分数与结构也不能直接等同。

## 资源与工程验证

- 两个外部进程合计 **370.01 秒**（6.17 分钟），包含校验权重、加载、预测和退出。
- 逐候选预测时间合计 **182.95 秒**；范围 8.62–13.49 秒。
- 峰值 PyTorch 分配 **13.44 GiB**，不含其他进程或 CUDA context。
- 模型加载约 12.69 秒（首批）；总进程时间还包含 26 GB 级缓存资产的 SHA-256 校验。
- 53 项 Python 测试通过，Ruff 通过；测试覆盖链映射、身份/协议/坐标/置信度校验、预算耗尽、完成复用和默认 ESMFold2 路径。
- 原单体基线封存的 210 个文件已逐一核验，未改变；真实候选没有使用模拟分数。

运行前发现继承的 `LD_LIBRARY_PATH` 导致 `libnvJitLink` 符号冲突，已通过单个 worker 的环境设置解决。
日志保留上游对 flash-attn 2.7.4 超出声明支持范围的警告；本次推理与输出校验成功，但尚未做跨内核数值一致性实验。
耗时来自共享 GPU，不代表独占设备吞吐。两批均成功，没有 GPU 推理失败或超预算。

## 复现与产物

[使用说明](../../interaction-design-mvp/docs/ESMFOLD2.md) · [完整机器可读摘要](ESMFOLD2_complex_pilot_summary.json) · [候选 CSV](ESMFOLD2_complex_candidates.csv)

- [批次 20260905T144133Z-b01a8b0b](../../interaction-design-mvp/artifacts/complex-assessments/20260905T144133Z-b01a8b0b/report.md)，外部进程 168.31 秒。
- [批次 20260905T145716Z-464c8a4a](../../interaction-design-mvp/artifacts/complex-assessments/20260905T145716Z-464c8a4a/report.md)，外部进程 201.70 秒。

可从已经完成的两个任务重新校验并汇总：

```bash
cd interaction-design-mvp
.venv/bin/python scripts/summarize_complex_batches.py \
  artifacts/complex-assessments/20260905T144133Z-b01a8b0b \
  artifacts/complex-assessments/20260905T145716Z-464c8a4a \
  --output-dir artifacts/esmfold2-pdl1-16-summary
```

完整 CIF、PAE 数组、运行日志、校验和在本机 artifacts 中，未纳入 Git；摘要和 CSV 可随代码保存。
这仍是单一开发靶点、每个序列一个模型样本的计算实验。没有实验结合成功率、独立测试集表现或 Agent 决策收益结论。
