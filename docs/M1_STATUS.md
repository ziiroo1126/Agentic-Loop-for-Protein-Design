# M1 实施记录

更新至 2026-09-07。**真实生成、ESMFold v1 单体回折与 ESMFold2 复合物评估已跑通。
当前蛋白 binder 流程采用 ESMFold2；AF3/PyRosetta 保留为可选协议，尚未真实运行。
固定流程与非 LLM 反馈控制器已完成真实闭环；宿主子 Agent 已完成候选选择回放。
最终交付采用 skill/插件。已完成公开实验数据的首轮靶点分离策略评测，尚未证明策略增益。**

仓库清理说明：原 BioClaw 聊天应用、部署与演示资源已从当前工作树移除；当前入口为
Python 科研核心和宿主插件。下文提到旧 TypeScript 应用的内容属于历史阶段记录。
已有实验记录、模型和数据保留，目录说明见[仓库首页](../README.zh-CN.md)。

## 上手演示

新增[五分钟上手指南](../interaction-design-mvp/docs/DEMO.md)与
`interaction-design-mvp/scripts/build_demo.py`，从已有结果包生成离线演示目录和 ZIP。
五个章节覆盖空草稿检查、保存的双候选运行、同一任务的三维结构、另一组数据上的
宿主复评案例，以及实际 CLI 操作。当前演示是记录浏览，不启动新的宿主模型或 GPU。

本机产物为 `interaction-design-mvp/artifacts/alpd-demo/index.html` 和旁边的
`alpd-demo.zip`（约 740 KB），不加入 Git。Firefox 验证了五个章节、输入展开、
选择／反馈／停止、嵌入三维渲染、三轮复评、下载链接和 1440／768／450 px 布局；
页面没有 HTTP 资源请求。复制结果、派生回放、ZIP 均通过逐文件校验；已有输出拒绝
覆盖，损坏输入拒绝导出并清理临时目录，原始归档保持有效。

相关自适应导出／回放测试 **5 项通过**，脚本 Ruff 与 JavaScript 语法检查通过。
复评 HTML 当前模板显示 ALPD，既有冻结页面不修改。此前完整 Python 验证为
883 项通过，本次没有重复全量测试或新增科学计算。

## 三维结构浏览

接入固定版本 **3Dmol.js 2.5.5**。`pipeline export` 自动生成带校验和的 `index.html`；
`viewer export` 可在已有结果包外生成独立页面，先核对原 manifest 和文件哈希。
页面内嵌浏览组件、许可证、结构和已有指标，支持候选／结构切换、链颜色与显隐、
残基信息、旋转缩放、PNG 保存和原结构下载。未评估复合物保持缺失状态。

新增 **13 项测试**，完整 Python 检查为 **883 项通过**，Ruff 通过。
复用本地构建缓存生成 wheel，确认静态资源完整，并从解包后的 wheel 导出页面成功。
Firefox 配合临时虚拟显示和软件渲染验证了开发结果中的生成、单体、复合物、参考
结构，链显隐、三种表示、PNG／结构下载和 1440／768／450 px 布局；页面未请求网络
资源。无 WebGL 时保留指标和结构下载。预览写入 Git 忽略的
`interaction-design-mvp/artifacts/structure-viewer-preview/index.html`。

本阶段没有新增模型推理或修改既有实验记录。首版不跨结构转移热点编号、不做结构
叠合或输入约束编辑。使用方式见[三维浏览指南](../interaction-design-mvp/docs/STRUCTURE_VIEWER.md)。

## 用户需求与任务准备

新增 `task init/review/build` 和独立的 `DesignBrief` 草稿格式。用户可先提供目标描述、
已有文件和长度偏好，宿主 Agent 记录未解决的要求，检查报告区分缺失输入与后端限制。
草稿允许热点暂缺和长度范围；编译任务前仍须明确热点与固定长度，并核对真实结构的
链、残基和热点映射。运行环境单独预检，输入准备不下载资源、不运行模型。

