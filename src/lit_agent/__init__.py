"""Literature Agent —— 面向中文用户的本地文献综述 / 精读 Agent。

模块
----
* :mod:`lit_agent.config`   配置
* :mod:`lit_agent.llm`      LLM 客户端（Ollama / OpenAI 兼容）
* :mod:`lit_agent.search`   学术检索（OpenAlex / arXiv / Crossref）
* :mod:`lit_agent.graph`    LangGraph 综述流水线
* :mod:`lit_agent.reader`   单篇 / 多篇精读
* :mod:`lit_agent.pdf`      PDF 解析与切块
"""

__version__ = "1.0.0"
