// FocusFlow 主界面逻辑
"use strict";

const invoke = window.__TAURI__.core.invoke;
const { listen } = window.__TAURI__.event;

const WD = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];

let currentView = "rank";
let trendDays = 7;
let chartsData = null;

// ===== 工具 =====
function $(id) { return document.getElementById(id); }

function fmt(n) {
  return Number(n || 0).toLocaleString("zh-CN");
}

function switchView(view) {
  currentView = view;
  document.querySelectorAll("#view-tabs .tab").forEach((b) => b.classList.toggle("active", b.dataset.view === view));
  const ids = ["rank", "group", "trend", "hourly", "weekday", "plugins", "settings"];
  ids.forEach((id) => {
    const el = $("view-" + id);
    if (el) el.style.display = id === view ? "" : "none";
  });
  // 插件管理页打开/关闭时切换热重载监听（打开才扫描，平时零后台开销）
  invoke("plugins_watch", { watch: view === "plugins" }).catch(() => {});
  renderCurrentView();
}

// 只渲染当前可见视图，避免每 500ms 全量重绘隐藏视图
function renderCurrentView() {
  if (currentView === "plugins") return renderPlugins();
  if (currentView === "settings") return renderSettings();
  if (!chartsData) return;
  switch (currentView) {
    case "rank": return renderRank(chartsData);
    case "group": return renderGroup(chartsData);
    case "trend": return renderTrend(chartsData);
    case "hourly": return renderHourly(chartsData);
    case "weekday": return renderWeekday(chartsData);
  }
}

// ===== 统计快照 =====
// 最高单日卡片：今日周期显示"历史最高"作对比目标，其余周期显示窗口内最高单日。
// 日期一律显示完整 (YYYY-MM-DD)，跨年无歧义。
function applyMax(s) {
  $("st-max-label").textContent = s.period === -1 ? "历史最高" : "最高单日";
  $("st-max").textContent = fmt(s.max_day);
  $("st-max-date").textContent = s.max_day_date ? "(" + s.max_day_date + ")" : "";
}

// 轻量数据：今日/速度/周期（高频推送）
function applyLive(s) {
  $("st-today").textContent = fmt(s.today_count);
  $("st-cpm").textContent = fmt(s.cpm) + " 次/分";

  const periodLabel = s.period === -1 ? "今日" : s.period === 0 ? "总计" : s.period + "天";
  $("st-total-label").textContent = "周期总数(" + periodLabel + ")";
  $("st-avg-label").textContent = s.period === -1 ? "日均(今日)" : s.period === 0 ? "日均(近30天)" : "日均(" + s.period + "天)";

  document.querySelectorAll("#period-tabs .tab").forEach((b) => {
    b.classList.toggle("active", Number(b.dataset.period) === s.period);
  });

  applyMax(s);
}

// 重量数据：图表/排行（低频推送，变化才更新）
function applyCharts(s) {
  chartsData = s;
  $("st-total").textContent = fmt(s.total);
  $("st-avg").textContent = fmt(s.avg);
  applyMax(s);
  renderCurrentView();
}