中英文首页已增加“用户目标与已有资料 → 任务澄清与结构化准备”的流程图，共享 skill
已接入草稿流程。新增 **53 项合成数据测试**，完整 Python 检查为 **870 项通过**；
Ruff、共享技能校验和宿主启动器的草稿创建／检查验证通过。本阶段没有新增 GPU 推理
或生物实验。字段与操作说明见[任务输入指南](../interaction-design-mvp/docs/TASK_INPUT.md)。

## 自适应宿主循环与过程展示

统一 CLI 与共享技能已接入 `adaptive`；新 v2 会话支持行动计划、证据校验、一次性
结果复盘、公开历史与静态 HTML 导出。增加项目技能安装/doctor 和 `pipeline preflight`。
PD-L1 开发案例由当前 Codex 宿主完成 3 次连续查询、3 次复盘与最终提交：90 个初始
预测加 3 次追加，共 93 次逻辑查询，最终已有标签命中 5/10。该案例不证明增益。

该阶段完整 Python 检查为 **817 项通过**，Ruff、插件测试及共享技能检查通过。
细节见[案例与验证记录](evidence/ADAPTIVE_LOOP_DEVELOPMENT.md)。下文测试数保留为
各历史阶段快照；可靠性模型、协议冻结及新独立验证仍待完成。

## 宿主重复实验

已在原六个开发靶点完成两轮新宿主选择，共 12 个新 Codex 子 Agent；请求、分数、
名额与基线固定。新重复命中 26/60、25/60，强基线为 26/60；首轮 27/60 的领先没有
稳定复现。两轮名单的靶点平均 Jaccard 为 0.909，Twist 终点均为 21/60。

新增 `benchmark-repeat` 协调器及 109 项测试，完整 Python 测试现为 **620 项通过**。
旧证据保持独立；重复不增加生物靶点或实验数量，也未完成新校准或外部盲测。
见 [重复实验记录](evidence/BENCHMARK_REPETITIONS.md)。

## 公开实验数据评测

新增 `benchmark prepare/observe/apply/report`，从固定版本 Anthropic 数据集接入七靶点
630 个候选。PD-L1 用于选择基线，六个评估靶点各 90 个候选、每策略选 10 个。
六个独立 Codex 子 Agent 仅使用匿名计算特征，全部提交锁定后统一揭示实验标签。
Agent 选中 27/60 个来源综合判定阳性，启发式和 EF2 ipSAE 均为 26/60；Twist
终点下 Agent 与启发式均为 21/60。当前不能宣称 Agent 稳定优于强基线。

这是已有候选及事后评分的回顾性分析，不是新 MolClaw binder 的实验验证，也不能证明
完整生成策略或 GPU 节省。该首轮阶段 Python 测试为 **511 项通过**。共享 skill 已支持该 CLI
入口；原有 DeepSeek 原生工具接口保持先前验证范围。见
[评测证据](evidence/EXTERNAL_BENCHMARK_DEVELOPMENT.md) 和
[运行说明](../interaction-design-mvp/docs/EXTERNAL_BENCHMARK.md)。

## 完整任务到导出流程

新增 `pipeline prepare/run/observe/apply/export`，已用两个新 PD-L1 候选完成真实
ODesign 生成和 ESMFold v1 单体检查。宿主子 Agent 选择 seed 62 后，ESMFold2 完成
复合物评估及界面反馈，执行器按上限停止，导出序列、结构、指标、协议与决策轨迹。
恢复和重复提交验证没有新增模型进程。五种原生操作均走官方 DSH 工具模块与同一
Python CLI；选择模型为 Codex 子 Agent，没有调用 DeepSeek 模型。

该流程开发阶段 Python 测试 **346 项通过**，原生桥接 25 项、官方 SDK 5 项通过。
seed 62 的 ipTM 为 0.183789，不支持有效 binder 结论。完整记录与结果包见
[流程开发证据](evidence/PIPELINE_DEVELOPMENT.md)，使用方法见
[PIPELINE.md](../interaction-design-mvp/docs/PIPELINE.md)。

## 宿主 skill / 插件与选择接口

