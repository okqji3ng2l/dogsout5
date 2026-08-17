# -*- coding: utf-8 -*-
"""AI 縮圖生成 Web 介面（前端）

這個檔案只負責前端頁面（HTML/CSS/JS 由 JavaScript 動態建立），
後端 API 拆成兩個 FastAPI APIRouter：
- thumbnail_router.py：縮圖生成（GET /api/keywords、POST /api/generate）
- video_router.py    ：腳本與影片生成（POST /api/script、POST /api/video）

啟動：python app.py  →  http://localhost:5000
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from thumbnail_router import thumbnail_router
from video_router import video_router

app = FastAPI(title="AI 縮圖產生器")
# 其他前端（不同 port）或後端需要 cluster 資料時，直接拉 GET /api/keywords；
# 開 CORS 讓跨來源的網頁也能呼叫
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).resolve().parent / "static"),
    name="static",
)
app.include_router(thumbnail_router)
app.include_router(video_router)


# ---------- 頁面（HTML 由 JavaScript 動態建立） ----------

INDEX_HTML = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI 縮圖產生器</title>
<style>
  :root {
    --surface: #fcfcfb; --page: #f9f9f7; --ink: #0b0b0b;
    --ink-2: #52514e; --muted: #898781; --line: #e1e0d9;
    --accent: #2a78d6; --accent-dark: #1c5cab;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--page); color: var(--ink);
    font-family: system-ui, -apple-system, "Segoe UI",
                 "Microsoft JhengHei", sans-serif;
  }
  main { max-width: 1100px; margin: 0 auto; padding: 24px 16px 64px; }
  h1 { font-size: 24px; margin: 8px 0 20px; }
  section {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 12px; padding: 18px 20px; margin-bottom: 16px;
  }
  section h2 { font-size: 16px; margin: 0 0 12px; }
  section h2 .num {
    display: inline-block; width: 22px; height: 22px; line-height: 22px;
    text-align: center; border-radius: 50%; background: var(--accent);
    color: #fff; font-size: 13px; margin-right: 8px;
  }
  .hint { color: var(--muted); font-size: 13px; margin: 0 0 10px; }
  .tabs { margin-bottom: 10px; }
  .tabs button {
    border: 1px solid var(--line); background: none; color: var(--ink-2);
    padding: 4px 14px; border-radius: 16px; cursor: pointer; font-size: 13px;
    margin-right: 6px;
  }
  .tabs button.active { background: var(--ink); color: #fff; border-color: var(--ink); }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; max-height: 220px; overflow-y: auto; }
  .chip {
    border: 1px solid var(--line); background: none; color: var(--ink);
    padding: 6px 14px; border-radius: 18px; cursor: pointer; font-size: 14px;
  }
  .chip.selected { background: var(--accent); border-color: var(--accent); color: #fff; }
  .styles { display: flex; gap: 10px; }
  .style-card {
    border: 2px solid var(--line); border-radius: 8px; padding: 8px 22px;
    cursor: pointer; background: none; font-size: 15px; color: var(--ink);
  }
  .style-card.selected { border-color: var(--accent); background: #eef4fc; }
  #dropzone {
    border: 2px dashed var(--line); border-radius: 10px; padding: 28px;
    text-align: center; color: var(--muted); cursor: pointer;
  }
  #dropzone.dragover { border-color: var(--accent); background: #eef4fc; }
  #dropzone img { max-width: 100%; max-height: 240px; border-radius: 8px; }
  textarea {
    width: 100%; min-height: 72px; border: 1px solid var(--line);
    border-radius: 8px; padding: 10px; font: inherit; resize: vertical;
    background: var(--surface); color: var(--ink);
  }
  #generate-btn, #script-btn {
    width: 100%; padding: 13px; font-size: 16px; border: none;
    border-radius: 10px; background: var(--accent); color: #fff; cursor: pointer;
  }
  #generate-btn:hover, #script-btn:hover { background: var(--accent-dark); }
  #generate-btn:disabled, #script-btn:disabled { background: var(--muted); cursor: wait; }
  #result img { max-width: 100%; border-radius: 10px; border: 1px solid var(--line); }
  #result .meta, #script-result .meta, #video-result .meta { color: var(--muted); font-size: 13px; margin-top: 8px; white-space: pre-wrap; }
  .error { color: #d03b3b; font-size: 14px; }
  .page-tabs { display: flex; gap: 8px; margin-bottom: 18px; }
  .page-tabs button {
    flex: 1; padding: 10px; font-size: 15px; cursor: pointer;
    border: 1px solid var(--line); border-radius: 10px;
    background: var(--surface); color: var(--ink-2);
  }
  .page-tabs button.active {
    background: var(--accent); border-color: var(--accent); color: #fff;
  }
  .topic-box {
    display: inline-block; font-size: 15px; padding: 8px 16px;
    background: #eef4fc; border-radius: 8px;
  }
  /* 腳本輸出格子（依 example/input_box.png 設計） */
  .script-card {
    border: 1px solid var(--line); border-radius: 18px; background: var(--surface);
    padding: 14px 16px 12px; box-shadow: 0 1px 3px rgba(0,0,0,.05);
  }
  #script-text {
    min-height: 90px; white-space: pre-wrap; outline: none;
    font: inherit; color: var(--ink); padding: 2px 2px 12px;
  }
  #script-text:empty::before { content: attr(data-placeholder); color: var(--muted); }
  #script-text[contenteditable="true"] {
    border-radius: 8px; background: #fffbea; caret-color: var(--accent);
  }
  .script-toolbar { display: flex; align-items: center; justify-content: space-between; }
  .script-toolbar .left { display: flex; gap: 8px; }
  .tool-btn {
    width: 40px; height: 40px; border-radius: 50%; font-size: 18px;
    border: 1px solid var(--line); background: var(--surface); cursor: pointer;
    display: flex; align-items: center; justify-content: center; padding: 0;
  }
  .tool-btn:hover { background: var(--page); }
  .tool-btn.active { border-color: var(--accent); background: #eef4fc; }
  .send-btn {
    width: 46px; height: 40px; border-radius: 12px; border: none;
    background: #e7e7e2; color: var(--ink); font-size: 19px; cursor: pointer;
  }
  .send-btn:hover { background: #dbdbd4; }
  .tool-btn:disabled, .send-btn:disabled { opacity: .45; cursor: wait; }
  #video-result video { max-width: 100%; border-radius: 10px; margin-top: 12px; }

  /* 左右雙欄版面（左：設定 1，右：成果展示 2） */
  .two-col {
    display: grid; grid-template-columns: 1fr 2fr;
    gap: 16px; align-items: start;
  }
  .col-left, .col-right { min-width: 0; }
  /* 右欄成果面板：在較寬的視窗時吸附在頂端跟隨捲動 */
  .col-right .panel { position: sticky; top: 16px; }
  /* 視窗較窄時左右欄改為上下堆疊 */
  @media (max-width: 900px) {
    .two-col { grid-template-columns: 1fr; }
    .col-right .panel { position: static; }
  }
  /* 成果展示面板（沿用卡片風格，但標題不帶序號） */
  .panel {
    background: var(--surface); border: 1px solid var(--line);
    border-radius: 12px; padding: 18px 20px; margin-bottom: 16px;
  }
  .panel > h2 { font-size: 16px; margin: 0 0 12px; }
  /* 生成前的灰色佔位提示 */
  .placeholder {
    border: 2px dashed var(--line); border-radius: 10px;
    padding: 56px 20px; text-align: center; color: var(--muted);
    background: var(--page); font-size: 14px;
  }
  /* 時間進度條（簡潔橫向長條） */
  .progress-wrap { margin-top: 16px; }
  .progress-bar {
    height: 10px; background: var(--line);
    border-radius: 6px; overflow: hidden;
  }
  .progress-fill {
    height: 100%; width: 0%; background: var(--accent);
    border-radius: 6px; transition: width .4s linear;
  }
  .progress-label { font-size: 13px; color: var(--muted); margin-top: 6px; }
</style>
</head>
<body>
<main id="app"></main>
<script>
// ---- 整個頁面的 HTML 都由 JavaScript 建立 ----
const state = { keyword: null, style: null, file: null, lang: "chinese", duration: null, thumbnail: null };
let keywordData = { chinese: [], english: [] };

const app = document.getElementById("app");

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const c of children)
    node.append(typeof c === "string" ? document.createTextNode(c) : c);
  return node;
}

function section(num, title, ...children) {
  return el("section", {},
    el("h2", {}, el("span", { class: "num" }, String(num)), title),
    ...children);
}

// 右欄成果展示面板：卡片外觀但標題不帶序號
function panel(title, ...children) {
  return el("div", { class: "panel" },
    el("h2", {}, title),
    ...children);
}

// 灰色佔位提示（生成前顯示於右欄成果區）
function placeholder(text) {
  return el("div", { class: "placeholder" }, text);
}

// -- 區塊 1：關鍵字選擇（單選） --
const chipsBox = el("div", { class: "chips" },
  "載入分群中...（第一次執行需要跑 embedding 與命名，可能要幾分鐘）");
function renderChips() {
  chipsBox.replaceChildren();
  const list = keywordData[state.lang] || [];
  if (!list.length) { chipsBox.append("（沒有關鍵字）"); return; }
  for (const kw of list) {
    const chip = el("button", {
      class: "chip" + (state.keyword === kw ? " selected" : ""),
      onclick: () => { state.keyword = kw; renderChips(); },
    }, kw);
    chipsBox.append(chip);
  }
}
const tabs = el("div", { class: "tabs" },
  ...[["chinese", "中文"], ["english", "英文"]].map(([key, label]) =>
    el("button", {
      class: key === state.lang ? "active" : "",
      onclick: (e) => {
        state.lang = key; state.keyword = null;
        tabs.querySelectorAll("button").forEach(b => b.classList.remove("active"));
        e.currentTarget.classList.add("active");
        renderChips();
      },
    }, label)));
const keywordSection = section(1, "關鍵字選擇",
  el("p", { class: "hint" }, "點選一個想要的主題分類（單選）"), tabs, chipsBox);

// -- 區塊 2：縮圖風格選擇（只列出風格名稱） --
const stylesBox = el("div", { class: "styles" },
  ...["浮誇", "寫實", "卡通"].map((name) => {
    const card = el("button", {
      class: "style-card",
      onclick: () => {
        state.style = name;
        stylesBox.querySelectorAll(".style-card")
          .forEach(c => c.classList.remove("selected"));
        card.classList.add("selected");
      },
    }, name);
    return card;
  }));
const styleSection = section(2, "縮圖風格選擇", stylesBox);

// -- 區塊 3：拖曳圖片欄位（可留空） --
const fileInput = el("input", { type: "file", accept: "image/*", style: "display:none" });
const dropzone = el("div", { id: "dropzone" }, "拖曳圖片到這裡，或點擊選擇檔案（可留空）");
function setFile(file) {
  state.file = file || null;
  dropzone.replaceChildren();
  if (!file) {
    dropzone.append("拖曳圖片到這裡，或點擊選擇檔案（可留空）");
    return;
  }
  const img = el("img", { src: URL.createObjectURL(file) });
  dropzone.append(img, el("p", { class: "hint" }, file.name + "（再點一下可更換，按住 Shift 點擊可移除）"));
}
dropzone.addEventListener("click", (e) => {
  if (e.shiftKey && state.file) { setFile(null); return; }
  fileInput.click();
});
fileInput.addEventListener("change", () => setFile(fileInput.files[0]));
for (const ev of ["dragover", "dragleave", "drop"]) {
  dropzone.addEventListener(ev, (e) => {
    e.preventDefault();
    dropzone.classList.toggle("dragover", ev === "dragover");
    if (ev === "drop" && e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
  });
}
const imageSection = section(3, "上傳參考圖片", dropzone, fileInput);

// -- 區塊 4：其他要求（可留空） --
const extraInput = el("textarea", { placeholder: "例如：加入「必看」兩個大字、背景放煙火...（可留空）" });
const extraSection = section(4, "使用者的其他要求", extraInput);

// -- 區塊 5：產生縮圖（按鈕在左欄，成果顯示於右欄） --
const resultBox = el("div", { id: "result" }, placeholder("產生的縮圖會顯示在這裡"));
const generateBtn = el("button", { id: "generate-btn", onclick: generate }, "產生縮圖");
const generateSection = section(5, "產生縮圖", generateBtn);

async function generate() {
  resultBox.replaceChildren();
  if (!state.keyword) { resultBox.append(el("p", { class: "error" }, "請先在區塊 1 選擇關鍵字")); return; }
  if (!state.style) { resultBox.append(el("p", { class: "error" }, "請先在區塊 2 選擇縮圖風格")); return; }

  generateBtn.disabled = true;
  generateBtn.textContent = "生成中，請稍候...";
  const form = new FormData();
  form.append("keyword", state.keyword);
  form.append("style", state.style);
  form.append("extra", extraInput.value.trim());
  if (state.file) form.append("image", state.file);

  try {
    const resp = await fetch("/api/generate", { method: "POST", body: form });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    state.thumbnail = data.image; // 留給「腳本&影片生成」頁傳給 Veo
    resultBox.append(el("img", { src: data.image }));
  } catch (err) {
    resultBox.append(el("p", { class: "error" }, String(err.message || err)));
  } finally {
    generateBtn.disabled = false;
    generateBtn.textContent = "產生縮圖";
  }
}

// ---- 頁面 2：腳本&影片生成 ----
// 區塊 1：影片主題與風格（直接延續縮圖製作頁選擇的關鍵字與風格）
const topicBox = el("div", { class: "topic-box" }, "尚未選擇");
const styleBox = el("div", { class: "topic-box" }, "尚未選擇");
const topicSection = section(1, "影片主題與風格",
  el("p", { class: "hint" }, "延續「縮圖製作」頁的關鍵字與風格選擇"),
  topicBox, " ", styleBox);

// 區塊 2：影片時長
const durationsBox = el("div", { class: "styles" },
  ...["8s", "16s", "24s", "32s"].map((d) => {
    const btn = el("button", {
      class: "style-card",
      onclick: () => {
        state.duration = d;
        durationsBox.querySelectorAll(".style-card")
          .forEach(c => c.classList.remove("selected"));
        btn.classList.add("selected");
      },
    }, d);
    return btn;
  }));
const durationSection = section(2, "影片時長", durationsBox);

// 區塊 3：影片內容
const videoContentInput = el("textarea",
  { placeholder: "想要產出的影片內容，例如：貓咪和狗狗搶罐頭大戰..." });
const contentSection = section(3, "影片內容", videoContentInput);

// 區塊 4：腳本輸出（依 example/input_box.png 的輸入卡片設計）
const scriptText = el("div", {
  id: "script-text",
  "data-placeholder": "按左下角大腦圖示由 AI 生成腳本，或按鉛筆圖示自行編輯...",
});
const scriptResult = el("div", { id: "script-result" }); // 錯誤訊息
const videoResult = el("div", { id: "video-result" },    // 生成的短影音（顯示於右欄）
  placeholder("生成的短影音會顯示在這裡"));

// -- 右欄下方的時間進度條（生成影片時依經過時間前進） --
const progressFill = el("div", { class: "progress-fill" });
const progressLabel = el("div", { class: "progress-label" }, "尚未開始");
const progressWrap = el("div", { class: "progress-wrap", style: "display:none" },
  el("div", { class: "progress-bar" }, progressFill),
  progressLabel);

let progressTimer = null;
let progressStart = 0;
const EST_SECONDS = 240; // 估計影片生成需時（秒），僅用來讓進度條緩慢前進

// 開始進度：依經過時間逼近估計時間，但最多到 95%，避免提早填滿
function startProgress() {
  if (progressTimer) clearInterval(progressTimer);
  progressStart = Date.now();
  progressWrap.style.display = "";
  progressFill.style.width = "0%";
  progressLabel.textContent = "已經過 0 秒";
  progressTimer = setInterval(() => {
    const elapsed = (Date.now() - progressStart) / 1000;
    const pct = Math.min(95, (elapsed / EST_SECONDS) * 100);
    progressFill.style.width = pct.toFixed(1) + "%";
    progressLabel.textContent = "已經過 " + Math.floor(elapsed) + " 秒";
  }, 1000);
}

// 完成：進度條填滿
function finishProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  const elapsed = Math.floor((Date.now() - progressStart) / 1000);
  progressFill.style.width = "100%";
  progressLabel.textContent = "完成，共花費 " + elapsed + " 秒";
}

// 失敗：停止前進
function failProgress() {
  if (progressTimer) { clearInterval(progressTimer); progressTimer = null; }
  progressLabel.textContent = "生成失敗，已停止";
}

// 大腦／鉛筆改用黑白線條 icon（inline SVG，線條顏色跟隨按鈕文字色）
const BRAIN_SVG = '<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/><path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4"/></svg>';
const PENCIL_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M21.174 6.812a1 1 0 0 0-3.986-3.987L3.842 16.174a2 2 0 0 0-.5.83l-1.321 4.352a.5.5 0 0 0 .623.622l4.353-1.32a2 2 0 0 0 .83-.497z"/><path d="m15 5 4 4"/></svg>';

const brainBtn = el("button",
  { class: "tool-btn", title: "AI 生成腳本", onclick: generateScript });
brainBtn.innerHTML = BRAIN_SVG;
const pencilBtn = el("button",
  { class: "tool-btn", title: "人工編輯腳本", onclick: toggleEdit });
pencilBtn.innerHTML = PENCIL_SVG;
const sendBtn = el("button",
  { class: "send-btn", title: "確認腳本，傳送給 Gemini 生成短影音", onclick: sendToVideo }, "↑");

const scriptCard = el("div", { class: "script-card" },
  scriptText,
  el("div", { class: "script-toolbar" },
    el("div", { class: "left" }, brainBtn, pencilBtn),
    sendBtn));
const scriptSection = section(4, "腳本輸出", scriptCard, scriptResult);

// ✏️：切換人工編輯模式（contenteditable）
function toggleEdit() {
  const editing = scriptText.getAttribute("contenteditable") === "true";
  scriptText.setAttribute("contenteditable", editing ? "false" : "true");
  pencilBtn.classList.toggle("active", !editing);
  if (!editing) scriptText.focus();
}

function setBusy(busy) {
  brainBtn.disabled = pencilBtn.disabled = sendBtn.disabled = busy;
}

// 🧠：AI 生成腳本（串流寫入卡片文字區）
async function generateScript() {
  scriptResult.replaceChildren();
  if (!state.keyword) {
    scriptResult.append(el("p", { class: "error" }, "請先到「縮圖製作」頁選擇關鍵字作為影片主題"));
    return;
  }
  if (!state.style) {
    scriptResult.append(el("p", { class: "error" }, "請先到「縮圖製作」頁選擇風格作為影片風格"));
    return;
  }
  if (!state.duration) {
    scriptResult.append(el("p", { class: "error" }, "請先選擇影片時長"));
    return;
  }
  setBusy(true);
  scriptText.textContent = "";
  try {
    const resp = await fetch("/api/script", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        keyword: state.keyword,
        style: state.style,
        duration: state.duration,
        content: videoContentInput.value.trim(),
      }),
    });
    if (!resp.ok) {
      const data = await resp.json();
      throw new Error(data.error || resp.statusText);
    }
    // 後端以 NDJSON 串流回傳，逐行解析、即時更新卡片文字區
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\\n");
      buf = lines.pop(); // 最後一段可能還不完整，留到下一輪
      for (const line of lines) {
        if (!line.trim()) continue;
        const data = JSON.parse(line);
        if (data.error) throw new Error(data.error);
        if (data.script !== undefined) scriptText.textContent = data.script; // 累積內容直接覆蓋
      }
    }
  } catch (err) {
    scriptResult.append(el("p", { class: "error" }, String(err.message || err)));
  } finally {
    setBusy(false);
  }
}

// ↑：腳本最後確認，把最終腳本當作 prompt 傳給 Gemini 生成短影音
async function sendToVideo() {
  videoResult.replaceChildren();
  const script = scriptText.textContent.trim();
  if (!script) {
    videoResult.append(el("p", { class: "error" }, "腳本是空的，請先用大腦圖示生成或鉛筆圖示編輯腳本"));
    return;
  }
  // 送出前先關閉編輯模式
  scriptText.setAttribute("contenteditable", "false");
  pencilBtn.classList.remove("active");
  setBusy(true);
  videoResult.append(el("p", { class: "hint" }, "短影音生成中（需要數分鐘，請耐心等候）..."));
  startProgress();
  try {
    const resp = await fetch("/api/video", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      // 一併帶上縮圖製作頁生成的縮圖（沒有就純文字生成）
      body: JSON.stringify({ script, image: state.thumbnail }),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || resp.statusText);
    videoResult.replaceChildren(el("video", { src: data.video, controls: "" }));
    finishProgress();
  } catch (err) {
    videoResult.replaceChildren(el("p", { class: "error" }, String(err.message || err)));
    failProgress();
  } finally {
    setBusy(false);
  }
}

// -- 組裝頁面（兩個可切換的分頁，皆為左右雙欄） --
// 縮圖製作：左欄放全部設定，右欄呈現最終縮圖
const thumbPage = el("div", { class: "two-col" },
  el("div", { class: "col-left" },
    keywordSection, styleSection, imageSection, extraSection, generateSection),
  el("div", { class: "col-right" },
    panel("最終縮圖", resultBox)));
// 腳本&影片生成：左欄放全部設定，右欄呈現最終短影音與時間進度條
const videoPage = el("div", { class: "two-col", style: "display:none" },
  el("div", { class: "col-left" },
    topicSection, durationSection, contentSection, scriptSection),
  el("div", { class: "col-right" },
    panel("最終短影音", videoResult, progressWrap)));

const pageTabs = el("div", { class: "page-tabs" },
  el("button", { class: "active", onclick: () => showPage("thumb") }, "縮圖製作"),
  el("button", { onclick: () => showPage("video") }, "腳本&影片生成"));

function showPage(name) {
  thumbPage.style.display = name === "thumb" ? "" : "none";
  videoPage.style.display = name === "video" ? "" : "none";
  const [thumbTab, videoTab] = pageTabs.querySelectorAll("button");
  thumbTab.classList.toggle("active", name === "thumb");
  videoTab.classList.toggle("active", name === "video");
  // 影片主題與風格直接延續縮圖製作頁的選擇
  topicBox.textContent =
    "主題：" + (state.keyword || "尚未選擇（請先到「縮圖製作」頁點選關鍵字）");
  styleBox.textContent =
    "風格：" + (state.style || "尚未選擇（請先到「縮圖製作」頁點選風格）");
}

app.append(
  el("h1", {}, "AI 縮圖產生器"),
  pageTabs, thumbPage, videoPage);

// 載入關鍵字
fetch("/api/keywords")
  .then(r => r.json().then(d => ({ ok: r.ok, d })))
  .then(({ ok, d }) => {
    if (!ok) throw new Error(d.error || "載入失敗");
    keywordData = d;
    renderChips();
  })
  .catch(err => {
    chipsBox.replaceChildren(el("p", { class: "error" }, "關鍵字載入失敗：" + err.message));
  });
</script>
</body>
</html>"""


@app.get("/")
async def index():
    return HTMLResponse(INDEX_HTML)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app:app", host="0.0.0.0", port=5000, reload=True)
