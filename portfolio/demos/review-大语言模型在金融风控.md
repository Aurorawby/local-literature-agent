# 大语言模型在金融风控中的应用 —— 文献综述

> 自动生成于 2026-10-26 22:34 ｜ 模型 `ollama/qwen2.5:3b` ｜ 覆盖 8 篇文献 ｜ 年份 2025–2026 ｜ 数字可追溯率 100.0%

## 一、综述正文

## 研究背景与问题
大语言模型（LLMs）在金融领域的应用日益广泛，尤其是在风控领域。然而，随着LLMs在金融风控中的应用增多，其责任挑战、解释能力、以及与现有模型的比较等问题也逐渐凸显。本文旨在综述这些研究，探讨大语言模型在金融风控中的应用及其局限性。

## 主要方法与技术路线
研究主要采用文献综述的方法，通过Web of Science和Scopus数据库筛选和分析相关文献。研究方法包括系统综述和六决策框架。数据主要来源于学术论文和公开数据集。

## 关键发现与证据
### 责任挑战与解释能力
多项研究指出，大语言模型在金融风控中的应用带来了责任挑战，责任研究集中在操作-技术模式[1]。此外，研究发现LLMs在信用风险模型中的后解释能力有限[2]，在自主生成解释时对齐有限。在解释现代信用风险模型的复杂性导致沟通障碍方面，研究开发了三种解释管道，使用三种大语言模型进行解释[3]。研究还发现，Transformer架构优于传统方法，AUC提升3.08%，KS增加10.3%[4]。

### 预测与管理
在预测与管理方面，研究发现大语言模型在预测合并撤回方面表现优异[5]，在尼日利亚银行的信用和流动性风险管理和披露方面也有显著影响[6]。此外，研究还探讨了大语言模型在情绪分析和投资组合优化中的应用[8]，发现其在提升投资组合优化效果方面具有潜力。

### 系统性局限
尽管大语言模型在金融风控中的应用显示出显著优势，但研究也指出了一些局限性。责任模式的构成贡献相对较少[1]，仅在特定数据集上进行测试[3]，且结果对替代的累计异常回报测量不稳健[5]。此外，现有研究主要集中在单一模型的使用，未考虑其他模型的性能[2][4][5][6][8]。

## 分歧、争议与局限
尽管大语言模型在金融风控中的应用显示出显著优势，但其责任挑战、解释能力、以及与现有模型的比较等问题仍存在争议。责任研究集中在操作-技术模式[1]，但责任模式的构成贡献相对较少[1]。此外，仅在特定数据集上进行测试[3]，且结果对替代的累计异常回报测量不稳健[5]。现有研究主要集中在单一模型的使用，未考虑其他模型的性能[2][4][5][6][8]。

## 研究空白与未来方向
尽管已有研究在大语言模型在金融风控中的应用方面取得了一定进展，但仍存在许多研究空白。未来研究可以进一步探讨大语言模型在金融风控中的责任挑战，以及在不同数据集和模型上的表现。此外，研究还可以探讨大语言模型在金融风控中的解释能力，以及如何提高其在自主生成解释时的对齐程度。同时，未来研究可以探讨大语言模型在金融风控中的应用，特别是在情绪分析和投资组合优化中的作用。

## 二、核心文献对比

| # | 论文 | 年份 | 来源 | 方法 | 主要发现 |
|---|------|------|------|------|----------|
| [1] | Responsible Large Language Models in Finance: … | 2026 | FinTech | 文献综述，使用Web of Science和Scopus筛选和分析 | 责任研究集中在操作-技术模式，构成贡献集中在规范和混合模式 |
| [2] | Could Large Language Models work as Post-hoc E… | 2026 | arXiv | 使用LendingClub数据集比较LLMs输出与SHAP和系数基元… | LLMs可靠地在受控提示下重现参考排名，但在自主生成解释时对齐有限 |
| [3] | Communicating Credit Risk with Large Language … | 2026 | arXiv | 开发三种解释管道，使用三种大语言模型进行解释 | 解释质量受证据表示约束，而非模型 |
| [4] | Transformer-based NLP approaches for credit ri… | 2026 | Frontiers in Artificial In… | 系统综述，使用Scopus、ScienceDirect和Web of… | Transformer架构优于传统方法，AUC提升3.08%，KS增加10.3% |
| [5] | LLM-Based Merger Withdrawal Prediction and Mod… | 2026 | Journal of Risk and Financ… | 大语言模型结合序列化合并特征和文本信息 | 提高撤回合并检测，保持排名性能 |
| [6] | Risk Management Practices and Financial Perfor… | 2025 | Journal of Risk and Financ… | 使用面板回归分析和广义矩估计法 | 流动性风险披露和公司规模对财务绩效有显著且正的影响；信用风险披露、信用风险、公司年龄和杠… |
| [7] | Responsible Innovation: A Strategic Framework … | 2025 | arXiv | 六决策框架 | 无 |
| [8] | Enhancing portfolio optimization with multi-LL… | 2025 | Investment Management and … | 使用长短期记忆网络整合三个金融领域微调的LLM情绪得分 | 年化回报率31.22%，夏普比率3.02，Omega比率2.48，Jensen’s Al… |

