// 悬浮窗逻辑：轮询统计、手动拖动、位置持久化、双击打开主界面
"use strict";

const invoke = window.__TAURI__.core.invoke;
const appWindow = window.__TAURI__.window.getCurrentWindow();
const PhysicalPosition = window.__TAURI__.window.PhysicalPosition;

function dbg(msg) {
  try {
    invoke("dbg_log", { msg: msg });
  } catch (e) {}
}

// ===== 手动拖动 =====
let isDown = false;
let drag = null;

document.addEventListener("mousedown", async (e) => {
  if (e.button !== 0) return;
  isDown = true;
  dbg("mousedown screen=(" + e.screenX + "," + e.screenY + ")");
  try {
    const pos = await appWindow.outerPosition();
    drag = { startX: e.screenX, startY: e.screenY, winX: pos.x, winY: pos.y };
    dbg("outerPosition=(" + pos.x + "," + pos.y + ") type=" + (pos.type || "?"));
  } catch (err) {
    drag = null;
    dbg("outerPosition ERROR: " + err);
  }
});

document.addEventListener("mousemove", (e) => {
  if (!isDown) return;
  if (!drag) return;
  const dx = e.screenX - drag.startX;
  const dy = e.screenY - drag.startY;
  if (Math.abs(dx) < 3 && Math.abs(dy) < 3) return;
  try {
    appWindow
      .setPosition(new PhysicalPosition(drag.winX + dx, drag.winY + dy))
      .then(() => dbg("moved to (" + (drag.winX + dx) + "," + (drag.winY + dy) + ")"))
      .catch((err) => dbg("setPosition ERROR: " + err));
  } catch (err) {
    dbg("setPosition throw: " + err);
  }
});

document.addEventListener("mouseup", () => {
  if (isDown) {
    isDown = false;
    persistPosition();
  }
});

// 双击打开主界面
document.addEventListener("dblclick", () => {
  dbg("dblclick");
  invoke("show_main");
});

// 禁用 WebView2 默认右键菜单（另存为等）
document.addEventListener("contextmenu", (e) => e.preventDefault());

// ===== 位置持久化 =====
let lastSaved = { x: null, y: null };
async function persistPosition() {
  try {
    const pos = await appWindow.outerPosition();
    const scale = (await appWindow.scaleFactor()) || 1;
    let px = pos.x / scale;
    let py = pos.y / scale;
    px = Math.round(px);
    py = Math.round(py);
    if (lastSaved.x === px && lastSaved.y === py) return;
    lastSaved = { x: px, y: py };
    await invoke("set_config", { section: "floating", key: "pos_x", value: String(px) });
    await invoke("set_config", { section: "floating", key: "pos_y", value: String(py) });
  } catch (e) {}
}
setInterval(persistPosition, 2000);

// ===== 数据轮询 =====
function fmt(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}

async function refresh() {
  try {
    const s = await invoke("get_stats");
    document.getElementById("today").textContent = fmt(s.today_count);
    document.getElementById("cpm").textContent = String(s.cpm || 0);
  } catch (e) {}
}

(async () => {
  try {
    const dark = (await invoke("get_config", { section: "gui", key: "theme" })) === "dark";
    document.body.classList.toggle("dark", dark);
  } catch (e) {}
  dbg("floating.js loaded");
  try {
    refresh();
  } catch (e) {}
  setInterval(() => {
    try {
      refresh();
    } catch (e) {}
  }, 500);
})();
