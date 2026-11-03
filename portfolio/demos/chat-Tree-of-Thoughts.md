# 论文对话记录：Tree of Thoughts: Deliberate Problem Solving

> 来源：本地 PDF ｜ n.d. ｜ 对话轮数：4

## 我

这篇论文的核心方法是什么？和 Chain-of-Thought 有什么区别？

## 助手

这篇论文的核心方法是“Tree of Thoughts”(ToT)，它扩展了“Chain of Thought”(CoT)的方法，允许探索连贯的文本单元，即“思想”或“思考链”。ToT 不同于 CoT 的地方在于，ToT 允许语言模型进行更深入的决策过程，包括考虑多个不同的推理路径和自我评估选择来决定下一步行动，以及在必要时向前或向后查看以做出全局选择。CoT 通过引入一个链 z1, ..., zn 来连接输入 x 和输出 y，其中每个 zi 是一个连贯的语言序列，作为解决问题的有意义的中间步骤。而 ToT 则允许探索连贯的文本单元作为解决问题的中间步骤。

## 我

在 Game of 24 上它表现如何？和哪些基线对比？具体数字是多少？

## 助手

在 Game of 24 上，ToT 的成功率为 74%，而 Chain of Thought (CoT) 的成功率为 4.0%。基线包括 IO (输入输出) 模型，其成功率为 7.3%。ToT (b=1) 的成功率为 45%，ToT (b=5) 的成功率为 74%。

## 我

作者自己承认了哪些局限？

## 助手

作者承认了以下局限：
- Deliberate search such as ToT 可能对很多现有任务来说并不必要，因为 GPT-4 已经在很多任务上表现出色（见附录 B.1）。
- 作为初步工作，这篇研究只探索了三个相对简单任务，这些任务挑战了 GPT-4（见附录 B.2 有关 GPT-3.5 实验结果的介绍）。
- 作者提到，这篇研究只探索了三个相对简单任务，这些任务挑战了 GPT-4。

## 我

【提炼观点】实验结果与适用边界

## 助手

### 观点 1
**观点**：使用语言模型进行有目的的推理可以比预编程的规则和学习的模型更灵活且更节省样本。
**原文依据**：
> When applicable, such a deliberate heuristic can be more flexible than programmed rules, and more sample-efficient than learned models.
> —— [p4]
**怎么用**：放在论文的实验部分，解释实验结果
**注意**：适用于不同的问题和思考步骤，但可能不适用于所有情况

### 观点 2
**观点**：通过评估状态的两种策略，可以生成用于启发式转换的值或分类。
**原文依据**：
> Similar to the thought generator, we consider two strategies to evaluate states either independently or together: (a) Value each state independently: V (pθ, S)(s) ∼pvalue θ (v|s) ∀s ∈S, where a value prompt reasons about the state s to generate a scalar value v (e.g. 1-10) or a classifica- tion (e.g. sure/likely/impossible) that could be heuristically turned into a value.
> —— [p4]
**怎么用**：放在论文的方法部分，解释方法
**注意**：评估策略的依据可能因问题和思考步骤而异

### 观点 3
**观点**：在问题解决中，使用语言模型评估其预测的可行性是一个日益重要的步骤。
**原文依据**：
> Using LLMs to assess the viability of their own predictions is becoming an in- creasingly important procedure in problem solving.
> —— [p9]
**怎么用**：放在论文的背景部分，介绍相关研究
**注意**：不适用于所有问题解决任务
