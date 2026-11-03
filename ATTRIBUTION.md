# 版权与来源说明（ATTRIBUTION）

## 一、上游项目

本项目的**骨架与起点**来自：

- 仓库：[langchain-ai/local-deep-researcher](https://github.com/langchain-ai/local-deep-researcher)
- 许可：**MIT License**
- 版权：Copyright (c) 2025 Lance Martin

MIT 许可明确允许：使用、复制、修改、合并、发布、分发、再许可和销售，
**唯一条件是保留版权声明与许可声明**。因此：

> **把改造后的版本重新公开到 GitHub 完全合法，不需要上游作者额外授权。**

本仓库的 [`LICENSE`](LICENSE) 同时保留了上游的版权声明和本项目的版权声明。

## 二、本项目做了哪些改造

与上游相比，当前仓库是**大幅重写**，不是简单换皮：

| 部分 | 上游原版 | 本项目 |
|---|---|---|
| 核心用途 | 通用网页深度研究（DuckDuckGo 等网页搜索） | 中文学术文献 Agent（Crossref / DOAJ / arXiv 学术检索） |
| 核心代码 | `src/ollama_deep_researcher/`（已删除） | `src/lit_agent/`（全新编写：检索 / 精读 / 综述 / 核验 / 对话 / 文献库） |
| 界面 | LangGraph Studio + 命令行 | Streamlit 网页界面（5 个标签页） |
| 评测 | 无 | `eval/`：10 主题 A/B 评测（可复跑） |
| 用户测试 | 无 | `user_test/`：5 人可用性测试套件 |
| 作品集 | 无 | `portfolio/`：架构图、截图、案例、样例产出 |

上游的原始 README 存档在 [`docs/上游README-原版.md`](docs/上游README-原版.md)，
其中提到的 `Dockerfile`、`langgraph.json`、`web research` 等属于原版方案，当前仓库已不再包含。

> 本仓库是**个人学习/作品集项目**，与上游作者及 LangChain 官方**没有隶属或背书关系**，
> 也不代表上游观点。

## 三、本项目新增部分的许可

`src/lit_agent/`、入口脚本、Streamlit 界面、评测与测试套件等新增代码：
**MIT License，Copyright (c) 2026 Your Name**。

你可以自由使用、修改、再分发，保留本文件与 `LICENSE` 即可。

## 四、第三方依赖的许可（均为 pip 安装，未打包其源码）

| 依赖 | 许可 | 说明 |
|---|---|---|
| langgraph | MIT | 编排框架 |
| streamlit | Apache-2.0 | 网页界面 |
| pymupdf | **AGPL-3.0**（或商业许可） | PDF 解析。仅作为依赖由用户自行 `pip install`，本仓库**不分发其代码**；如未来要打包成桌面应用分发或商业化，建议换成 `pypdf` 或购买其商业许可 |
| httpx | BSD-3-Clause | HTTP 客户端 |
| python-dotenv | BSD-3-Clause | 读取 `.env` |
| typing-extensions | PSF-2.0 | 类型标注向后兼容 |

## 五、数据与论文内容的合规说明

- 本项目只通过公开 API（Crossref / DOAJ / arXiv / OpenAlex）获取**标题、作者、摘要、DOI 等元数据**；
- **不爬取知网等无公开 API 的数据库**，合规路径是：用户自己下载 PDF → 本地解析；
- **不存储、不再分发论文全文**：`papers/*.pdf`、`.cache/` 均已在 `.gitignore` 中排除；
- `portfolio/demos/` 中的样例产出只包含**论文标题、DOI、短摘要引用**，用于演示工具能力，
  版权归原作者/出版方所有；如用于正式发表请按原文引用；
- 使用者需自行遵守所在机构与出版方的数据库使用条款。

## 六、如果你要 fork / 二次发布

1. 保留 `LICENSE`（含上游版权声明）；
2. 保留本 `ATTRIBUTION.md`，或在 README 中写明来源与改动；
3. 不要暗示上游作者为你背书；
4. 不要把论文全文、数据库导出文件提交进仓库；
5. 商业化前重新评估 **PyMuPDF（AGPL）** 的许可成本。
