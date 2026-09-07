# MolClaw

[English](README.md)

MolClaw 提供可复现的分子相互作用设计流程，以及供宿主 Agent 调用的科学工具。
Python 核心负责科学任务、模型执行、有预算约束的候选选择、状态记录与结果导出；
宿主接入以 Codex、Claude Code 和 DeepSeek Harness 的 skill／插件形式交付。

当前流程连接 ODesign 生成、ESMFold 单体检查和 ESMFold2 复合物评估。
已有公开数据评测及宿主重复实验记录，尚未证明稳定的 LLM 筛选优势。
下一阶段研究自适应分配追加评估机会的作用。

现已提供 `adaptive` 连续复评入口，串起待检验判断、预测查询、逐轮复盘与离线回放。
可先打开仓库内的 [PD-L1 案例页面](docs/evidence/adaptive-loop-pdl1/index.html)，
或按[自适应使用指南](interaction-design-mvp/docs/ADAPTIVE.md)运行无需模型的示例。
案例属于开发数据演示，不是独立验证。

## 目录结构

```text
interaction-design-mvp/      Python 包、测试、协议与任务示例
  src/interaction_design/   科学工作流与命令行入口
  config/                   固定版本资产与评估协议
  docs/                     运行和评测说明
  artifacts/                本机实验产物，Git 忽略
  models/、data/            本机模型资产与外部数据，Git 忽略
plugins/molclaw/            共享 skill 与宿主适配
docs/                      项目计划、研究目标与实验记录
.github/workflows/         Python 持续集成
```

## 使用入口

本机已经准备好 Python 开发环境时，在仓库根目录执行：

```bash
cd interaction-design-mvp
.venv/bin/interaction-design --help
.venv/bin/interaction-design validate examples/ligand_binder.json
.venv/bin/interaction-design pipeline --help
```

环境准备和纯 CPU 示例见 [Python 包说明](interaction-design-mvp/README.md)，
宿主接入见 [插件说明](plugins/molclaw/README.md)，
完整任务到导出流程见 [Pipeline 指南](interaction-design-mvp/docs/PIPELINE.md)。

## 开发与研究记录

- [开发和本地检查](CONTRIBUTING.md)
- [项目计划](docs/PROJECT_PLAN.md)
- [实施与验证记录](docs/M1_STATUS.md)
- [当前研究目标](docs/RESEARCH_GOAL.md)
- [实验记录](docs/evidence/)

模型权重、虚拟环境和完整实验产物属于本机资源，新克隆仓库不会自动获得。
下载或安装前优先复用本地缓存与现有环境。

## 项目历史与许可

MolClaw 起源于 BioClaw／NanoClaw 聊天应用。旧版聊天应用及其部署资源已从当前
工作树清理，原始代码可从 Git 历史查阅。当前维护的执行核心位于
`interaction-design-mvp/`，宿主适配位于 `plugins/molclaw/`。

保留原始[仓库许可证](LICENSE)、[Python 包许可证](interaction-design-mvp/LICENSE)
以及 `interaction-design-mvp/config/` 中的上游许可声明。
