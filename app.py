"""文献 Agent 的本地 Web 界面。

启动方式（推荐双击 启动界面.bat）：
    .venv\\Scripts\\python.exe -m streamlit run app.py

界面分三块：
* 左侧：模型 / 轮数 / 年份等设置（可在这里切换本地 Ollama 或云端 DeepSeek）
* 「文献综述」标签页：输入主题 → 看进度 → 阅读报告 / 下载 Markdown + JSON
* 「单篇精读」标签页：上传 PDF 或粘贴 arXiv / DOI → 生成中文精读笔记
"""

from __future__ import annotations

import json
import sys
import time
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from lit_agent.chat import PaperChat, load_paper  # noqa: E402
from lit_agent.cli import write_output  # noqa: E402
from lit_agent.audit import (  # noqa: E402
    build_chat_audit,
    build_review_audit,
    render_html as render_audit_html,
    render_markdown as render_audit_markdown,
)
from lit_agent.library import PaperLibrary  # noqa: E402
from lit_agent.config import PROJECT_ROOT, Settings  # noqa: E402
from lit_agent.graph import build_graph  # noqa: E402
from lit_agent.llm import ChatLLM, LLMError  # noqa: E402
from lit_agent.reader import (  # noqa: E402
    compare_pdfs,
    parse_arxiv_id,
    read_arxiv,
    read_doi,
    read_pdf_file,
)

st.set_page_config(
    page_title="文献综述 Agent",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded",
)

UPLOAD_DIR = PROJECT_ROOT / ".cache" / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

BASE = Settings.load()


# --------------------------------------------------------------------------
# 侧边栏
# --------------------------------------------------------------------------
def sidebar() -> Settings:
    settings = deepcopy(BASE)
    with st.sidebar:
        st.title("📚 文献 Agent")
        st.caption("本地优先 · 免费学术检索 · 带引用核验")

        st.subheader("模型")
        provider_label = st.radio(
            "模型来源",
            ["本地 Ollama（免费）", "云端 API（更快更准）"],
            index=0 if BASE.provider == "ollama" else 1,
        )
        settings.provider = "ollama" if provider_label.startswith("本地") else "openai"

        if settings.provider == "ollama":
            settings.model = st.text_input("模型名", BASE.model, help="必须和 ollama list 一致")
            settings.ollama_base_url = st.text_input("Ollama 地址", BASE.ollama_base_url)
            st.caption("💡 `ollama pull qwen2.5:7b` 可以换更强的模型")
        else:
            presets = {
                "DeepSeek": ("https://api.deepseek.com/v1", "deepseek-chat"),
                "OpenAI": ("https://api.openai.com/v1", "gpt-4o-mini"),
                "通义千问": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
                "自定义": (BASE.api_base, BASE.model),
            }
            preset = st.selectbox("服务商", list(presets))
            default_base, default_model = presets[preset]
            settings.api_base = st.text_input("API 地址", default_base)
            settings.model = st.text_input(
                "模型名", default_model,
                help="模型名必须和服务商后台完全一致，填错会导致所有 AI 步骤失败。\n"
                     "DeepSeek 可用：deepseek-chat、deepseek-reasoner、deepseek-flash",
            )
            settings.api_key = st.text_input(
                "API Key", value=BASE.api_key, type="password",
                help="只保存在本次会话，不会写进 .env（想永久保存就改 .env）",
            )
            if not settings.api_key:
                st.warning("需要填写 API Key 才能用云端模型")

        st.divider()
        st.subheader("研究规模")
        settings.rounds = st.slider("研究轮数", 1, 3, BASE.rounds,
                                    help="1 快 / 2 标准 / 3 深挖")
        settings.papers_per_round = st.slider("每轮精读篇数", 2, 8, BASE.papers_per_round)
        year_value = st.number_input(
            "年份下限（0 = 不限）", min_value=0, max_value=2030, value=BASE.year_from or 0,
        )
        if 0 < year_value < 1900 or year_value > datetime.now().year:
            st.caption(f"年份下限应在 1900–{datetime.now().year} 之间（0 = 不限），"
                       f"当前填的 {year_value} 已忽略。")
            year_value = 0
        settings.year_from = year_value or None
        settings.include_preprints = st.checkbox(
            "包含 arXiv 预印本（默认关；综述不建议引用预印本）",
            value=BASE.include_preprints,
            help="预印本没有经过正式同行评审，不能作为综述的主要引用来源；需要最新进展时再开，并在正文里标明。",
        )

        st.divider()
        st.subheader("质量")
        settings.verify_citations = st.checkbox(
            "引用核验（编号 + 数字回查，秒级，确定性）", value=BASE.verify_citations,
            help="检查 [n] 编号是否有效、正文里的数字能否在来源论文中找到",
        )
        settings.verify_semantic = st.checkbox(
            "语义抽检（可选，慢 1-2 分钟，仅供参考）", value=BASE.verify_semantic,
            help="让模型逐句判断结论能否被摘要支持。3B 模型判定噪声较大，不作为质量结论",
        )
        if settings.verify_semantic:
            settings.verify_max_sentences = st.slider(
                "抽检句数", 2, 12, BASE.verify_max_sentences,
            )

        st.divider()
        model_ok = settings.provider == "ollama" or bool(settings.api_key)
        sources = "Crossref + DOAJ" + (" + arXiv（预印本）" if settings.include_preprints else "")
        st.caption(
            f"当前：`{settings.provider}/{settings.model}`\n\n"
            f"搜索：{sources}"
        )
        if not model_ok:
            st.error("云端模型缺少 API Key")
    return settings