// ===== 键鼠排行 =====
function renderRank(s) {
  const box = $("view-rank");
  if (!s.rank || s.rank.length === 0) {
    box.innerHTML = '<div class="empty">暂无数据</div>';
    return;
  }
  const total = s.total || 0;
  const rows = s.rank
    .map(
      ([k, c], i) =>
        `<tr><td>${i + 1}</td><td class="key">${escapeHtml(k)}</td><td class="num">${fmt(c)}</td><td>${total ? ((c / total) * 100).toFixed(2) : "0.00"}%</td></tr>`
    )
    .join("");
  box.innerHTML = `<table class="grid"><thead><tr>
    <th class="col-rank">排名</th><th class="col-key">键鼠</th><th class="col-count">次数</th><th class="col-percent">占比</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

function renderGroup(s) {
  const box = $("view-group");
  if (!s.group || s.group.length === 0) {
    box.innerHTML = '<div class="empty">暂无数据</div>';
    return;
  }
  const total = s.total || 0;
  const rows = s.group
    .map(
      ([k, c]) =>
        `<tr><td class="key">${escapeHtml(k)}</td><td class="num">${fmt(c)}</td><td>${total ? ((c / total) * 100).toFixed(2) : "0.00"}%</td></tr>`
    )
    .join("");
  box.innerHTML = `<table class="grid"><thead><tr>
    <th>分组</th><th>次数</th><th>占比</th>
    </tr></thead><tbody>${rows}</tbody></table>`;
}

// ===== 趋势图 =====
function renderTrend(s) {
  const data = (trendDays === 30 ? s.trend30 : s.trend) || [];
  const mapped = data.map(([date, value]) => ({ date, value }));
  lineChart($("trend-chart"), "每日活跃趋势", mapped);
}

// ===== 小时 / 星期 =====
function renderHourly(s) {
  const hourly = s.hourly || [];
  const labels = hourly.map((_, h) => h + "时");
  barChart($("hourly-chart"), "今日每小时活跃", hourly, labels);
}

function renderWeekday(s) {
  const wd = s.weekday || [];
  const map = new Map(wd);
  const values = [0, 1, 2, 3, 4, 5, 6].map((i) => map.get(i) || 0);
  barChart($("weekday-chart"), "近30天星期活跃", values, WD);
}

// ===== 插件管理 =====
let openPluginName = null;

async function renderPlugins() {
  if (openPluginName) {
    renderPluginDetail();
    return;
  }
  try {
    const plugins = await invoke("get_plugins");
    const box = $("view-plugins");
    if (!plugins || plugins.length === 0) {
      box.innerHTML = '<div class="empty">暂无插件</div>';
      return;
    }
    box.innerHTML = plugins
      .map(
        (p) =>
          `<div class="plugin-row">
            <div class="info"><div class="name">${escapeHtml(p.name)}</div><div class="desc">${escapeHtml(p.desc || "")}</div></div>
            <div style="display:flex;align-items:center;gap:10px;">
              <span style="font-size:13px;color:var(--muted);">v${escapeHtml(p.version || "")} · ${escapeHtml(p.author || "")}</span>
              <button class="btn ghost" onclick="openPlugin('${escapeHtml(p.name)}')">打开</button>
            </div>
          </div>`
      )
      .join("");
  } catch (e) {
    $("view-plugins").innerHTML = '<div class="empty">插件加载失败</div>';
  }
}

function openPlugin(name) {
  openPluginName = name;
  renderPluginDetail();
}

async function renderPluginDetail() {
  const box = $("view-plugins");
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const view = await invoke("get_plugin_view", { name: openPluginName });
    if (!view) {
      box.innerHTML = `<button class="btn ghost" onclick="closePlugin()">返回</button><div class="empty">插件未提供视图</div>`;
      return;
    }
    let html = `<div style="margin-bottom:8px;"><button class="btn ghost" onclick="closePlugin()">← 返回插件列表</button>
      <span style="margin-left:10px;font-weight:700;font-size:16px;">${escapeHtml(view.title || openPluginName)}</span></div><hr>`;
    for (const w of view.widgets) {
      html += renderWidget(w);
    }
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = `<button class="btn ghost" onclick="closePlugin()">返回</button><div class="empty">加载失败: ${escapeHtml(e)}</div>`;
  }
}

function renderWidget(w) {
  switch (w.kind) {
    case "label":
      return `<p>${escapeHtml(w.text)}</p>`;
    case "heading":
      return `<h3 style="color:var(--accent);margin:12px 0 4px;">${escapeHtml(w.text)}</h3>`;
    case "keyvalue":
      return `<div class="setting-row"><span class="lbl">${escapeHtml(w.key)}</span><span style="font-weight:600;">${escapeHtml(w.value)}</span></div>`;
    case "table":
      return `<table class="grid" style="margin:8px 0;"><thead><tr>${(w.headers || []).map((h) => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${
        (w.rows || []).map((r) => `<tr>${r.map((c) => `<td>${escapeHtml(c)}</td>`).join("")}</tr>`).join("")
      }</tbody></table>`;
    case "button":
      return `<div style="margin:6px 0;"><button class="btn" onclick="pluginBtn('${escapeHtml(openPluginName)}','${escapeHtml(w.id)}')">${escapeHtml(w.text)}</button></div>`;
    case "separator":
      return `<hr>`;
    case "textarea":
      return `<pre style="background:var(--accent-soft);padding:8px;border-radius:8px;white-space:pre-wrap;font-family:inherit;">${escapeHtml(w.text)}</pre>`;
    case "textinput":
      return `<div class="setting-row"><span class="lbl">${escapeHtml(w.text)}</span><input type="text" onchange="pluginField('${escapeHtml(openPluginName)}','${escapeHtml(w.field)}',this.value)"></div>`;
    default:
      return "";
  }
}

async function pluginBtn(name, id) {
  try {
    await invoke("plugin_action", { name, id });
    await renderPluginDetail();
  } catch (e) {
    alert("插件动作失败: " + e);
  }
}

async function pluginField(name, field, value) {
  try {
    await invoke("plugin_set_field", { name, field, value });
    await renderPluginDetail();
  } catch (e) {
    console.error("插件输入失败", e);
  }
}

function closePlugin() {
  openPluginName = null;
  renderPlugins();
}

// ===== 设置 =====
async function renderSettings() {
  const box = $("view-settings");
  const s = await invoke("get_settings");
  const dark = s.theme === "dark";
  const paused = s.paused;
  const hotkeyEnabled = s.hotkey_enabled;
  const hotkeyStr = s.hotkey_str;
  const floatingEnabled = s.floating_enabled;

  box.innerHTML = `
    <div class="section-title">常规</div>
    <div class="setting-row"><span class="lbl">暗色模式</span><input type="checkbox" id="set-dark" ${dark ? "checked" : ""}></div>
    <div class="setting-row"><span class="lbl">暂停记录</span><input type="checkbox" id="set-paused" ${paused ? "checked" : ""}></div>

    <div class="section-title">全局热键</div>
    <div class="setting-row"><span class="lbl">启用热键</span><input type="checkbox" id="set-hotkey-enabled" ${hotkeyEnabled ? "checked" : ""}></div>
    <div class="setting-row"><span class="lbl">热键组合</span><input type="text" id="set-hotkey-str" value="${escapeHtml(hotkeyStr)}"></div>

    <div class="section-title">悬浮窗</div>
    <div class="setting-row"><span class="lbl">显示悬浮窗</span><input type="checkbox" id="set-floating" ${floatingEnabled ? "checked" : ""}></div>
    <div class="setting-row"><button class="btn ghost" onclick="window.__TAURI__.core.invoke('show_floating')">立即显示</button>
      <button class="btn ghost" onclick="window.__TAURI__.core.invoke('hide_floating')">立即隐藏</button></div>

    <div class="section-title">数据操作</div>
    <div class="setting-row">
      <button class="btn ghost" onclick="doImport()">导入旧数据</button>
      <button class="btn ghost" onclick="doExport('csv')">导出 CSV</button>
      <button class="btn ghost" onclick="doExport('html')">导出 HTML</button>
      <button class="btn" onclick="doVacuum()">压缩数据库</button>
      <button class="btn ghost" onclick="doBackup()">立即备份</button>
    </div>
    <div id="set-msg" style="color:var(--success);margin-top:8px;"></div>
    <div id="set-maint" style="color:var(--muted);font-size:13px;margin-top:8px;"></div>
  `;

  $("set-dark").addEventListener("change", async (e) => {
    await invoke("set_config", { section: "gui", key: "theme", value: e.target.checked ? "dark" : "light" });
    document.body.classList.toggle("dark", e.target.checked);
  });
  $("set-paused").addEventListener("change", async (e) => {
    // 同步到实际暂停状态
    const target = e.target.checked;
    if ((await invoke("is_paused")) !== target) await invoke("toggle_pause");
  });
  $("set-hotkey-enabled").addEventListener("change", async (e) => {
    await invoke("set_config", { section: "hotkey", key: "enabled", value: e.target.checked ? "true" : "false" });
  });
  $("set-hotkey-str").addEventListener("change", async (e) => {
    await invoke("set_config", { section: "hotkey", key: "toggle_window", value: e.target.value });
  });
  $("set-floating").addEventListener("change", async (e) => {
    await invoke("set_config", { section: "floating", key: "enabled", value: e.target.checked ? "true" : "false" });
  });
  renderMaintInfo();
}

async function doVacuum() {
  try {
    await invoke("vacuum_db");
    $("set-msg").textContent = "压缩完成";
    $("set-msg").style.color = "var(--success)";
  } catch (e) {
    $("set-msg").textContent = "压缩失败: " + e;
    $("set-msg").style.color = "var(--danger)";
  }
}

async function doImport() {
  try {
    const msg = await invoke("import_legacy");
    $("set-msg").textContent = msg || "导入完成";
    $("set-msg").style.color = "var(--success)";
    applyLive(await invoke("get_live"));
    applyCharts(await invoke("get_charts"));
  } catch (e) {
    $("set-msg").textContent = "导入失败: " + e;
    $("set-msg").style.color = "var(--danger)";
  }
}

async function doExport(fmt) {
  try {
    const path = await invoke("export_report", { fmt });
    $("set-msg").textContent = path && path !== "已取消" ? "已导出: " + path : "已取消";
    $("set-msg").style.color = "var(--success)";
  } catch (e) {
    $("set-msg").textContent = "导出失败: " + e;
    $("set-msg").style.color = "var(--danger)";
  }
}

async function doBackup() {
  try {
    const path = await invoke("do_backup");
    $("set-msg").textContent = "备份完成: " + path;
    $("set-msg").style.color = "var(--success)";
    await renderMaintInfo();
  } catch (e) {
    $("set-msg").textContent = "备份失败: " + e;
    $("set-msg").style.color = "var(--danger)";
  }
}

async function renderMaintInfo() {
  try {
    const info = await invoke("get_maintenance_info");
    const lines = [
      "退出时自动备份（轮转保留最近若干份），上次压缩: " + (info.last_vacuum || "尚未压缩"),
      "备份数量: " + info.backup_count + (info.latest_backup ? "，最新: " + info.latest_backup : ""),
    ];
    $("set-maint").innerHTML = lines.map((l) => `<div>${escapeHtml(l)}</div>`).join("");
  } catch (e) {}
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// ===== 事件绑定与启动 =====
document.querySelectorAll("#period-tabs .tab").forEach((b) => {
  b.addEventListener("click", () => {
    const p = Number(b.dataset.period);
    invoke("set_period", { period: p });
    // 记住选择，重启后默认显示同一周期
    invoke("set_config", { section: "gui", key: "default_period", value: String(p) });
  });
});
document.querySelectorAll("#view-tabs .tab").forEach((b) => {
  b.addEventListener("click", () => switchView(b.dataset.view));
});
document.querySelectorAll("#trend-days .tab").forEach((b) => {
  b.addEventListener("click", () => {
    trendDays = Number(b.dataset.days);
    document.querySelectorAll("#trend-days .tab").forEach((x) => x.classList.toggle("active", x === b));
    if (chartsData) renderTrend(chartsData);
  });
});

// 初始化主题 + 订阅事件 + 初始加载
(async () => {
  try {
    const dark = (await invoke("get_config", { section: "gui", key: "theme" })) === "dark";
    document.body.classList.toggle("dark", dark);
  } catch (e) {
    console.error("读取主题失败", e);
  }
  // 版本号从后端单一来源读取（Cargo 包版本），失败时用默认占位
  try {
    const v = await invoke("get_version");
    $("version").textContent = v ? "FocusFlow v" + v : "";
  } catch (e) {
    $("version").textContent = "";
  }
  try {
    await listen("stats-live", (e) => applyLive(e.payload));
    await listen("stats-charts", (e) => applyCharts(e.payload));
    // 插件热重载完成 → 若停在插件管理页则刷新列表
    await listen("plugins-reloaded", () => {
      if (currentView === "plugins") renderPlugins();
    });
    // 暂停状态在托盘/命令任意入口改变时即时同步设置页勾选
    await listen("pause-changed", (e) => {
      const cb = $("set-paused");
      if (cb) cb.checked = !!e.payload;
    });
  } catch (e) {
    console.error("事件订阅失败", e);
  }
  try {
    applyLive(await invoke("get_live"));
    applyCharts(await invoke("get_charts"));
  } catch (e) {
    console.error("初始加载失败", e);
  }
})();