## 三、研究空白与下一步

- LLM在金融风控中的隐私风险
- LLM在金融风控中的公平性
- LLM在金融风控中的可解释性

## 四、参考文献

[1] Chong Hui Tan, Qinxu Ding. Responsible Large Language Models in Finance: A Descriptive Bibliometric Overview and Taxonomy of Responsibility. FinTech, 2026. DOI: 10.3390/fintech5030071
[2] Wenxi Geng, Dingyuan Liu, Liya Li, Yiqing Wang. Could Large Language Models work as Post-hoc Explainability Tools in Credit Risk Models?. arXiv, 2026. https://arxiv.org/abs/2602.18895v2
[3] Sahab Zandi, Noah Kostesku, Christophe Mues, María Óskarsdóttir, Cristián Bravo. Communicating Credit Risk with Large Language Models: Evaluation of Explanations from Standard and Alternative Data-Based Models. arXiv, 2026. https://arxiv.org/abs/2608.17715v1
[4] Pfarelo Raliphada, Seun Olukanmi, Micheal Olusanya. Transformer-based NLP approaches for credit risk prediction: a systematic review. Frontiers in Artificial Intelligence, 2026. DOI: 10.3389/frai.2026.1819994
[5] Muntasir Shohrab, Zhibo Ye, Yanguang Liu, Dantong Yu. LLM-Based Merger Withdrawal Prediction and Model–Market Disagreement Around Climate Risk. Journal of Risk and Financial Management, 2026. DOI: 10.3390/jrfm19070488
[6] Omobolade Stephen Ogundele, Lethiwe Nzama. Risk Management Practices and Financial Performance: Analysing Credit and Liquidity Risk Management and Disclosures by Nigerian Banks. Journal of Risk and Financial Management, 2025. DOI: 10.3390/jrfm18040198
[7] Ahmadreza Tavasoli, Maedeh Sharbaf, Seyed Mohamad Madani. Responsible Innovation: A Strategic Framework for Financial LLM Integration. arXiv, 2025. https://arxiv.org/abs/2504.02165v1
[8] Lamukanyani Alson Mantshimuli, John Weirstrass Muteba Mwamba. Enhancing portfolio optimization with multi-LLM sentiment aggregation: A Black-Litterman integration approach. Investment Management and Financial Innovations, 2025. DOI: 10.21511/imfi.22(3).2025.16

## 五、引用核验（自动）

- **编号有效性**：正文 23 处引用，有效 23 处，全部有效
- **引用覆盖**：8 篇参考文献中 7 篇被正文引用（未被引用：[7]）
- **数字回查**：2 个数字中 2 个能在所引论文中找到依据
- **数字可追溯率**：**100.0%**

本次检查未发现可疑引用或数字。

> 说明：编号与数字核验是**确定性检查**（不依赖模型判断）；数字回查找不到，可能是综述改写时写错了数字，也可能是原文用了不同写法，都需要回到原文确认。

---

### 方法与局限说明

- **检索来源**：Crossref（期刊）、DOAJ（开放获取期刊）、arXiv（预印本）
- **年份范围**：2019 至今
- **检索式**：large language model finance risk；language model credit scoring；transformer fraud detection；大语言模型在金融风控中的应用；LLM financial risk management privacy risk；LLM financial risk management fairness
- **流程**：检索 → 相关性筛选 → 逐篇结构化精读 → 主题合成 → 反思补检（共 2 轮）→ 引用核验
- **引用核验**：编号有效性与数字回查均为**确定性检查**（不依赖模型判断）；语义抽检默认关闭。
- **局限**：精读基于论文摘要，可能遗漏正文细节；AI 抽取的结论需回到原文核对；未覆盖知网等需要机构权限的中文数据库。
