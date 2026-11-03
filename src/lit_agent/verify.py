"""引用核验：把"模型可能编引用 / 编数字"变成可复现的指标。

三层设计（按可靠性从高到低）

1. **结构核验**（确定性，零成本，最可靠）
   * 正文 ``[n]`` 是否都能对应到参考文献第 n 条；
   * 参考文献有没有"挂着但正文没用到"的；
   * 引用密度（每千字多少处引用）。

2. **数字回查**（确定性，零成本，专抓幻觉）
   * 把带引用句子里的数字（百分比、小数、样本量…）抽出来，
     回到所引论文的摘要 / 精读卡片里找；
   * 找不到 → 标记"数字未在来源中找到"，这是**高置信度的幻觉信号**；
   * 汇总成"数字回查通过率"。

3. **语义抽检**（可选，调用模型，仅供参考）
   * 让模型判断"这句话能否被摘要/卡片支持"；
   * 小模型（3B）判定噪声大，只作为参考，不进主指标。

为什么这么设计：3B 模型给自己打分不稳定（同一份稿子两次判定能差 6 个百分点），
所以把**可靠性高的确定性检查**做成主指标，模型判定降级为参考。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .llm import ChatLLM, LLMError

CITE_RE = re.compile(r"\[(\d+(?:\s*[,，]\s*\d+)*)\]")
SENT_SPLIT_RE = re.compile(r"(?<=[。！？；!?;])\s*|\n+")
NUM_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*(%|％|倍|万|亿)?")

VERDICT_SUPPORT = "支持"
VERDICT_PARTIAL = "部分支持"
VERDICT_UNSUPPORTED = "不支持"
VERDICT_UNCOVERED = "摘要未涉及"
VERDICT_UNVERIFIED = "未核验"
_VERDICT_SET = {VERDICT_SUPPORT, VERDICT_PARTIAL, VERDICT_UNSUPPORTED, VERDICT_UNCOVERED}

SEMANTIC_SYSTEM_ZH = """你是严格的引用核验员。给你一句话，以及它引用论文的原文摘要和精读卡片，
判断这句话是否有依据。

只能选一个：
- "支持"：摘要或卡片里有明确对应的事实、方法或数字；
- "部分支持"：方向一致，但句子里的具体细节或数字找不到；
- "不支持"：与给出的材料矛盾；
- "摘要未涉及"：材料里没提这件事（概括性论断常见）。