# --------------------------------------------------------------------------
# 运行辅助
# --------------------------------------------------------------------------
class LiveLog:
    """把运行日志实时显示在页面上。"""

    def __init__(self, placeholder, keep: int = 14):
        self.placeholder = placeholder
        self.lines: list[str] = []
        self.keep = keep

    def __call__(self, message: str = "") -> None:
        self.lines.append(message)
        self.placeholder.code("\n".join(self.lines[-self.keep:]), language="text")


def run_review(settings: Settings, topic: str) -> dict:
    """跑一次综述，返回合并后的状态。"""
    llm = ChatLLM(settings)
    status = st.status(
        "正在运行：规划检索式 → 检索 → 筛选 → 精读 → 合成 → 核验", expanded=True
    )
    log = LiveLog(st.empty())
    merged: dict = {}
    started = time.perf_counter()

    try:
        log(llm.ping())
        graph = build_graph(settings, llm, log)
        for event in graph.stream(
            {"topic": topic, "language": settings.language}, {"recursion_limit": 200}
        ):
            for _node, update in event.items():
                if isinstance(update, dict):
                    merged.update(update)
                    if update.get("draft"):
                        status.update(label="正在核验引用 ...")
    except Exception:
        status.update(label="运行中断（原因见下方错误提示）", state="error", expanded=False)
        raise

    merged["_elapsed"] = time.perf_counter() - started
    status.update(label=f"完成，用时 {merged['_elapsed']:.0f} 秒", state="complete", expanded=False)
    return merged


def metric_row(merged: dict) -> None:
    metrics = (merged.get("verification") or {}).get("metrics") or {}
    extracts = merged.get("extracts", [])
    cols = st.columns(4)
    cols[0].metric("精读文献", f"{len(extracts)} 篇")
    cols[1].metric("耗时", f"{(merged.get('_elapsed') or 0) / 60:.1f} 分钟")
    cols[2].metric("引用处数", metrics.get("citation_marks", "—"))
    rate = metrics.get("number_rate")
    cols[3].metric("数字可追溯率", f"{rate}%" if rate is not None else "—")


