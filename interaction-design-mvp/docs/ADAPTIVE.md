# 自适应复评与回放

`alpd adaptive` 将已有候选的逐 seed 预测按需揭示，支持宿主决策、
逐轮复盘、固定策略与离线页面。它是开发数据执行器；当前不会重新生成候选或运行 GPU。
完整动作格式、恢复规则和信息边界见
[共享技能中的自适应操作指南](../../plugins/alpd/skills/alpd/references/adaptive-evaluation.md)。

## 先看结果

仓库内附有[PD-L1 宿主案例页面](../../docs/evidence/adaptive-loop-pdl1/index.html)。
下载或克隆后用浏览器打开该 HTML，无需服务；GitHub 文件预览本身不会运行页面脚本。
[案例记录](../../docs/evidence/ADAPTIVE_LOOP_DEVELOPMENT.md)说明数据来源、预定停止规则及结果。

## 无模型的本地示例

在 `interaction-design-mvp/` 中，使用已有开发环境执行：

```bash
.venv/bin/alpd adaptive prepare \
  --imported examples/adaptive-synthetic.json \
  --output artifacts/adaptive-example --quota 2 \
  --budget-per-candidate 3 --policy uniform
.venv/bin/alpd adaptive run artifacts/adaptive-example
.venv/bin/alpd adaptive report artifacts/adaptive-example
.venv/bin/alpd adaptive export artifacts/adaptive-example \
  --output artifacts/adaptive-example-view
```

打开 `artifacts/adaptive-example-view/index.html`。该输入的分数和标签均为虚构，只用于
软件演示；`uniform` 是固定规则，不是 LLM。四个候选共获取 12 次预测，每个观察 3 个 seed。
输出目录必须不存在；重复演示时使用新目录。

实际宿主循环用 `--policy harness --require-review` 准备新会话，然后：

```text
observe → 带证据的计划 → apply → observe → reflect → 下一轮或 select
```

`apply` 与 `reflect` 从文件或 `--submission -` 读取 JSON。`history` 帮助恢复尚待复盘
的动作。提交所有候选池后，`report` 才能揭示已有实验终点。`export` 本身不触发终点
揭示，也不会导出未购买的预测；它可以用于进行中和已完成的会话。

预算包含每个候选的初始 seed；查询数不是实测 GPU 节省。复盘只核查引用的可见证据，
宿主的 `supported/refuted/inconclusive` 属于判断摘要，不是工具认证的生物结论。
旧 v1 会话保持冻结；v2 不迁移旧记录，需要新建会话。

## 接入与环境检查

在仓库根目录执行，目标项目目录需已存在：

```bash
python3 plugins/alpd/tools/local.py install --host codex --project-dir /path/to/work
python3 plugins/alpd/tools/local.py doctor --host codex --project-dir /path/to/work
```

另支持 `claude` 和 `deepseek`。安装仅链接完整共享技能，遇到其他来源的已有技能时
拒绝覆盖。按输出向宿主提供 `ALPD_PROJECT_ROOT`。Doctor 区分已有 CLI 可用、技能
路径就绪、宿主发现未验证及模型会话未启动；详细选项见
[宿主接入](../../plugins/alpd/skills/alpd/references/host-adapters.md)。

给 doctor 增加 `--task TASK.json --runtime RUNTIME.json` 可复用本地资源预检。
等价的核心入口为 `alpd pipeline preflight TASK.json --runtime RUNTIME.json`。
此检查不创建任务或启动推理；资源检查通过不等于 GPU 或真实模型调用已经验证。