必须为给定每一个编号都输出判定。只输出 JSON：
{"verdicts": [{"index": 2, "verdict": "支持", "reason": "20字以内理由"}]}"""


@dataclass
class CitationItem:
    """一个带引用的句子及其核验结果。"""

    sentence: str
    indices: list[int] = field(default_factory=list)
    numbers: list[str] = field(default_factory=list)
    missing_numbers: list[str] = field(default_factory=list)
    semantic: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------
# 基础工具
# --------------------------------------------------------------------------
def _norm(text: str) -> str:
    return (
        (text or "")
        .replace(",", "")
        .replace("，", "")
        .replace("％", "%")
        .lower()
    )


def split_cited_sentences(draft: str) -> list[tuple[str, list[int]]]:
    """切成句子，只保留带 [n] 引用的。"""
    results: list[tuple[str, list[int]]] = []
    seen: set[str] = set()
    for raw in SENT_SPLIT_RE.split(draft or ""):
        sentence = (raw or "").strip().strip("-*# ")
        if not sentence or len(sentence) < 8:
            continue
        indices: list[int] = []
        for match in CITE_RE.finditer(sentence):
            for piece in re.split(r"[,，]", match.group(1)):
                if piece.strip().isdigit():
                    indices.append(int(piece.strip()))
        if not indices or sentence in seen:
            continue
        seen.add(sentence)
        results.append((sentence, sorted(set(indices))))
    return results


def extract_numbers(text: str) -> list[str]:
    """抽出句子里的"论据型"数字。

    只保留：带单位（%/倍/万/亿）、小数、或 ≥100 的数（样本量等）。
    这样能避开"三种方法""两个步骤"这类普通计数和引用编号 [1] 的干扰，
    让"数字回查"专注于最容易被编造的统计量。
    """
    # 先去掉 [n] 引用标记，否则编号会被当成数字
    text = CITE_RE.sub("", text or "")
    numbers: list[str] = []
    for match in NUM_RE.finditer(text):
        raw = match.group(1).replace(",", "")
        unit = match.group(2) or ""
        try:
            value = float(raw)
        except ValueError:
            continue
        if not unit and 1900 <= value <= 2035:  # 年份不算论据
            continue
        is_decimal = "." in raw
        if not unit and not is_decimal and value < 100:
            continue  # 普通小计数不算
        token = f"{raw}{unit}"
        if token not in numbers:
            numbers.append(token)
    return numbers


def _evidence_text(extract: dict) -> str:
    """论文的可用证据文本：摘要 + 精读卡片。"""
    parts = [
        extract.get("abstract", ""),
        extract.get("one_liner", ""),
        extract.get("method", ""),
        extract.get("data", ""),
        extract.get("findings", ""),
    ]
    return _norm(" ".join(p for p in parts if p))


def _number_found(number: str, evidence: str) -> bool:
    """数字（含单位）是否出现在证据文本里。"""
    if number in evidence:
        return True
    match = re.match(r"^(\d+(?:\.\d+)?)(%|％|倍|万|亿)?$", number)
    if not match:
        return False
    raw, unit = match.group(1), match.group(2) or ""
    if raw in evidence:  # 3.08% ↔ 3.08
        return True
    # 中文单位换算：120万 ↔ 1200000，1.2亿 ↔ 120000000
    if unit in {"万", "亿"}:
        scale = 10_000 if unit == "万" else 100_000_000
        try:
            expanded = f"{int(float(raw) * scale)}"
        except (ValueError, OverflowError):
            return False
        if expanded in evidence:
            return True
    return False


# --------------------------------------------------------------------------
# 主流程
# --------------------------------------------------------------------------
def verify_citations(
    draft: str,
    extracts: list[dict],
    llm: ChatLLM | None = None,
    log=print,
    semantic_sentences: int = 0,
    language: str = "zh",
) -> dict[str, Any]:
    """核验正文引用。``semantic_sentences=0`` 表示不做模型语义抽检。"""
    by_index = {int(e["index"]): e for e in extracts if e.get("index") is not None}
    if not draft or not by_index:
        return {"ok": False, "reason": "没有正文或参考文献", "metrics": {}}

    sentences = split_cited_sentences(draft)
    cited_set = {i for _, idxs in sentences for i in idxs}
    total_marks = sum(len(idxs) for _, idxs in sentences)
    valid_marks = sum(1 for _, idxs in sentences for i in idxs if i in by_index)
    uncited = sorted(i for i in by_index if i not in cited_set)

    metrics: dict[str, Any] = {
        "sentences_with_citation": len(sentences),
        "citation_marks": total_marks,
        "valid_citation_marks": valid_marks,
        "invalid_citation_marks": total_marks - valid_marks,
        "reference_count": len(by_index),
        "uncited_references": uncited,
        "uncited_reference_count": len(uncited),
        "citation_density": round(total_marks / max(1, len(draft)) * 1000, 2),
        "numbers_checked": 0,
        "numbers_found": 0,
        "numbers_missing": 0,
        "number_rate": None,
        "semantic_checked": 0,
        "support": 0,
        "partial": 0,
        "unsupported": 0,
        "not_covered": 0,
        "unverified": 0,
        "semantic_rate": None,
    }
    items: list[CitationItem] = []

    # ---------------- 1 + 2：结构 & 数字回查（确定性） ----------------
    for sentence, idxs in sentences:
        targets = [by_index[i] for i in sorted(set(idxs)) if i in by_index]
        item = CitationItem(sentence=sentence, indices=sorted(set(idxs)))
        numbers = extract_numbers(sentence)
        if numbers and targets:
            evidence = " ".join(_evidence_text(t) for t in targets)
            for number in numbers:
                metrics["numbers_checked"] += 1
                item.numbers.append(number)
                if _number_found(number, evidence):
                    metrics["numbers_found"] += 1
                else:
                    metrics["numbers_missing"] += 1
                    item.missing_numbers.append(number)
        items.append(item)

    checked = metrics["numbers_checked"]
    if checked:
        metrics["number_rate"] = round(metrics["numbers_found"] / checked * 100, 1)

    # ---------------- 3：语义抽检（可选，仅供参考） ----------------
    if semantic_sentences > 0 and llm is not None and sentences:
        to_check = _pick_evenly(sentences, semantic_sentences)
        system = SEMANTIC_SYSTEM_ZH
        log(f"   语义抽检 {len(to_check)} 句（小模型判定，仅供参考）...")
        for sentence, idxs in to_check:
            targets = [by_index[i] for i in sorted(set(idxs)) if i in by_index]
            if not targets:
                continue
            verdicts = _ask_verdicts(llm, system, sentence, targets, log)
            missing = [t for t in targets if t["index"] not in verdicts]
            if missing:
                verdicts.update(
                    _ask_verdicts(llm, system, sentence, missing, log,
                                  hint="你上次漏掉了这些编号，这次必须回答。")
                )
            for t in targets:
                verdicts.setdefault(t["index"], {
                    "index": t["index"], "verdict": VERDICT_UNVERIFIED,
                    "reason": "模型未给出判定",
                })
            # 合并进对应 item
            item = next((i for i in items if i.sentence == sentence), None)
            if item is None:
                item = CitationItem(sentence=sentence, indices=sorted(set(idxs)))
                items.append(item)
            item.semantic = list(verdicts.values())
            for v in verdicts.values():
                if v["verdict"] == VERDICT_SUPPORT:
                    metrics["support"] += 1
                elif v["verdict"] == VERDICT_PARTIAL:
                    metrics["partial"] += 1
                elif v["verdict"] == VERDICT_UNSUPPORTED:
                    metrics["unsupported"] += 1
                elif v["verdict"] == VERDICT_UNVERIFIED:
                    metrics["unverified"] += 1
                else:
                    metrics["not_covered"] += 1
            metrics["semantic_checked"] += 1

        judged = (metrics["support"] + metrics["partial"]
                  + metrics["unsupported"] + metrics["not_covered"])
        if judged:
            metrics["semantic_rate"] = round(
                (metrics["support"] + metrics["partial"]) / judged * 100, 1
            )

    return {
        "ok": True,
        "metrics": metrics,
        "items": [
            {
                "sentence": i.sentence,
                "indices": i.indices,
                "numbers": i.numbers,
                "missing_numbers": i.missing_numbers,
                "semantic": i.semantic,
            }
            for i in items
        ],
    }


def _pick_evenly(
    items: list[tuple[str, list[int]]], limit: int
) -> list[tuple[str, list[int]]]:
    if len(items) <= limit:
        return items
    step = max(1, len(items) / limit)
    picked: list[tuple[str, list[int]]] = []
    seen: set[str] = set()
    for i in range(limit):
        item = items[min(len(items) - 1, int(i * step))]
        if item[0] not in seen:
            seen.add(item[0])
            picked.append(item)
    return picked


def _ask_verdicts(
    llm: ChatLLM,
    system: str,
    sentence: str,
    targets: list[dict],
    log,
    hint: str = "",
) -> dict[int, dict[str, Any]]:
    evidence = "\n\n".join(
        f"[{t['index']}] {t.get('title', '')[:100]}\n"
        f"摘要：{(t.get('abstract') or '')[:500]}\n"
        f"卡片：{t.get('one_liner', '')}；{t.get('findings', '')[:120]}"
        for t in targets
    )
    indices = ", ".join(str(t["index"]) for t in targets)
    user = (
        f"句子：{sentence}\n\n引用论文：\n{evidence}\n\n"
        f"请为编号 {indices} 各输出一条判定，只输出 JSON。{hint}"
    )
    allowed = {t["index"] for t in targets}
    found: dict[int, dict[str, Any]] = {}
    try:
        data = llm.chat_json(system, user, max_tokens=320, temperature=0)
    except (LLMError, ValueError) as exc:
        log(f"   ! 语义抽检失败（按未核验处理）：{exc}")
        return found

    for raw in data.get("verdicts", []) or []:
        try:
            index = int(raw.get("index"))
        except (TypeError, ValueError):
            continue
        if index not in allowed:
            continue
        verdict = str(raw.get("verdict", "")).strip()
        reason = str(raw.get("reason", ""))[:60]
        if verdict not in _VERDICT_SET:
            verdict = VERDICT_UNCOVERED
        if verdict == VERDICT_UNSUPPORTED and any(
            key in reason for key in ("未涉及", "未提及", "没有提", "没提")
        ):
            verdict = VERDICT_UNCOVERED
        found[index] = {"index": index, "verdict": verdict, "reason": reason}
    return found


# --------------------------------------------------------------------------
# 渲染
# --------------------------------------------------------------------------
def render_section(result: dict[str, Any], extracts: list[dict]) -> str:
    metrics = result.get("metrics") or {}
    if not result.get("ok") or not metrics:
        return "## 五、引用核验\n\n（未执行引用核验）\n"

    lines = ["## 五、引用核验（自动）", ""]

    marks = metrics.get("citation_marks", 0)
    invalid = metrics.get("invalid_citation_marks", 0)
    lines.append(
        f"- **编号有效性**：正文 {marks} 处引用，有效 {metrics.get('valid_citation_marks', 0)} 处"
        + (f"，**无效 {invalid} 处（引用了不存在的参考文献编号）**" if invalid else "，全部有效")
    )
    total_refs = metrics.get("reference_count", 0)
    uncited = metrics.get("uncited_references") or []
    lines.append(
        f"- **引用覆盖**：{total_refs} 篇参考文献中 {total_refs - len(uncited)} 篇被正文引用"
        + (f"（未被引用：{'、'.join(f'[{i}]' for i in uncited)}）" if uncited else "")
    )

    rate = metrics.get("number_rate")
    if metrics.get("numbers_checked"):
        lines.append(
            f"- **数字回查**：{metrics['numbers_checked']} 个数字中 "
            f"{metrics['numbers_found']} 个能在所引论文中找到依据"
            + (f"，**{metrics['numbers_missing']} 个未找到**" if metrics["numbers_missing"] else "")
        )
        if rate is not None:
            lines.append(f"- **数字可追溯率**：**{rate}%**")
    else:
        lines.append("- **数字回查**：正文带引用的句子里没有可核对的数字")

    if metrics.get("semantic_checked"):
        lines.append(
            f"- **语义抽检（参考）**：{metrics['semantic_checked']} 句 → "
            f"支持 {metrics.get('support', 0)}、部分支持 {metrics.get('partial', 0)}、"
            f"不支持 {metrics.get('unsupported', 0)}、摘要未涉及 {metrics.get('not_covered', 0)}"
            + (f"、未核验 {metrics['unverified']}" if metrics.get("unverified") else "")
        )
        if metrics.get("semantic_rate") is not None:
            lines.append(
                f"  - 语义支撑率 {metrics['semantic_rate']}%"
                "（3B 小模型判定噪声较大，仅作参考，不作为质量结论）"
            )

    # 需人工核对清单：优先数字问题
    number_issues = [i for i in result.get("items", []) if i.get("missing_numbers")]
    semantic_issues = [
        (i, [c for c in i.get("semantic", [])
             if c.get("verdict") in {VERDICT_UNSUPPORTED, VERDICT_UNCOVERED, VERDICT_UNVERIFIED}])
        for i in result.get("items", [])
    ]
    semantic_issues = [(i, bad) for i, bad in semantic_issues if bad]

    if number_issues or semantic_issues:
        lines += ["", "**需人工核对：**", ""]
        for item in number_issues[:8]:
            cited = "、".join(f"[{i}]" for i in item.get("indices", []))
            numbers = "、".join(item["missing_numbers"])
            short = item["sentence"] if len(item["sentence"]) <= 80 else item["sentence"][:80] + "…"
            lines.append(f"- **数字 {numbers} 未在{cited}中找到** ← 「{short}」")
        for item, bad in semantic_issues[:5]:
            marks_text = "、".join(f"[{c['index']}] {c['verdict']}" for c in bad)
            short = item["sentence"] if len(item["sentence"]) <= 80 else item["sentence"][:80] + "…"
            lines.append(f"- {marks_text} ← 「{short}」")
    elif metrics.get("citation_marks"):
        lines += ["", "本次检查未发现可疑引用或数字。"]

    lines.append("")
    lines.append(
        "> 说明：编号与数字核验是**确定性检查**（不依赖模型判断）；"
        "数字回查找不到，可能是综述改写时写错了数字，也可能是原文用了不同写法，"
        "都需要回到原文确认。"
    )
    return "\n".join(lines) + "\n"