# --------------------------------------------------------------------------
# 标签页 1：文献综述
# --------------------------------------------------------------------------
def tab_review(settings: Settings) -> None:
    st.subheader("输入一个研究主题")
    st.caption(
        "建议写具体一点：`大语言模型在金融风控中的应用` 比 `国际金融` 好得多——"
        "越具体，检索式越准，综述越像样。"
    )
    col1, col2 = st.columns([4, 1])
    topic = col1.text_input(
        "研究主题", placeholder="例如：大语言模型在金融风控中的应用",
        label_visibility="collapsed",
    )
    st.caption(
        f"运行前确认：源 = Crossref + DOAJ"
        + (" + arXiv 预印本" if settings.include_preprints else "（不含 arXiv 预印本）")
        + f"；轮数 = {settings.rounds}；每轮精读 = {settings.papers_per_round} 篇；"
        f"年份下限 = {settings.year_from or '不限'}。"
        "检索式会在开始后由模型生成，所以这里先确认范围和规模。"
    )
    confirm = st.checkbox("我已确认主题和检索范围", value=False, key="review_confirm")
    run = col2.button("开始生成", type="primary", use_container_width=True)

    if run:
        if not topic.strip():
            st.error("请先输入研究主题")
        elif not confirm:
            st.error("先勾选「我已确认主题和检索范围」再开始。")
        elif settings.provider == "openai" and not settings.api_key:
            st.error("云端模型需要 API Key，请在左侧填写")
        else:
            st.session_state.pop("md_path", None)
            try:
                st.session_state["merged"] = run_review(settings, topic.strip())
                st.session_state["topic"] = topic.strip()
            except LLMError as exc:
                st.error(f"模型调用失败：{exc}")
            except Exception as exc:  # noqa: BLE001
                st.error(f"运行失败：{type(exc).__name__}: {exc}")

    merged = st.session_state.get("merged")
    if not merged:
        st.info("填好主题点「开始生成」。首次运行会下载/加载模型，可能慢一些。")
        return

    topic_saved = st.session_state.get("topic", "research")
    st.divider()
    metric_row(merged)

    with st.expander("工具轨迹：每一步看到了什么", expanded=False):
        queries = merged.get("all_queries") or []
        candidates = merged.get("candidates") or []
        selected = merged.get("selected") or []
        extracts = merged.get("extracts") or []
        metrics = (merged.get("verification") or {}).get("metrics") or {}
        st.markdown("**① 检索式**（模型规划，代码执行）")
        st.write("；".join(queries) if queries else "—")
        st.markdown("**② 候选 → 筛选 → 精读**")
        st.write(f"候选 {len(candidates)} 篇 → 筛选 {len(selected)} 篇 → 精读 {len(extracts)} 篇")
        st.markdown("**③ 引用核验**（程序检查）")
        st.write(
            f"带引用句子 {metrics.get('sentences_with_citation', '—')} 条；"
            f"引用标记 {metrics.get('valid_citation_marks', '—')} 个；"
            f"数字回查失败 {metrics.get('numbers_missing', '—')} 个；"
            f"未被引用参考文献 {metrics.get('uncited_reference_count', '—')} 篇。"
        )

    report = merged.get("report", "")
    md_path = st.session_state.get("md_path")
    if report and not md_path:
        # 结果落盘：output/ 目录，和命令行版本保持一致
        md_path = write_output(f"{topic_saved}-文献综述", report, sidecar={
            "topic": topic_saved,
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "model": f"{settings.provider}/{settings.model}",
            "settings": {
                "rounds": settings.rounds,
                "papers_per_round": settings.papers_per_round,
                "year_from": settings.year_from,
                "language": settings.language,
            },
            "queries": merged.get("all_queries", []),
            "gaps": merged.get("gaps", []),
            "papers": merged.get("extracts", []),
            "draft": merged.get("draft", ""),
            "verification": merged.get("verification", {}),
        })
        st.session_state["md_path"] = md_path

    if report:
        c1, c2 = st.columns(2)
        c1.download_button(
            "⬇️ 下载 Markdown", report, file_name=Path(md_path).name,
            mime="text/markdown", use_container_width=True,
        )
        c2.download_button(
            "⬇️ 下载 JSON（结构化数据）",
            json.dumps({
                "topic": topic_saved,
                "papers": merged.get("extracts", []),
                "verification": merged.get("verification", {}),
            }, ensure_ascii=False, indent=2),
            file_name=Path(md_path).with_suffix(".json").name,
            mime="application/json", use_container_width=True,
        )
        if md_path:
            st.caption(f"已保存到：`{md_path}`")

        audit = build_review_audit(
            report,
            merged.get("extracts", []),
            merged.get("verification", {}),
            {
                "topic": topic_saved,
                "model": f"{settings.provider}/{settings.model}",
                "queries": merged.get("all_queries", []),
            },
        )
        a1, a2 = st.columns(2)
        a1.download_button(
            "🧾 下载引用审计报告（Markdown）",
            render_audit_markdown(audit),
            file_name=f"{topic_saved}-引用审计.md",
            mime="text/markdown",
            use_container_width=True,
        )
        a2.download_button(
            "🧾 下载引用审计报告（HTML）",
            render_audit_html(audit),
            file_name=f"{topic_saved}-引用审计.html",
            mime="text/html",
            use_container_width=True,
        )
        st.caption(
            f"审计结论：**{audit['status']}** · "
            f"{len(audit.get('claims') or [])} 条带引用句子 · "
            f"{len(audit.get('issues') or [])} 个待复核项。"
            "证据为摘要/精读卡片摘录，不是全文页码；页码级证据见「论文对话」。"
        )

        mentor_lines = [
            "# 给导师看的综述初稿",
            "",
            f"- 主题：{topic_saved}",
            f"- 引用审计结论：{audit.get('status', '')}",
            f"- 待复核项：{len(audit.get('issues') or [])} 个",
            "",
            "> 说明：这是初稿 + 核对线索，不是最终论文；引用和数字需要你确认。",
            "",
            report,
            "",
            "## 待复核项",
        ]
        issues = audit.get("issues") or []
        if issues:
            for issue in issues[:30]:
                mentor_lines.append(
                    f"- [{issue.get('severity', '')}] {issue.get('type', '')}："
                    f"{issue.get('sentence', '')} {issue.get('detail', '')}".strip()
                )
        else:
            mentor_lines.append("- 无结构性待复核项；仍需人工核对结论和原文。")
        st.download_button(
            "下载给导师核对版（Markdown）",
            "\n".join(mentor_lines),
            file_name=f"{topic_saved}-给导师核对版.md",
            mime="text/markdown",
            use_container_width=True,
        )

        claims = audit.get("claims") or []
        if claims:
            st.markdown("---")
            st.subheader("逐条核对引用")
            st.caption("每条带引用的句子都需要你看一眼；勾选表示你已核对，未勾选的不会被标成已核对。")
            checked = []
            for idx, claim in enumerate(claims):
                sentence = (claim.get("sentence") or "").strip()
                flags = []
                if claim.get("missing_numbers"):
                    flags.append("数字待回原文")
                if claim.get("invalid_citations"):
                    flags.append("引用编号无效")
                suffix = (" · " + " · ".join(flags)) if flags else ""
                ok = st.checkbox(
                    f"{idx + 1}. {sentence[:110]}{suffix}",
                    key=f"audit_check_{topic_saved}_{idx}",
                )
                checked.append(bool(ok))
            done = sum(checked)
            st.progress(done / len(checked), text=f"已核对 {done}/{len(checked)} 条")
            if done < len(checked):
                st.caption("还有未核对条目。导出记录会保留每条的真实状态，不会替你标成已核对。")

            record_lines = [
                "# 人工核对记录",
                "",
                f"- 主题：{topic_saved}",
                f"- 审计结论：{audit.get('status', '')}",
                f"- 已核对：{done}/{len(checked)}",
                "",
                "| # | 句子 | 引用 | 状态 | 备注 |",
                "|---|---|---|---|---|",
            ]
            for idx, (claim, ok) in enumerate(zip(claims, checked), start=1):
                sentence = (claim.get("sentence") or "").replace("|", "\\|").strip()
                cites = "、".join(f"[{i}]" for i in (claim.get("citations") or [])) or "—"
                note = "；".join(
                    filter(None, [
                        "数字待回原文" if claim.get("missing_numbers") else "",
                        "引用编号无效" if claim.get("invalid_citations") else "",
                    ])
                ) or "—"
                record_lines.append(
                    f"| {idx} | {sentence[:160]} | {cites} | {'已核对' if ok else '待核对'} | {note} |"
                )
            record_lines += [
                "",
                "> 说明：这是人工核对记录，不是模型自评。未核对条目需要回原文确认。",
            ]
            st.download_button(
                "下载人工核对记录（Markdown）",
                "\n".join(record_lines),
                file_name=f"{topic_saved}-人工核对记录.md",
                mime="text/markdown",
                use_container_width=True,
            )

        st.markdown(report)