用户进一步指定官方 `deepseek-ai/deepseek-harness` 后，新增 `dsh-molclaw` npm bundle
和 Cordis patch，提供 prepare/observe/apply 三个原生工具及帮助工具。发布版
`0.1.2-rc.1` 的真实 ToolRuntime、Bash 与本地 subprocess provider 已加载该插件并
调用 Python CLI，完成 16 候选池中 2 个候选的确定性回放及上限停止。
这是无 LLM 调用的工具集成验证，没有启动完整 profile/Web UI 或新增 GPU 推理。

新增 `--decision -` 从 stdin 接收结构化提交；宿主工具保留取消、权限与父调用关联。
修复默认主线程 SIGTERM 的 worker 进程组清理，并以 `SystemExit(143)` 允许外层保存
失败回执。该阶段完整 Python 测试 **231 项通过**；原生适配另有 17 项桥接测试、4 项
官方 SDK 检查及 10 项真实工具链检查。详情见
[DeepSeek 适配记录](evidence/DEEPSEEK_HARNESS_ADAPTER.md)。

以下保留首版宿主适配的阶段记录。

新增 `screen prepare/observe/apply/run`，让宿主模型选择已有候选的复合物评估顺序。
执行器冻结输入与协议，校验请求身份、候选范围和精确数值证据，保存请求、响应和工具
产物。相同已完成请求可幂等重交；失败或状态不明的执行不会自动重跑。固定顺序与
Pareto/序列多样性启发式走同一接口。

仓库内 [plugins/molclaw](../plugins/molclaw/README.md) 包含 Codex、Claude Code 两套
manifest、共享 skill、宿主接入说明和可搬移启动脚本。格式验证通过；没有安装到全局
配置。该阶段 DeepSeek 仅提供通用 skill/CLI 契约；现已补充上文的官方原生适配。

独立 Codex collaboration 子 Agent 只接收可见指标，真实选择 seeds 49/48，随后依据
已揭示复合物观察选择 43/44。共两轮、4 个候选，到达上限后由执行器停止。这是原
16 候选的结果回放，无新 GPU 推理；精确宿主模型版本和 token 用量未披露，保留为空。
完整测试 **220 项通过**，Ruff、离线构建和仓库外 wheel 入口/资源检查通过。

该 skill 当前聚焦已有候选选择。自然语言任务到生成、筛选和导出的完整宿主体验、
Claude/DeepSeek 实际模型会话及独立目标策略比较尚待完成。

- [接口与运行说明](../interaction-design-mvp/docs/SCREENING.md)
- [实际宿主决策及完整开发记录](evidence/HARNESS_SELECTION_DEVELOPMENT.md)

## 当前评估后端：ESMFold2

按用户要求，将当前蛋白复合物评估路径切换到标准版 ESMFold2。新增
`complex prepare/run/report`，以及生成命令的 `--complex-config`，可以从真实生成直接
接续离线复合物回折。原 AF3/PyRosetta 入口、16 候选单体基线和封存记录保留。

复用本地 ESMFold2、ESMC-6B、CCD 与现有 Python 环境，在 L20 上完成既定代表候选
seeds 46/54/50 的 binder＋PD-L1 回折。固定无 MSA、seed 1、20 loops、100 diffusion
steps、每候选 1 个样本。整批进程 168.31 秒，峰值 PyTorch 显存 13.44 GiB；三次预测
分别为 13.49、11.62、8.62 秒。没有新增模型下载或修改共享环境。

seed 46 的 ipTM 为 0.827，但 binder 全长对齐 RMSD 为 8.90 Å，按 target 对齐后
binder RMSD 为 25.23 Å，说明高界面置信度不足以确认原设计几何。其他两个候选的
ipTM 约为 0.208、0.190。所有通过标记均为空，没有实验结合或 Agent 收益结论。

随后按同一配置补齐其余 13 个候选，目前 **16/16 复合物评估完成**，无重复采样。
两个批次总进程时间为 370.01 秒（6.17 分钟），ipTM 中位数为 0.206。
新增完整汇总表和软件/权重一致性检查。该阶段 Python 测试为 53 项通过，Ruff 通过。

