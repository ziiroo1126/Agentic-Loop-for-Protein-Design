# ALPD 五分钟演示

在线入口：https://ziiroo1126.github.io/Agentic-Loop-for-Protein-Design/

离线使用：下载图库中的 `alpd-demo.zip`，解压并保留文件目录，打开 `demo/index.html`。
浏览无需模型环境、GPU 或网络；三维显示需要 JavaScript 和 WebGL。
本地嵌入页面受限时，使用“独立打开”链接。Firefox 离线嵌入页面可能不保存下载，
需要保存 PNG 或结构时请点击“独立打开／下载”。

## 演示内容

1. **任务准备**：查看工具实际生成的空草稿缺失项，展开主案例的完整输入。
2. **主案例运行**：查看同一次真实运行的两个候选、宿主选择、工具反馈及预算停止。
   页面列出引用的证据、工具结果和后续行动；原始记录没有单独保存宿主复盘时明确说明。
3. **同一任务的三维结构**：切换候选和结构、旋转缩放、隐藏链、查看残基、下载 PNG 或结构。
4. **独立复评案例**：另一组已有 90 个候选上的三次查询和复盘，与主案例不是同一次运行。
5. **实际使用方法**：任务准备、运行、决策和导出的 CLI 流程。

浏览和导出页面都不运行模型。主案例包含真实历史计算；复评使用缓存预测；
另一个 CPU 软件示例采用合成数据。ALPD 是纯计算项目，不开展湿实验。
案例用于验证软件流程；方法收益需通过独立计算评测与基线比较检验，现有记录尚未证明稳定的 Agent 筛选优势。

## 从仓库重建

新克隆仓库已经包含完整主案例。完成 CPU 安装并激活环境后，在仓库根目录执行：

```bash
python tools/build_site.py --output /tmp/alpd-site
```

打开 `/tmp/alpd-site/index.html`。`downloads/` 包含演示 ZIP、主案例 ZIP 和 `SHA256SUMS`。
构建器从原始校验记录复制结果，使用当前模板生成呈现，原始归档保持不变。
输出路径须不存在。主案例文件见 `examples/pdl1-binder/`。

只构建组合演示，或展示自己的完整结果包：

```bash
python interaction-design-mvp/scripts/build_demo.py \
  --result-bundle examples/pdl1-binder/run --output /tmp/alpd-demo
```

输出为 `/tmp/alpd-demo/index.html` 及 `/tmp/alpd-demo.zip`。
个人结果可以替换 `--result-bundle`，但必须是通过完整性检查的已完成结果包。
主案例复现说明、软件来源、输入和配置模板都随案例单独提供。
