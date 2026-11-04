"""生成人工标注工具 lit_labeler/index.html：候选池 JSON 内嵌，双击即用、盲标、本地保存、一键导出。

用法：先跑 dump_pool.py 生成 eval/human_eval_pool.json，然后
    .venv\\Scripts\\python.exe eval\\make_labeler.py
输出：lit_labeler/index.html（双击即用）
"""
from __future__ import annotations

import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
POOL = ROOT / "eval" / "human_eval_pool.json"
OUT = ROOT / "lit_labeler" / "index.html"

TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>文献相关性标注台 · lit_labeler</title>
<style>
  :root{
    --ink:#1f1b16; --muted:#7a7268; --bg:#f7f2ea; --card:#fff; --line:#eadfd0;
    --orange:#ff7a45; --orange-soft:#ffe8dc;
    --green:#12a184; --green-soft:#dcf5ee;
    --blue:#5b7cfa; --blue-soft:#e4e9ff;
    --red:#d64545; --red-soft:#fde5e5;
    --radius:18px; --shadow:0 10px 26px rgba(60,42,20,.10);
  }
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);
    font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif;
    min-height:100vh;padding:28px 16px 40px}
  .wrap{max-width:780px;margin:0 auto}
  h1{font-size:24px;margin:0 0 4px}
  .sub{color:var(--muted);font-size:13.5px;line-height:1.8;margin:0 0 14px}
  .bar{height:10px;background:#e8dfd2;border-radius:99px;overflow:hidden;margin-bottom:6px}
  .bar-fill{height:100%;background:linear-gradient(90deg,#ffb38a,var(--orange));border-radius:99px;transition:width .25s}
  .meta{font-size:12.5px;color:var(--muted);display:flex;gap:14px;flex-wrap:wrap;margin-bottom:16px}
  .meta b{color:var(--ink)}
  .card{background:var(--card);border-radius:var(--radius);padding:22px 24px;box-shadow:var(--shadow)}
  .topic-tag{display:inline-block;background:var(--blue-soft);color:#3b5bdb;border-radius:999px;padding:3px 12px;font-size:12px;font-weight:700;margin-bottom:10px}
  .title{font-size:17.5px;font-weight:800;line-height:1.55;margin-bottom:8px}
  .abs{font-size:13.5px;color:#4a443c;line-height:1.95;max-height:300px;overflow:auto;background:#faf6ef;border-radius:10px;padding:12px 14px}
  .meta-line{font-size:12px;color:var(--muted);margin:8px 0 14px}
  .keys{display:flex;gap:10px;margin-top:14px}
  .keys button{flex:1;border:2px solid var(--line);background:#fff;border-radius:12px;padding:14px 8px;font-size:14px;font-weight:800;cursor:pointer}
  .keys button small{display:block;font-size:11px;color:var(--muted);font-weight:600;margin-top:3px}
  .keys button:hover{transform:translateY(-1px)}
  .k1:hover{border-color:var(--red)} .k2:hover{border-color:var(--orange)} .k3:hover{border-color:var(--green)}
  .k1{color:var(--red)} .k2{color:var(--orange)} .k3{color:var(--green)}
  .row2{display:flex;gap:10px;margin-top:10px}
  .row2 button{flex:1;border:1.2px solid var(--line);background:#fff;border-radius:10px;padding:8px;font-size:12.5px;font-weight:700;color:var(--muted)}
  .flash{position:fixed;left:0;right:0;top:0;bottom:0;pointer-events:none;opacity:0;transition:opacity .18s}
  .flash.go{opacity:.75}
  .flash.f3{background:var(--green-soft)} .flash.f2{background:var(--orange-soft)} .flash.f1{background:var(--red-soft)}
  .done-box{display:none;text-align:center;padding:30px 20px}
  .done-box h2{margin:6px 0 14px}
  .done-stats{display:flex;gap:12px;justify-content:center;flex-wrap:wrap;margin:14px 0 20px}
  .stat{background:#faf6ef;border-radius:12px;padding:14px 20px}
  .stat .v{font-size:26px;font-weight:800}
  .stat .k{font-size:12px;color:var(--muted)}
  .note{font-size:12px;color:var(--muted);line-height:1.9;background:#faf6ef;border-radius:10px;padding:10px 14px;margin-top:16px}
  footer{text-align:center;color:#a89b8a;font-size:12px;margin-top:22px;line-height:1.9}
</style>
</head>
<body>
<div class="flash" id="flash"></div>
<div class="wrap">
  <h1>🏷 文献相关性标注台</h1>
  <p class="sub">给检索评测做<b>人工金标准</b>：逐篇判断「这篇论文跟主题相关吗」。全程键盘操作，<b>盲标</b>——
  系统的自动判定已隐藏，你的判断不受它影响；标注结果只存本机，导出后用于对比「规则口径 vs 人工口径」。</p>

  <div class="bar"><div class="bar-fill" id="bar" style="width:0%"></div></div>
  <div class="meta">
    <span>进度 <b id="prog">0 / 0</b></span>
    <span>当前主题 <b id="cur-topic">—</b></span>
    <span>✅3 <b id="c3" style="color:var(--green)">0</b> · 🔶2 <b id="c2" style="color:var(--orange)">0</b> · ⛔1 <b id="c1" style="color:var(--red)">0</b> · ⏭跳过 <b id="cs">0</b></span>
  </div>

  <div class="card" id="card">
    <div id="work-area"></div>
    <div class="done-box" id="done-area">
      <div style="font-size:40px">🎉</div>
      <h2>标注完成！</h2>
      <div class="done-stats" id="done-stats"></div>
      <div class="row2" style="max-width:420px;margin:0 auto">
        <button class="k3" style="font-size:15px" id="btn-export">⬇️ 导出 lit_labels.json</button>
        <button class="btn-like" id="btn-review" style="border:1.5px solid var(--line);background:#fff;border-radius:12px;font-size:15px;font-weight:700">🔍 查漏（未标）</button>
      </div>
      <div class="note">导出的 JSON 放回项目的 <b>eval/</b> 文件夹（文件名保持 lit_labels.json），
      然后运行 <b>.venv\Scripts\python.exe eval\human_eval.py</b> 生成三层评测报告。</div>
    </div>
  </div>

  <div class="note">
    <b>打分口径</b>：️3️⃣ 高度相关 = 论文同时覆盖主题的<b>核心概念</b>和<b>应用领域</b>（你会引用的那种）；
    2️⃣ 部分相关 = 只覆盖一头、方法沾边但不是直接研究这个主题；1️⃣ 不相关 = 跑题 / 只在参考文献里蹭了个词。
    拿不准就选 2；确实无法判断可选「跳过」（不计入统计）。<b>快捷键</b>：1 / 2 / 3 打分，U 撤销上一篇，S 跳过。
    关掉页面没关系——进度自动保存在本机浏览器，下次打开接着标。
  </div>
  <footer>lit_labeler · 检索评测的人工金标准工具 · 数据不出本机</footer>
</div>

<script>
'use strict';
var POOL = __POOL_JSON__;
var KEY = 'lit_labels_v1';

var flat = [];
POOL.topics.forEach(function(t) {
  t.items.forEach(function(it) { flat.push({ item: it, topic: t }); });
});
var labels = {};
try { labels = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch (e) {}

var pos = 0;
// 跳到第一篇未标的
for (var i = 0; i < flat.length; i++) { if (!(flat[i].item.uid in labels)) { pos = i; break; } }

function esc(s) { return String(s == null ? '' : s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

function counts() {
  var c = {3:0, 2:0, 1:0, skip:0};
  Object.keys(labels).forEach(function(k) {
    var v = labels[k];
    if (v.label === 'skip') c.skip += 1;
    else c[v.label] += 1;
  });
  return c;
}

function refresh() {
  var done = Object.keys(labels).length;
  document.getElementById('bar').style.width = (done / flat.length * 100) + '%';
  document.getElementById('prog').textContent = done + ' / ' + flat.length;
  var c = counts();
  document.getElementById('c3').textContent = c[3];
  document.getElementById('c2').textContent = c[2];
  document.getElementById('c1').textContent = c[1];
  document.getElementById('cs').textContent = c.skip;
  var work = document.getElementById('work-area');
  var doneBox = document.getElementById('done-area');
  if (pos >= flat.length || (done >= flat.length)) {
    work.style.display = 'none';
    doneBox.style.display = 'block';
    document.getElementById('cur-topic').textContent = '—';
    document.getElementById('done-stats').innerHTML =
      '<div class="stat"><div class="v">' + c[3] + '</div><div class="k">高度相关</div></div>'
      + '<div class="stat"><div class="v">' + c[2] + '</div><div class="k">部分相关</div></div>'
      + '<div class="stat"><div class="v">' + c[1] + '</div><div class="k">不相关</div></div>'
      + '<div class="stat"><div class="v">' + c.skip + '</div><div class="k">跳过</div></div>';
    return;
  }
  work.style.display = 'block';
  doneBox.style.display = 'none';
  var cur = flat[pos], it = cur.item;
  document.getElementById('cur-topic').textContent = cur.topic.topic;
  work.innerHTML =
    '<div class="topic-tag">' + esc(cur.topic.topic) + '</div>'
    + '<div class="title">' + esc(it.title) + '</div>'
    + '<div class="meta-line">' + esc((it.sources || []).join(' · ')) + (it.year ? ' · ' + it.year : '')
    + (it.venue ? ' · ' + esc(it.venue) : '') + '　｜　第 ' + (pos + 1) + ' / ' + flat.length + ' 篇</div>'
    + '<div class="abs">' + (it.abstract ? esc(it.abstract) : '<i>（无摘要，按标题判断）</i>') + '</div>'
    + '<div class="keys">'
    + '<button class="k1" onclick="rate(1)">1 ⛔ 不相关<small>键盘 1</small></button>'
    + '<button class="k2" onclick="rate(2)">2 🔶 部分相关<small>键盘 2</small></button>'
    + '<button class="k3" onclick="rate(3)">3 ✅ 高度相关<small>键盘 3</small></button>'
    + '</div>'
    + '<div class="row2"><button onclick="undo()">↩ 撤销上一篇（U）</button><button onclick="skip()">⏭ 跳过这篇（S）</button></div>';
}

function flash(cls) {
  var f = document.getElementById('flash');
  f.className = 'flash go ' + cls;
  setTimeout(function() { f.className = 'flash'; }, 160);
}

function rate(v) {
  if (pos >= flat.length) return;
  labels[flat[pos].item.uid] = { label: v, topic: flat[pos].topic.id, title: flat[pos].item.title.slice(0, 60), ts: Date.now() };
  localStorage.setItem(KEY, JSON.stringify(labels));
  flash('f' + v);
  advance();
}

function skip() {
  if (pos >= flat.length) return;
  labels[flat[pos].item.uid] = { label: 'skip', topic: flat[pos].topic.id, title: flat[pos].item.title.slice(0, 60), ts: Date.now() };
  localStorage.setItem(KEY, JSON.stringify(labels));
  advance();
}

function undo() {
  for (var i = pos - 1; i >= 0; i--) {
    if (flat[i].item.uid in labels) {
      delete labels[flat[i].item.uid];
      localStorage.setItem(KEY, JSON.stringify(labels));
      pos = i;
      refresh();
      return;
    }
  }
}

function advance() {
  var start = pos;
  pos += 1;
  while (pos < flat.length && (flat[pos].item.uid in labels)) pos += 1;
  if (pos >= flat.length) {
    // 环视还有没有漏的
    for (var j = 0; j < flat.length; j++) { if (!(flat[j].item.uid in labels)) { pos = j; break; } }
    if (j >= flat.length && !(flat.some(function(x){return !(x.item.uid in labels);}))) pos = flat.length;
  }
  refresh();
}

document.addEventListener('keydown', function(ev) {
  if (pos >= flat.length) return;
  if (ev.key === '1') rate(1);
  else if (ev.key === '2') rate(2);
  else if (ev.key === '3') rate(3);
  else if (ev.key === 'u' || ev.key === 'U') undo();
  else if (ev.key === 's' || ev.key === 'S') skip();
});

document.getElementById('btn-export').addEventListener('click', function() {
  var blob = new Blob([JSON.stringify({ exported: new Date().toISOString(), tool: 'lit_labeler', labels: labels }, null, 1)], { type: 'application/json' });
  var a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'lit_labels.json';
  a.click(); URL.revokeObjectURL(a.href);
});
document.getElementById('btn-review').addEventListener('click', function() {
  for (var j = 0; j < flat.length; j++) { if (!(flat[j].item.uid in labels)) { pos = j; refresh(); return; } }
});

refresh();
</script>
</body>
</html>
"""

def main() -> int:
    pool = json.loads(POOL.read_text(encoding="utf-8"))
    total = sum(len(t["items"]) for t in pool["topics"])
    html = TEMPLATE.replace("__POOL_JSON__", json.dumps(pool, ensure_ascii=False))
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    print(f"共 {total} 篇候选 -> {OUT}")
    print("双击 lit_labeler/index.html 开始标注（进度自动保存，导出 lit_labels.json 放回 eval/）。")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