# --------------------------------------------------------------------------
# 标签页 2：单篇精读
# --------------------------------------------------------------------------
def tab_read(settings: Settings) -> None:
    st.subheader("读一篇论文")
    mode = st.radio(
        "输入方式", ["上传 PDF", "arXiv 链接/编号", "DOI"],
        horizontal=True,
    )

    target: Path | None = None
    arxiv_id = None
    doi = None

    if mode == "上传 PDF":
        uploaded = st.file_uploader("选择 PDF 文件（知网下载的中文 PDF 也可以）", type=["pdf"])
        if uploaded is not None:
            target = UPLOAD_DIR / uploaded.name
            target.write_bytes(uploaded.getbuffer())
            st.caption(f"已接收：{uploaded.name}（{uploaded.size / 1024:.0f} KB）")
    elif mode == "arXiv 链接/编号":
        text = st.text_input("arXiv 链接或编号", placeholder="https://arxiv.org/abs/2305.10601")
        arxiv_id = parse_arxiv_id(text) if text else None
        if text and not arxiv_id:
            st.warning("没识别出 arXiv 编号，格式例如 2305.10601")
    else:
        doi = st.text_input("DOI", placeholder="10.1038/nature12373")

    if st.button("开始精读", type="primary"):
        if not (target or arxiv_id or doi):
            st.warning("请先提供输入")
            return
        if settings.provider == "openai" and not settings.api_key:
            st.error("云端模型需要 API Key")
            return

        status = st.status("正在读取并精读 ...", expanded=True)
        log = LiveLog(status.empty())
        try:
            llm = ChatLLM(settings)
            log(llm.ping())
            if target:
                note = read_pdf_file(target, settings, llm, log)
                stem = f"精读-{target.stem}"
            elif arxiv_id:
                note = read_arxiv(arxiv_id, settings, llm, log)
                stem = f"精读-arXiv-{arxiv_id}"
            else:
                note = read_doi(doi, settings, llm, log)
                stem = f"精读-DOI-{doi[:20]}"
            path = write_output(stem, note)
            status.update(label=f"完成，已保存到 {path.name}", state="complete", expanded=False)
            st.session_state["note"] = note
            st.session_state["note_path"] = path
        except Exception as exc:  # noqa: BLE001
            status.update(label="失败", state="error")
            st.error(f"{type(exc).__name__}: {exc}")

    note = st.session_state.get("note")
    if note:
        st.divider()
        path = st.session_state.get("note_path")
        st.download_button(
            "⬇️ 下载精读笔记", note,
            file_name=Path(path).name if path else "精读笔记.md",
            mime="text/markdown",
        )
        st.markdown(note)


