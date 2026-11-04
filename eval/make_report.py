"""生成评测总览页 eval/report.html（面试演示用）：把检索 A/B、模型选型、三层评测汇总成一页，数据内嵌。

用法：
    .venv\\Scripts\\python.exe eval\\make_report.py
数据源：eval/results.json / v2.json / MODEL_COMPARISON.json / human_eval_results.json（可选）
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "eval"
OUT = OUT_DIR / "report.html"


def load(name: str):
    try:
        return json.loads((OUT_DIR / name).read_text(encoding="utf-8"))
    except Exception:
        return None


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def bar_row(label: str, value: float, max_v: float, color: str, extra: str = "") -> str:
    w = max(3, min(100, value / max_v * 100)) if max_v else 3
    return (f'<div class="brow"><span class="bl">{esc(label)}</span>'
            f'<div class="btrack"><div class="bfill" style="width:{w:.0f}%;background:{color}"></div></div>'
            f'<span class="bv">{value}{extra}</span></div>')


def main() -> int:
    v2 = load("v2.json") or {}
    model = load("MODEL_COMPARISON.json") or {}
    human = load("human_eval_results.json")

    conds = (v2.get("summary") or {}).get("conditions", {})
    rows = v2.get("rows") or []

    cond_meta = [
        ("b0", "B0 naive（中文直检）", "#b8b0a4"),
        ("b1", "B1 检索式", "#9cb3f7"),
        ("b2", "B2 hard（v1）", "#ffb38a"),
        ("b3", "B3 soft（v2 线上）", "#ff7a45"),
    ]
    max_p = max([conds.get(c, {}).get("precision@10", 0) for c, _, _ in cond_meta] + [10])

    bars = "".join(bar_row(label, conds.get(key, {}).get("precision@10", 0), max_p, color,
                           f"%（±{conds.get(key, {}).get('precision@10_mean_range', 0)}pp）" if conds.get(key, {}).get("precision@10_mean_range") else "%")
                   for key, label, color in cond_meta)

    per_topic_rows = "".join(
        f"<tr><td>{esc(r['topic'])}</td>"
        + "".join(f"<td>{r.get(key, {}).get('precision@10', '—')}%</td>" for key, _, _ in cond_meta)
        + "</tr>"
        for r in rows
    )

    # 模型对比
    model_rows_html = ""
    model_bars = ""
    try:
        entries = model.get("models") or model.get("rows") or []
        if isinstance(model, dict) and not entries:
            # 兼容 {model_name: {...}} 结构
            entries = [{"name": k, **v} for k, v in model.items() if isinstance(v, dict)]
        for m in entries:
            name = m.get("name") or m.get("model") or "?"
            rate = m.get("field_rate") or m.get("completeness") or m.get("field_completeness")
            secs = m.get("elapsed_s") or m.get("seconds") or m.get("time_s")
            if rate is not None:
                model_bars += bar_row(f"{name} 字段完整率", float(rate), 100, "#12a184", "%")
            if secs is not None:
                model_bars += bar_row(f"{name} 耗时", float(secs), 600, "#5b7cfa", "s")
            model_rows_html += f"<tr><td>{esc(name)}</td><td>{rate if rate is not None else '—'}</td><td>{secs if secs is not None else '—'}</td></tr>"
    except Exception:
        pass

    # 三层评测
    if human:
        rv = human.get("rule_vs_human", {})
        lb = human.get("llm_vs_human", {})
        gp = human.get("b1_vs_b3_golden", {})
        hp = human.get("human_precision", {})
        section3 = f"""
    <div class="grid3">
      <div class="kcard"><div class="kv">{rv.get('kappa', '—')}</div><div class="kk">规则口径 vs 人工 κ（{rv.get('agreement_pct')}% 一致）</div></div>
      <div class="kcard"><div class="kv">{lb.get('kappa_vs_human', '—')}</div><div class="kk">7B 模型打分 vs 人工 κ（n={lb.get('n', '—')}）</div></div>
      <div class="kcard"><div class="kv">{hp.get('b3', {}).get('wide_mean_p10', '—')}%</div><div class="kk">金标准口径 B3(soft) P@10</div></div>
    </div>
    <p class="insight">混淆矩阵：TP {rv.get('confusion', {}).get('tp')} · FP {rv.get('confusion', {}).get('fp')} · FN {rv.get('confusion', {}).get('fn')} · TN {rv.get('confusion', {}).get('tn')}
    —— 规则口径精确率 {rv.get('precision')}%、召回 {rv.get('recall')}%：代理标注的偏差被量化了。</p>
    {f'<p class="insight">金标准配对（{gp.get("n_topics")} 主题全覆盖）：B1 {gp.get("b1_mean")}% vs B3 {gp.get("b3_mean")}%；B3 更优 {gp.get("b3_wins")} 题 / B1 更优 {gp.get("b1_wins")} 题。</p>' if gp.get('rows') else ''}
    <table><tr><th>主题</th><th>B1</th><th>B3(soft)</th><th>差值</th></tr>
    {''.join(f"<tr><td>{esc(x['topic'])}</td><td>{x['b1']}%</td><td>{x['b3']}%</td><td>{x['diff_b3_minus_b1']:+}</td></tr>" for x in gp.get('rows', []))}</table>
    """
    else:
        section3 = '<p class="insight">人工标注尚未完成：运行 lit_labeler 标注 → eval/human_eval.py → 重新生成本页。</p>'

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>文献 Agent · 评测总览</title>
<style>
  :root{{--ink:#1f1b16;--muted:#7a7268;--bg:#f7f2ea;--card:#fff;--line:#eadfd0;--orange:#ff7a45;
    --orange-soft:#ffe8dc;--blue:#5b7cfa;--green:#12a184;--green-soft:#dcf5ee;--red:#d64545;--red-soft:#fde5e5}}
  *{{box-sizing:border-box}}
  body{{margin:0;background:var(--bg);color:var(--ink);padding:34px 18px 46px;
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}
  .wrap{{max-width:860px;margin:0 auto}}
  h1{{font-size:28px;margin:0 0 6px}} h2{{font-size:19px;margin:34px 0 10px}}
  .sub{{color:var(--muted);font-size:14px;line-height:1.85;margin:0 0 20px}}
  .card{{background:var(--card);border-radius:16px;padding:20px 22px;box-shadow:0 10px 26px rgba(60,42,20,.10);margin-bottom:18px}}
  .brow{{display:grid;grid-template-columns:170px 1fr 120px;gap:10px;align-items:center;font-size:13px;margin-bottom:8px}}
  .bl{{color:var(--muted);font-weight:600}} .btrack{{height:14px;background:#f1e8dc;border-radius:99px;overflow:hidden}}
  .bfill{{height:100%;border-radius:99px}} .bv{{font-weight:800;font-size:13.5px}}
  table{{width:100%;border-collapse:collapse;font-size:12.8px;margin-top:10px}}
  th,td{{border:1px solid var(--line);padding:6px 9px;text-align:left}} th{{background:#faf6ef}}
  .insight{{font-size:13px;line-height:1.9;background:#faf6ef;border-radius:10px;padding:10px 14px}}
  .warn{{background:var(--red-soft)}}
  .grid3{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:8px 0}}
  .kcard{{background:#faf6ef;border-radius:12px;padding:14px;text-align:center}}
  .kv{{font-size:26px;font-weight:800;color:var(--orange)}} .kk{{font-size:11.8px;color:var(--muted);margin-top:4px;line-height:1.6}}
  footer{{text-align:center;color:#a89b8a;font-size:12.3px;margin-top:30px;line-height:2}}
</style>
</head>
<body><div class="wrap">
  <h1>📊 文献 Agent · 评测总览</h1>
  <p class="sub">检索层 A/B（10 主题 × 4 策略 × 重复实验）+ 模型选型实测 + <b>三层评测体系</b>（规则 → 模型打分 → 人工金标准）。
  所有数字可复现：<code>.venv\\Scripts\\python.exe eval\\run_eval.py --repeat 2</code> 等脚本都在仓库 eval/ 目录。</p>

  <h2>① 检索层 A/B：Precision@10（10 主题均值）</h2>
  <div class="card">{bars}
    <p class="insight"><b>怎么读</b>：B0 → B1（中文主题 → 学术英文检索式 + 多源检索）是唯一稳健的结论——数量级提升，10 主题里 9 个提升；
    B1 → B2 的 +6pp 落在 ±11-14pp 的重复噪声内，<b>不宣称显著</b>；B2 → B3 的差异同样在噪声内，
    但评测本身有<b>构念效度问题</b>（判定与机制同源）→ 所以下面用人工金标准重新回答。</p>
  </div>

  <h2>② 逐主题明细（规则口径）</h2>
  <div class="card"><table><tr><th>主题</th>{''.join(f'<th>{esc(label.split('（')[0])}</th>' for _, label, _ in cond_meta)}</tr>{per_topic_rows}</table>
  <p class="insight">中文政策类主题（如「数据要素市场化」）偏低是真实的检索边界——英文文献少，写进产品说明而不是隐藏。</p></div>

  <h2>③ 模型选型实测（本地 CPU）</h2>
  <div class="card">{model_bars or '<p class="insight">数据见 eval/MODEL_COMPARISON.md</p>'}
    <p class="insight">3B vs 7B：字段完整率 80% → 100%，耗时 2.8×——据此设计<b>本地/云端三档切换</b>，而不是拍脑袋选型。</p></div>

  <h2>④ 三层评测体系（本次新增：人工金标准）</h2>
  <div class="card">{section3}</div>

  <h2>⑤ 诚实口径</h2>
  <div class="card"><p class="insight">① 规则口径绝对值不可与其他系统比较，只做同条件对比；② 样本 10 主题 + 重复噪声 ±11-14pp，只对数量级差异下结论；
  ③ 人工标注为单人盲标（防锚定），2 人交叉标注是下一步；④ AI 读综述质量不做自动评测，用引用核验 + 人工抽查兜底。</p></div>

  <footer>eval/report.html · 数据由 make_report.py 从 eval/*.json 生成 · 每个数字都能在仓库里复现</footer>
</div></body></html>"""
    OUT.write_text(html, encoding="utf-8")
    print(f"已生成 {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