- [使用、协议、输出与恢复范围](../interaction-design-mvp/docs/ESMFOLD2.md)
- [真实复合物运行结果及证据](evidence/ESMFOLD2_complex_pilot.md)

## 界面几何反馈

新增 `complex feedback`，在 CPU 上读取完成的预测，输出跨链重原子接触、碰撞、指定
热点接触和生成接触保留率。通过原始参考结构与完整目标序列校验，将 B64/68/126/128
映射到回折目标的第 45/49/107/109 个残基。输出包含结构化观察、原始数据哈希、代码和
协议快照；失败也保留记录，原有生成与回折结果不重写。

全部 16 个候选的派生分析已完成，用时 2.65 秒，无额外 GPU 推理。11 个预测有小于
2 Å 的跨链重原子对；最多接触 4 个指定热点中的 3 个。seed 46 的 ipTM 为 0.827，
但生成接触保留率为 0。这些是几何诊断，不能直接转成 binder 成功率。

公开 4ZQK 实验结构及其人为分离、重叠和整体刚体变换对照通过几何计算检查。
对照尚不校准预测器。新增 21 项测试，目前共 **74 项通过**，Ruff 通过。
这些观察已用于下文的固定流程与反馈控制器。筛选标准与独立目标实验需要另外制定。

- [命令、计算定义与限制](../interaction-design-mvp/docs/INTERFACE_FEEDBACK.md)
- [16 候选的完整界面反馈结果](evidence/INTERFACE_FEEDBACK_PDL1_16.md)

## M2 起步：可执行的采样与停止控制器

新增 `campaign prepare/run/replay`。固定策略和反馈策略共用采样、评估、反馈、停止
接口，运行中固定任务约束、模型协议和新种子顺序。反馈策略在连续若干轮没有扩展
诊断指标的 Pareto 前沿时停止；这里比较 ipTM、热点接触、碰撞残基对和目标对齐后的
binder RMSD，没有定义生物学通过阈值。轮数上限始终存在，工具时间上限可选。

原 16 候选按 4 个一批回放时，两种策略均观察完 16 个候选，再到达轮数上限，
当前没有策略收益证据。回放明确标注不运行模型，也不用于估计 GPU 节省。

真实控制器在已有 16 候选反馈基础上，使用新 seeds 59/60 执行一轮生成、ESMFold2
回折与几何反馈，然后按事先确定的一轮上限停止。工具调用耗时 176.96 秒；其中生成
进程 102.19 秒，复合物评估进程 68.32 秒。使用共享 L20 GPU 3 和缓存权重，环境未改动。
两个新候选均接触 1/4 个指定热点，未宣布结合成功。

新增 19 项控制器测试，共 **93 项通过**。覆盖策略分歧、可见历史、重复种子拒绝、
修改输入拒绝、失败保留、已完成步骤恢复和预算停止。本轮没有 LLM 调用；下一阶段
需要接入 LLM 决策适配器，并在更有区分度的动作与独立实验协议上检验其作用。

- [控制器协议、命令与恢复范围](../interaction-design-mvp/docs/CAMPAIGNS.md)
- [开发回放与真实闭环证据](evidence/CONTROLLER_DEVELOPMENT.md)

下文保留此前阶段的实现记录，其中 AF3 环境缺口属于原可选协议，不再阻塞当前 ESMFold2 路径。

## 已验证的真实运行

| 任务 | 配置 | 实际产出 | 生成进程耗时 |
| --- | --- | --- | --- |
| 蛋白—小分子 | aspirin SMILES，30 残基 binder，seed 42 | 1 个 CIF 和序列 | 79.700 秒 |
| PD-L1 蛋白 binder | 靶标 B20–133，热点 B64/B68/B126/B128，60 残基 binder，seed 42 | 1 个复合物 CIF 和序列 | 86.195 秒 |

均在单张 NVIDIA L20 上执行，使用相同的 ODesign/OInvFold 权重。耗时包括模型初始化，
不含启动前的文件校验，也不等于测得的 GPU 利用时间。只有两个单候选测试，不构成质量或吞吐基准。

