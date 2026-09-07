# ALPD：五分钟上手演示

解压演示 ZIP，保留目录内全部文件，用浏览器打开 `index.html`。
页面不需要网络、服务或 GPU。三维浏览需要启用 JavaScript 和 WebGL。
如果浏览器限制本地嵌入页面，使用页面上的“独立打开”链接。

## 依次体验

1. **准备任务**：查看空草稿的真实检查报告，展开已有案例的完整任务 JSON。
   草稿检查是在生成 demo 时运行的；展开任务只是查看已有输入。
2. **回看运行**：拖动滑块或点击“下一步”，查看候选的初始指标、宿主的选择理由、
   复合物反馈和停止记录。所有动作来自已有运行，不会发起新的计算或模型决策。
3. **三维浏览**：切换候选和结构，尝试隐藏一条链、旋转、点击残基、保存 PNG。
   本机示例中 seed 62 有复合物预测，seed 61 保持未评估。
4. **复评与复盘**：查看另一组开发数据上的逐轮宿主记录。它与前三步的双候选
   任务是不同案例；点击“下一轮”不会运行 GPU。查询数也不表示实测计算节省。
5. **自己运行**：查看实际任务准备、运行、决策提交和结果导出的命令。

两个案例都是开发记录，没有证明新 binder 的实验结合或 Agent 的稳定筛选优势。
导出的历史页面包含最终记录，不能作为隔离未来信息的盲态决策界面。

## 平时怎么用

已有开发环境时，从仓库进入 Python 包目录：

```bash
cd interaction-design-mvp
.venv/bin/interaction-design task init --name my_design --output artifacts/my-design/brief.json
.venv/bin/interaction-design task review artifacts/my-design/brief.json
```

第一次检查会返回 `needs_input`，退出码为 2，表示需要补齐结构、区域、热点和长度。
可以编辑 JSON，也可以让已配置共享 skill 的宿主 Agent 根据你提供的信息整理。
自然语言由宿主理解，CLI 接收结构化 JSON。

补齐后继续：

```bash
.venv/bin/interaction-design task build artifacts/my-design/brief.json \
  --output artifacts/my-design/task.json
.venv/bin/interaction-design pipeline preflight artifacts/my-design/task.json \
  --runtime /absolute/path/to/runtime.json
.venv/bin/interaction-design pipeline prepare artifacts/my-design/task.json \
  --runtime /absolute/path/to/runtime.json \
  --strategy harness --batch-size 1 --max-evaluations 2
```

`runtime.json` 需要指向本机已有模型、代码和运行环境；示例配置在仓库的
`interaction-design-mvp/config/pipeline-runtime.example.json`。预检不运行模型。
准备命令返回 `pipeline_dir`，后续使用返回的实际路径：

```bash
.venv/bin/interaction-design pipeline run <pipeline_dir>
.venv/bin/interaction-design pipeline observe <pipeline_dir>
.venv/bin/interaction-design pipeline apply <pipeline_dir> --decision decision.json
.venv/bin/interaction-design pipeline export <pipeline_dir> --output artifacts/my-results
```

`run` 会执行真实生成和单体检查；harness 模式随后等待选择。宿主依据当前请求的
`response_schema` 编写 `decision.json`；`apply` 执行评估并返回下一请求或完成状态。
按返回状态重复 observe/apply，完成后再 export。打开 `artifacts/my-results/index.html`
查看三维结果。复合物配额不限制生成与单体检查；输出文件或目录须使用新路径。

详细字段和运行说明在仓库的 `interaction-design-mvp/docs/TASK_INPUT.md`、
`PIPELINE.md` 和 `STRUCTURE_VIEWER.md`；宿主配置见 `plugins/` 下的说明。

## 重新生成这个演示包

在仓库根目录，使用已经完成并导出的结果包：

```bash
interaction-design-mvp/.venv/bin/python interaction-design-mvp/scripts/build_demo.py \
  --result-bundle interaction-design-mvp/artifacts/pipeline-development/result-bundle-reviewed \
  --output interaction-design-mvp/artifacts/alpd-demo
```

命令生成 `alpd-demo/index.html` 和旁边的 `alpd-demo.zip`，不下载资源或重新运行模型。
本机开发结果不进入 Git；其他机器需提供自己的完整结果包，替换 `--result-bundle`。
默认附带的复评案例来自仓库 `docs/evidence/adaptive-loop-pdl1/`，可用 `--replay` 替换。

构建过程校验源文件哈希，复制已有结果与决策记录，通过现有 3Dmol.js 导出器生成
结构页面，并为演示生成校验清单。复评页面按当前 ALPD 模板重新渲染，JSON 记录保留
原始字节；原始结果包与归档页面不改写。

没有本地模型结果时，可先按仓库 `interaction-design-mvp/docs/ADAPTIVE.md` 的
四候选合成示例体验 prepare/run/report/export。它使用虚构分数和固定策略，无 LLM
或 GPU 调用，与真实宿主案例的含义不同。