# --------------------------------------------------------------------------
# 标签页 3：论文对话
# --------------------------------------------------------------------------
def tab_chat(settings: Settings) -> None:
    st.subheader("和一篇论文对话")
    st.caption(
        "加载一篇 PDF / arXiv 论文，然后围绕它提问、让它提炼能写进论文的观点、"
        "或者直接在原文里找可引用的句子。"
    )

    # ---------------- 还没有加载论文 ----------------
    if "chat_index" not in st.session_state:
        col1, col2 = st.columns([3, 2])
        with col1:
            uploaded = st.file_uploader(
                "上传 PDF（知网下载的中文文献也可以）", type=["pdf"], key="chat_pdf"
            )
        with col2:
            target_text = st.text_input(
                "或者粘贴 arXiv 链接 / 编号 / DOI",
                placeholder="2305.10601 或 10.1038/nature12373",
                key="chat_target",
            )

        if st.button("加载论文", type="primary"):
            if settings.provider == "openai" and not settings.api_key:
                st.error("云端模型需要 API Key，请在左侧填写")
                return
            target = None
            if uploaded is not None:
                path = UPLOAD_DIR / uploaded.name
                path.write_bytes(uploaded.getbuffer())
                target = str(path)
            elif target_text.strip():
                target = target_text.strip()
            if not target:
                st.warning("请先上传 PDF 或粘贴 arXiv/DOI")
                return

            status = st.status("正在解析论文 ...", expanded=True)
            log = LiveLog(status.empty())
            try:
                llm = ChatLLM(settings)
                log(llm.ping())
                index, paper = load_paper(target, log)
                st.session_state["chat_index"] = index
                st.session_state["chat_paper"] = paper
                st.session_state["chat_history"] = []
                status.update(label=f"已加载：{paper.title[:50]}", state="complete",
                              expanded=False)
            except Exception as exc:  # noqa: BLE001
                status.update(label="加载失败", state="error")
                st.error(f"{type(exc).__name__}: {exc}")
                return
            if uploaded is not None:
                st.rerun()
        return

    # ---------------- 已加载论文 ----------------
    index = st.session_state["chat_index"]
    paper = st.session_state["chat_paper"]
    history: list[dict] = st.session_state.setdefault("chat_history", [])

    info_col, reset_col = st.columns([4, 1])
    info_col.markdown(f"**{paper.title}**")
    info_col.caption(
        f"{paper.year or 'n.d.'} ｜ {paper.venue or paper.url} ｜ "
        f"{index.meta.get('pages', '?')} 页 / {len(index.chunks)} 个片段"
    )
    if reset_col.button("换一篇", use_container_width=True):
        for key in ("chat_index", "chat_paper", "chat_history"):
            st.session_state.pop(key, None)
        st.rerun()

    mode = st.radio(
        "模式", ["论文问答", "提炼可写观点", "方法与局限"],
        horizontal=True, key="chat_mode",
    )

    # 历史消息
    for message in history:
        with st.chat_message("user" if message["role"] == "user" else "assistant"):
            st.markdown(message["content"])
            if message.get("sources"):
                pages = sorted({s["page"] for s in message["sources"]})
                with st.expander(f"依据片段（{len(message['sources'])} 段，页码 "
                                 f"{', '.join('p%d' % p for p in pages)}）"):
                    for i, source in enumerate(message["sources"], 1):
                        st.markdown(f"**[{i}] 第 {source['page']} 页** ｜ "
                                    f"相关度 {source['score']} ｜ {source.get('chars', '?')} 字")
                        st.text(" ".join((source.get("text") or source.get("snippet") or "").split()))

    placeholder = "输入问题，例如：这篇论文的方法是什么？它在什么数据上验证的？"
    if mode == "提炼可写观点":
        placeholder = "输入你关心的方向（可留空 = 提炼全文最值得写的观点），例如：主要贡献和适用边界"
    elif mode == "方法与局限":
        placeholder = "输入关注点（可留空），例如：实验设计是否足以支撑结论？"

    question = st.chat_input(placeholder)
    if question:
        if settings.provider == "openai" and not settings.api_key:
            st.error("云端模型需要 API Key")
            return
        history.append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        with st.chat_message("assistant"):
            with st.spinner("正在读论文 ..."):
                try:
                    llm = ChatLLM(settings)
                    chat = PaperChat(index, paper, settings, llm)
                    chat.history = history[:-1]
                    if mode == "提炼可写观点":
                        result = chat.claims(question)
                    elif mode == "方法与局限":
                        result = chat.critique(question)
                    else:
                        result = chat.ask(question)
                    answer = result["answer"]
                    st.markdown(answer)
                    if result.get("sources"):
                        st.caption("依据页码：" + ", ".join(
                            f"p{p}" for p in sorted({s["page"] for s in result["sources"]})))
                    if result.get("claims"):
                        audit = build_chat_audit(
                            paper, result,
                            {"model": f"{settings.provider}/{settings.model}"},
                        )
                        a1, a2 = st.columns(2)
                        a1.download_button(
                            "🧾 观点审计（Markdown）",
                            render_audit_markdown(audit),
                            file_name=f"{paper.title[:24]}-观点审计.md",
                            mime="text/markdown",
                            use_container_width=True,
                        )
                        a2.download_button(
                            "🧾 观点审计（HTML）",
                            render_audit_html(audit),
                            file_name=f"{paper.title[:24]}-观点审计.html",
                            mime="text/html",
                            use_container_width=True,
                        )
                        st.caption(
                            f"观点审计：{audit['status']} · "
                            f"{len(audit.get('claims') or [])} 条观点、"
                            f"{len(audit.get('issues') or [])} 条需人工核对。"
                        )
                except Exception as exc:  # noqa: BLE001
                    answer = f"出错：{type(exc).__name__}: {exc}"
                    st.error(answer)
                    result = {"sources": []}
        history.append({
            "role": "assistant", "content": answer, "sources": result.get("sources", []),
        })
        st.session_state["chat_history"] = history

    # 引文摘录 + 保存
    with st.expander("📌 原文引文摘录（确定性检索，可直接引用）"):
        keyword = st.text_input("关键词", placeholder="例如：Tree of Thoughts / 显著提升 / sample", key="chat_quote_kw")
        if keyword.strip():
            chat = PaperChat(index, paper, settings, ChatLLM(settings))
            hits = chat.quote(keyword)
            if not hits:
                st.info("原文里没有找到包含该关键词的句子。")
            for hit in hits:
                st.markdown(f"**[p{hit['page']}]** {hit['sentence']}")

    if history:
        c1, c2 = st.columns(2)
        transcript = "# 论文对话记录：" + paper.title + "\n\n" + "\n\n".join(
            f"## {'我' if m['role'] == 'user' else '助手'}\n\n{m['content']}" for m in history
        )
        c1.download_button(
            "⬇️ 下载对话记录", transcript,
            file_name=f"对话-{paper.title[:24]}.md", mime="text/markdown",
            use_container_width=True,
        )
        if c2.button("💾 保存到 output/", use_container_width=True):
            chat = PaperChat(index, paper, settings, ChatLLM(settings))
            chat.history = history
            path = chat.save(f"对话-{paper.title[:30]}")
            st.success(f"已保存：{path}")
    else:
        st.info("在下面输入框里问第一个问题吧。")


