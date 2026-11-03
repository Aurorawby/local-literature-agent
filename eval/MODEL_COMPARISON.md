# 模型对比：结构化抽取可靠性（3B vs 7B）

- 论文：`2305.10601`, `2201.11903`, `2203.11171`（同一批 PDF、同一套提示词、temperature=0.1）
- 任务：逐篇抽取「研究问题 / 方法 / 数据 / 发现 / 局限」五个字段
- 判定：字段回显 = 输出里出现字段名或说明文字（小模型典型失败模式）；有效字段 = 非空且非「未提及」

## 总览

| 模型 | 平均耗时/篇 | 有效字段率 | 字段回显次数 | 有回显的篇数 |
|---|---|---|---|---|
| qwen2.5:3b | 12.6s | 80% | 0 | 0/3 |
| qwen2.5:7b | 35.7s | 100% | 0 | 0/3 |

## 逐篇明细

| 模型 | 论文 | 耗时 | 有效字段 | 回显字段 |
|---|---|---|---|---|
| qwen2.5:3b | 2305.10601 | 8.7s | 2/5 | 无 |
| qwen2.5:3b | 2201.11903 | 15.3s | 5/5 | 无 |
| qwen2.5:3b | 2203.11171 | 13.9s | 5/5 | 无 |
| qwen2.5:7b | 2305.10601 | 46.8s | 5/5 | 无 |
| qwen2.5:7b | 2201.11903 | 33.3s | 5/5 | 无 |
| qwen2.5:7b | 2203.11171 | 26.9s | 5/5 | 无 |

## 抽取结果原文（便于人工核对）

### qwen2.5:3b · 2305.10601 Tree of Thoughts: Deliberate Problem Solving with Large Language Model

```json
{
  "problem": "现有语言模型在推理过程中受限于token-level决策",
  "method": "引入Tree of Thoughts框架，扩展Chain of Thought方法",
  "data": "未提及",
  "findings": "未提及",
  "limitations": "未提及"
}
```

### qwen2.5:3b · 2201.11903 Chain-of-Thought Prompting Elicits Reasoning in Large Language Models

```json
{
  "problem": "如何通过链式思维提高大型语言模型的复杂推理能力",
  "method": "提供链式思维示例作为提示",
  "data": "三种大型语言模型",
  "findings": "改善了算术、常识和符号推理任务的性能",
  "limitations": "未提及局限性"
}
```

### qwen2.5:3b · 2203.11171 Self-Consistency Improves Chain of Thought Reasoning in Language Model

```json
{
  "problem": "语言模型在复杂推理任务上的性能提升问题",
  "method": "自一致性策略",
  "data": "预训练的大语言模型",
  "findings": "自一致性策略提高了语言模型的链式思考推理能力",
  "limitations": "未提及局限"
}
```

### qwen2.5:7b · 2305.10601 Tree of Thoughts: Deliberate Problem Solving with Large Language Model

```json
{
  "problem": "现有语言模型在需要探索或前瞻决策的任务中表现不佳",
  "method": "引入Tree of Thoughts框架",
  "data": "摘要未提及",
  "findings": "Tree of Thoughts框架能促进语言模型在文本单元上的探索",
  "limitations": "摘要未提及"
}
```

### qwen2.5:7b · 2201.11903 Chain-of-Thought Prompting Elicits Reasoning in Large Language Models

```json
{
  "problem": "提高大型语言模型进行复杂推理的能力",
  "method": "链式思维提示方法",
  "data": "三个大型语言模型",
  "findings": "链式思维提示在算术、常识和符号推理任务上表现优异",
  "limitations": "摘要未提及"
}
```

### qwen2.5:7b · 2203.11171 Self-Consistency Improves Chain of Thought Reasoning in Language Model

```json
{
  "problem": "复杂推理任务中链式推理表现不佳",
  "method": "提出自一致性解码策略",
  "data": "预训练大型语言模型",
  "findings": "自一致性策略能提高链式推理准确性",
  "limitations": "摘要未提及"
}
```

## 结论（自动生成，需人工确认）

- 有效字段率：qwen2.5:3b 80% vs qwen2.5:7b 100%
- 平均耗时：qwen2.5:3b 12.6s vs qwen2.5:7b 35.7s（2.8×）
- 字段回显：qwen2.5:3b 0 次 / qwen2.5:7b 0 次
- ⚠️ 样本仅 3 篇，结论是方向性的；7B 在 CPU 上明显更慢，是否值得取决于用户对质量的容忍度。

> 复跑：`.venv\Scripts\python.exe eval/compare_models.py`