- [小分子任务报告](../interaction-design-mvp/artifacts/20260905T031647Z-d830dab5/report.md)
- [PD-L1 任务报告](../interaction-design-mvp/artifacts/20260905T031949Z-042eb8af/report.md)
- [可随代码保存的运行摘要、序列与校验和](evidence/M1_run_summary.json)
- [安装、数据来源和复现步骤](../interaction-design-mvp/docs/ODESIGN_SETUP.md)

完整 artifacts 当前保存在本机且被 Git 忽略，以上两个报告链接适用于本工作区；新克隆仓库可以
读取摘要，并按复现步骤生成自己的 artifacts。两次运行没有独立 AF3/PyRosetta 分数，报告中的
score/rank/threshold_pass 均为空。不能据此声称候选已通过筛选或具有实际结合能力。

## 本轮工程改动

1. 保存生成记录后再评估，增加 `interaction-design evaluate <run-dir>`，允许补齐评估文件后重评。
2. 每次评估保存独立 manifest、报告或 failure；原始生成 manifest 保持不变。记录评估输入哈希，
   拒绝被修改的生成结构，并明确当前模式仅导入 sidecar 文件。
3. 生成失败也保留 task、request、failure 和 manifest。真实进程输出实时写入日志，超时后保留日志，
   终止本地进程组；容器失败时尝试清理对应容器。
4. 运行前将实际 checkpoint 校验与锁定版本关联，记录权重、CCD 数据哈希及推理 Python 环境。
5. 修复“越低越好”的指标全部相同时贡献为零的问题，并拒绝 NaN/Infinity 评分。
6. 增加官方 PD-L1 输入的最小生成示例、环境依赖清单与复现说明。

独立评估失败的真实检查记录位于
[小分子任务的失败记录](../interaction-design-mvp/artifacts/20260905T031647Z-d830dab5/evaluations/20260905T032030Z-31032389/failure.json)：
缺少 AF3 sidecar 时返回错误且保留原始候选。恢复成功、结构篡改、权重不匹配和超时行为通过自动化
测试验证；没有为真实候选填入 mock 分数。

## 发现并修复的上游版本问题