# --------------------------------------------------------------------------
# 标签页 4：文献库
# --------------------------------------------------------------------------
def tab_library(settings: Settings) -> None:
    st.subheader("多篇论文一起读")
    st.caption(
        "把几篇同主题论文放进一个文献库：可以跨文献提问、生成对比表、找它们共同没有解决的问题。"
    )

    if "library" not in st.session_state:
        col1, col2 = st.columns([3, 2])
        with col1:
            uploads = st.file_uploader(
                "上传多篇 PDF（可多选）", type=["pdf"], accept_multiple_files=True,
                key="lib_pdfs",
            )
        with col2:
            arxiv_text = st.text_area(
                "或者粘贴多个 arXiv 编号（每行一个）",
                placeholder="2305.10601\n2201.11903\n2203.11171",
                height=120, key="lib_arxiv",
            )
        if st.button("加载文献库", type="primary"):
            if settings.provider == "openai" and not settings.api_key:
                st.error("云端模型需要 API Key，请在左侧填写")
                return
            targets: list[tuple[str, str]] = [("pdf", p) for p in (uploads or [])]
            for line in (arxiv_text or "").splitlines():
                line = line.strip()
                if line:
                    targets.append(("arxiv", line))
            if not targets:
                st.warning("请上传 PDF 或填写 arXiv 编号")
                return

            status = st.status("正在建立文献库 ...", expanded=True)
            log = LiveLog(status.empty())
            try:
                llm = ChatLLM(settings)
                log(llm.ping())
                library = PaperLibrary(settings, llm, log)
                for kind, item in targets:
                    if kind == "pdf":
                        path = UPLOAD_DIR / item.name
                        path.write_bytes(item.getbuffer())
                        library.add_pdf(path)
                    else:
                        library.add_arxiv(item)
                if not library.entries:
                    raise RuntimeError("没有成功加载任何文献")
                st.session_state["library"] = library
                st.session_state["lib_history"] = []
                status.update(label=f"已加载 {len(library.entries)} 篇文献", state="complete",
                              expanded=False)
            except Exception as exc:  # noqa: BLE001
                status.update(label="加载失败", state="error")
                st.error(f"{type(exc).__name__}: {exc}")
                return
            st.rerun()
        return

    library: PaperLibrary = st.session_state["library"]
    history: list[dict] = st.session_state.setdefault("lib_history", [])

    info_col, reset_col = st.columns([4, 1])
    with info_col:
        st.markdown(f"**文献库：{len(library.entries)} 篇**")
        for entry in library.entries:
            st.caption(f"[{entry.number}] {entry.paper.title[:80]}")
    if reset_col.button("换一批", use_container_width=True):
        for key in ("library", "lib_history"):
            st.session_state.pop(key, None)
        st.rerun()

    mode = st.radio(
        "模式", ["跨文献问答", "对比表", "研究空白"],
        horizontal=True, key="lib_mode",
    )

    for message in history:
        with st.chat_message("user" if message["role"] == "user" else "assistant"):
            st.markdown(message["content"])
            sources = message.get("sources") or []
            if sources:
                with st.expander("依据片段"):
                    for source in sources:
                        if "page" in source:
                            st.caption(f"[{source['entry']}] p{source['page']} ｜ "
                                       f"相关度 {source.get('score', '-')}")
                            st.text((source.get("text") or "")[:300])
                        else:
                            st.caption(f"[{source.get('entry')}] {source.get('title', '')}")

    placeholder = {
        "跨文献问答": "例如：这几篇论文在方法上有什么不同？谁的结论最可靠？",
        "对比表": "输入关注点（可留空），例如：方法差异与实验结论",
        "研究空白": "输入关注点（可留空）",
    }[mode]
    question = st.chat_input(placeholder)
    if question is not None:
        if settings.provider == "openai" and not settings.api_key:
            st.error("云端模型需要 API Key")
            return
        label = question or "（未指定关注点）"
        history.append({"role": "user", "content": label})
        with st.chat_message("user"):
            st.markdown(label)
        with st.chat_message("assistant"):
            with st.spinner("正在读这几篇论文 ..."):
                try:
                    llm = ChatLLM(settings)
                    if mode == "对比表":
                        result = library.compare(question)
                    elif mode == "研究空白":
                        result = library.gaps()
                    else:
                        result = library.ask(question)
                    st.markdown(result["answer"])
                except Exception as exc:  # noqa: BLE001
                    result = {"answer": f"出错：{type(exc).__name__}: {exc}", "sources": []}
                    st.error(result["answer"])
        history.append({
            "role": "assistant", "content": result["answer"],
            "sources": result.get("sources", []),
        })
        st.session_state["lib_history"] = history

    if history:
        extra = "\n\n" + "\n\n".join(
            f"## {'我' if m['role'] == 'user' else '助手'}\n\n{m['content']}" for m in history
        )
        transcript = library.transcript(f"文献库（{len(library.entries)} 篇）") + extra
        c1, c2 = st.columns(2)
        c1.download_button(
            "⬇️ 下载记录", transcript, file_name="文献库记录.md",
            mime="text/markdown", use_container_width=True,
        )
        if c2.button("💾 保存到 output/", use_container_width=True):
            path = library.save(f"文献库-{len(library.entries)}篇", extra=extra)
            st.success(f"已保存：{path}")


# --------------------------------------------------------------------------
# 标签页 5：说明
# --------------------------------------------------------------------------
def tab_help() -> None:
    st.subheader("这个工具是怎么工作的")
    st.markdown(
        """
```text
主题 → 规划英文学术检索式 → Crossref/DOAJ/arXiv 检索 → 去重打分
     → 模型筛选 → 逐篇结构化精读 → 反思补检 → 中文综述 → 引用核验
```

**四个标签页：**

| 标签页 | 用途 |
|---|---|
| 📝 文献综述 | 一个主题 → 中文综述初稿（带 DOI 与引用核验） |
| 📄 单篇精读 | 一篇论文 → 结构化中文笔记 |
| 💬 论文对话 | 一篇论文 → 连续提问 / 提炼可写观点 / 摘原文引文 |
| ❓ 说明 | 就是这一页 |

**论文对话为什么靠谱：**
1. 检索是**词法检索（BM25）**，同样的提问永远命中同样的原文片段，可复现；
2. 中文提问会自动转成英文关键词再检索（否则英文论文搜不到）；
3. 回答必须标 [p页码]，片段里没有的内容会明说"没有提到"；
4. 「提炼观点」时的原文依据由**程序从句子编号回填**，不是模型复述，避免"假装引用"；
5. 每条回答下方的「依据片段」展开就是**完整原文**；问句里加「详细」会得到更长的回答。
        """
    )
    st.subheader("常见问题")
    st.markdown(
        """
| 现象 | 解决 |
|---|---|
| 连不上 Ollama | 开始菜单打开 Ollama，托盘出现羊驼图标 |
| 模型不存在 | `ollama pull qwen2.5:3b`（或你在左侧填的名字） |
| 太慢 | 把轮数调到 1、篇数调到 2-3；或切云端 API |
| 综述里数字不对 | 3B 模型会记错数字，**所有数字必须回原文核对** |
| 想读知网中文文献 | 下载 PDF → 「单篇精读」标签页上传；批量用命令行 `--folder papers` |
        """
    )
    st.subheader("当前配置")
    st.code(BASE.describe(), language="text")


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
settings = sidebar()

tabs = st.tabs(["📝 文献综述", "📄 单篇精读", "💬 论文对话", "📚 文献库", "❓ 说明"])
with tabs[0]:
    tab_review(settings)
with tabs[1]:
    tab_read(settings)
with tabs[2]:
    tab_chat(settings)
with tabs[3]:
    tab_library(settings)
with tabs[4]:
    tab_help()