旧锁中的 ODesign `87b67de` 已切换到 ProteinMPNN/LigandMPNN，而项目仍配置 OInvFold 权重。
经检查官方历史，切换发生于
[`9982361`](https://github.com/OTeam-AI4S/ODesign/commit/998236111b5f1f5cd81d0c08507e60be3bf5def9)。
本轮把源码固定到其父提交 `43944e930aea7dd74d2762f6cce7855471edae7b`，与现有 OInvFold 资产契约一致。
实际运行的上游源码工作区干净，未修改模型实现。更新到新版逆折叠后端需要单独迁移和验证。

现有约 4.41 GB 的模型参数全部复用，并通过 `hf cache verify` 与固定 Hub 版本核对。
化学组分数据约 554 MB，按来源记录直连下载；依赖也使用缓存或命令范围内的直连。
没有使用订阅代理下载，也没有修改 Clash 或 agent 全局连接设置。

## 验证与剩余工作

- 本地 Python 3.14.6：批量单体评估增量后 41 项测试通过，Ruff 通过。
- wheel 和 sdist 构建通过；wheel 包含新增执行器和更新的资产锁。
- 真实推理使用独立 Python 3.10.0、Torch 2.3.1 环境；完整版本保存在运行记录中。
- 根目录旧 TypeScript 应用未作功能修改；先前发现的测试问题仍独立存在。

原 AF3/PyRosetta 协议仍需要相应环境验证。当前 ESMFold2 路径已有真实运行，后续工作转向
独立靶点上的评估、有预算约束的 Agent 工具循环、演示界面与受控对照实验。
当前可用于工程展示的是实际生成、输入约束映射、版本兼容修复与可恢复的记录链路；Agent 决策收益
和分子设计质量增益仍是后续待验证的研究问题。

## ESMFold 单体回折实测

已复用本机缓存和现有环境，在一张 L20 上预测 60 残基设计 binder 及 114 残基天然 PD-L1 片段。
binder 的平均 Cα pLDDT 为 51.18，与设计骨架的全长 Cα RMSD 为 7.83 Å；天然片段分别为
93.01 和 0.47 Å（相对参考 PDB）。binder 首次推理约 1.06 秒，PyTorch 峰值分配约 13.33 GiB。
这表明本机可以运行 ESMFold，当前设计候选的单体结构支持较弱。没有将其标记为通过筛选。

独立脚本、版本差异处理、原始预测、输入/输出哈希与复算证据均已保存，见
[ESMFold 实测报告](evidence/ESMFOLD_smoke.md)。本轮没有下载或修改已有环境。
这仍是单体结构检查；AF3/PyRosetta 复合物评估和完整 Agent 验证尚未完成。


## 评估接入增量

已增加 `assessment prepare/check/run/import-af3/import-rosetta/report`。准备阶段固定候选身份、
AF3 链映射、目标 MSA 策略、随机种子、单样本采样、PPI XML 与本地进程时间预算。
执行阶段逐候选调用外部 AF3 与 PyRosetta；成功阶段保留回执，失败重试复用已有结果。
导入阶段校验任务、链、序列、结构校验和、样本数、协议和种子，拒绝错误候选与超出约定采样数的结果。

- [评估运行与外部结果导入说明](../interaction-design-mvp/docs/EVALUATION.md)
- [真实 PD-L1 候选的 AF3 输入及环境检查证据](evidence/M1_assessment_preparation.json)
- [本机评估任务状态](../interaction-design-mvp/artifacts/20260905T031949Z-042eb8af/assessments/20260905T034716Z-e5b1f83a/status.json)

这份真实评估任务已准备好，当前状态为 blocked：尚未配置可用 AF3/PyRosetta 环境。
本轮没有下载评估模型或数据库，也没有为真实候选生成模拟分数。恢复、预算、取消和结果身份校验
使用明确标注的合成测试产物验证；本轮测试通过不能替代真实评估验证。

预算目前统计本地外部进程的累计墙钟时间，包含失败尝试，不包含预检、哈希、报告生成或外部导入
所消耗的计算；尚未成为完整的 GPU/LLM 预算系统。完整 Agent 决策循环仍待实现。

## 16 候选 PD-L1 基线

已在生成前固定种子 43–58、60 残基长度、模型版本、分析指标和代表候选选择规则，完成全部
16 个真实生成及 ESMFold 回折。新增 `monomer prepare/run/report`，保存输入/输出校验和、
完整候选表、序列、结构、成对多样性和失败记录；批次只加载一次 ESMFold。

seed 49 的 Cα pLDDT 为 81.99、相对设计的全长 Cα RMSD 为 0.948 Å；seed 48 分别为
82.28 和 1.553 Å。最高置信度的 seed 46 则为 89.08 和 9.418 Å。因此单看置信度不足以
判断序列是否支持原设计骨架；这提供了后续预算分配实验的动机，还没有证明 Agent 收益。

生成成功进程 355.70 秒，首次环境故障尝试 286.27 秒，整批 ESMFold 进程 39.86 秒；
累计外部进程墙钟时间约 11.36 分钟，包含失败成本。共享服务器上的这些耗时不等于 GPU
利用时间或独占设备吞吐。所有候选的 `threshold_pass` 仍为空，没有结合成功率结论。

原定高/中/低置信度代表候选为 seeds 46/54/50，其 AF3 请求已准备，尚未执行。
这三个候选现已完成独立记录的 ESMFold2 复合物评估；后续仍需用预先固定的对照实验
比较固定流程与反馈决策策略。

- [全部 16 个候选、分析、限制与复现入口](evidence/PDL1_16_baseline.md)
- [随代码保存的指标、序列及校验和](evidence/PDL1_16_baseline_summary.json)
- [批量评估命令和恢复范围](../interaction-design-mvp/docs/ESMFOLD.md)
