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
  // 设置页不依赖图表数据：不随推送重建，避免整页 innerHTML 重建清空正在输入的内容
  if (currentView === "settings") return;
  if (currentView === "plugins") {
    // 插件详情页需要周期性刷新（如番茄钟倒计时），renderPluginDetail 内部做了
    // 代次防抖 + 内容比对，内容未变或输入中不会重建 DOM
    if (openPluginName) renderPluginDetail();
    return;
  }
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

// 插件详情页渲染的请求代次：丢弃乱序返回的陈旧响应
let pluginDetailSeq = 0;

async function renderPluginDetail(force) {
  const seq = ++pluginDetailSeq;
  const box = $("view-plugins");
  let view;
  try {
    view = await invoke("get_plugin_view", { name: openPluginName });
  } catch (e) {
    if (seq !== pluginDetailSeq) return;
    box.innerHTML = `<button class="btn ghost" onclick="closePlugin()">返回</button><div class="empty">加载失败: ${escapeHtml(e)}</div>`;
    return;
  }
  // 已有更新的请求在途：丢弃本次陈旧响应（防乱序覆盖）
  if (seq !== pluginDetailSeq) return;
  if (!view) {
    box.innerHTML = `<button class="btn ghost" onclick="closePlugin()">返回</button><div class="empty">插件未提供视图</div>`;
    return;
  }
  let html = `<div style="margin-bottom:8px;"><button class="btn ghost" onclick="closePlugin()">← 返回插件列表</button>
    <span style="margin-left:10px;font-weight:700;font-size:16px;">${escapeHtml(view.title || openPluginName)}</span></div><hr>`;
  for (const w of view.widgets) {
    html += renderWidget(w);
  }
  // 内容未变化：不重建 DOM，保留正在输入的内容与焦点（每 2s 图表推送也会触发刷新）
  // 注意：行选中高亮（tr.sel）是运行时添加的 class，会破坏 innerHTML 相等比较，
  // 因此比较前先移除高亮，重建后再按保存的选中集合恢复。
  const prevSel = { ...pluginSelIds };
  box.querySelectorAll("tr.sel").forEach((tr) => tr.classList.remove("sel"));
  if (box.innerHTML === html) {
    restoreSelClasses(box, prevSel);
    return;
  }
  // 正在输入（焦点在框内输入控件）：跳过本次重建，失焦后下次刷新再更新。
  // force=true（按钮动作/联动刷新）时强制重建，否则下拉联动会失效。
  if (!force) {
    const ae = document.activeElement;
    if (ae && box.contains(ae) && (ae.tagName === "INPUT" || ae.tagName === "SELECT" || ae.tagName === "TEXTAREA")) {
      return;
    }
  }
  box.innerHTML = html;
  restoreSelClasses(box, prevSel); // 重建后恢复选中状态（高亮 + 集合）
}

// 重建后恢复表格选中行：按保存的分组集合重新加高亮 class 并同步选中集合
function restoreSelClasses(box, groups) {
  const next = {};
  for (const g of Object.keys(groups || {})) {
    const set = new Set();
    (groups[g] || []).forEach((id) => {
      const tr = box.querySelector(`tr[data-group="${g}"][data-rid="${id}"]`);
      if (tr) {
        tr.classList.add("sel");
        set.add(id);
      }
    });
    next[g] = set;
  }
  pluginSelIds = next;
}

function renderWidget(w) {
  switch (w.kind) {
    case "label":
      return `<p>${escapeHtml(w.text)}</p>`;
    case "heading":
      return `<h3 style="color:var(--accent);margin:12px 0 4px;">${escapeHtml(w.text)}</h3>`;
    case "keyvalue":
      return `<div class="setting-row"><span class="lbl">${escapeHtml(w.key)}</span><span style="font-weight:600;">${escapeHtml(w.value)}</span></div>`;
    case "table": {
      const hasActions = (w.ids && w.ids.length && w.actions && w.actions.length);
      const selectable = (w.ids && w.ids.length);
      const thead = (w.headers || []).map((h) => `<th>${escapeHtml(h)}</th>`).join("");
      const tbody = (w.rows || []).map((r, ri) => {
        const cells = r.map((c) => `<td>${escapeHtml(c)}</td>`).join("");
        let actionTd = "";
        if (hasActions) {
          const id = w.ids[ri];
          // M5: stopPropagation 防止点击冒泡到 <tr onclick> 误切换选中
          const btns = w.actions.map((a) => `<button class="btn ghost mini" onclick="event.stopPropagation();pluginBtn('${escapeHtml(openPluginName)}','${escapeHtml(a.prefix)}${id}')">${escapeHtml(a.text)}</button>`).join("");
          actionTd = `<td class="row-actions">${btns}</td>`;
        }
        // 可选中行：点击切换选中（配合顶部 sel 按钮做修改/删除/距今）。
        // 高亮只由 pluginSelectRow 运行时添加 class，不写进模板，
        // 避免与 innerHTML 相等判断产生属性顺序差异导致误重建。
        let rowAttrs = "";
        if (selectable) {
          const rawId = w.ids[ri];
          const grp = escapeHtml(w.group || "");
          const os = escapeHtml(w.onselect || "");
          rowAttrs = ` data-rid="${String(rawId)}" data-group="${grp}" onclick="pluginSelectRow(this, '${grp}', '${String(rawId)}', '${os}')"`;
        }
        return `<tr${rowAttrs}>${cells}${actionTd}</tr>`;
      }).join("");
      return `<table class="grid" style="margin:8px 0;"><thead><tr>${thead}${hasActions ? "<th>操作</th>" : ""}</tr></thead><tbody>${tbody}</tbody></table>`;
    }
    case "button": {
      let onclick;
      if (w.modal) {
        onclick = `modalOpen('${escapeHtml(w.modal)}')`;
      } else if (w.sel) {
        onclick = `pluginBtnSel('${escapeHtml(openPluginName)}','${escapeHtml(w.id)}','${escapeHtml(w.group || "")}')`;
      } else {
        onclick = `pluginBtn('${escapeHtml(openPluginName)}','${escapeHtml(w.id)}')`;
      }
      return `<div style="margin:6px 0;"><button class="btn" ${w.disabled ? "disabled" : ""} onclick="${onclick}">${escapeHtml(w.text)}</button></div>`;
    }
    case "separator":
      return `<hr>`;
    case "textarea":
      return `<pre style="background:var(--accent-soft);padding:8px;border-radius:8px;white-space:pre-wrap;font-family:inherit;">${escapeHtml(w.text)}</pre>`;
    case "textinput":
      return `<div class="setting-row"><span class="lbl">${escapeHtml(w.label || w.text || "")}</span><input type="text" value="${escapeHtml(w.value || "")}" onchange="pluginField('${escapeHtml(openPluginName)}','${escapeHtml(w.field)}',this.value)"></div>`;
    case "select":
      return `<div class="setting-row"><span class="lbl">${escapeHtml(w.label || w.text || "")}</span><select onchange="${w.refresh ? "pluginField" : "pluginFieldStay"}('${escapeHtml(openPluginName)}','${escapeHtml(w.field)}',this.value)">${(w.options || []).map((o) => `<option value="${escapeHtml(o.value)}" ${String(o.value) === String(w.value) ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}</select></div>`;
    case "modal_form": {
      // 弹窗表单：按钮（可选）打开模态框；字段变更写入插件状态（不重建页面），提交触发插件动作
      const mid = w.id || "pf-modal-" + (w.field || Math.random().toString(36).slice(2, 8));
      const fields = (w.fields || []).map((f) => pluginFieldHtml(f)).join("");
      const innerWidgets = (w.children || []).map((c) => renderWidget(c)).join("");
      const bodyHtml = (w.content ? `<pre class="modal-content">${escapeHtml(w.content)}</pre>` : "")
        + fields
        + innerWidgets
        + ((w.actions && w.actions.length) ? `<div class="widget-row modal-actions">${w.actions.map((a) => `<button class="btn" onclick="modalAction('${escapeHtml(openPluginName)}','${escapeHtml(a.prefix)}','${mid}')">${escapeHtml(a.text)}</button>`).join("")}</div>` : "");
      const isOpen = !!(w.open || openModals.has(mid));
      return `<div class="plugin-modal">${w.text ? `<div style="margin:6px 0;"><button class="btn" onclick="modalOpen('${mid}')">${escapeHtml(w.text)}</button></div>` : ""}
        <div class="modal-overlay" id="${mid}" style="display:${isOpen ? "flex" : "none"};" onclick="if (event.target === this) modalCancel('${escapeHtml(openPluginName)}','${escapeHtml(w.cancel || "")}','${mid}')">
          <div class="modal-dialog">
            <div class="modal-head"><span>${escapeHtml(w.title || w.text || "新增")}</span><button class="modal-close" onclick="modalCancel('${escapeHtml(openPluginName)}','${escapeHtml(w.cancel || "")}','${mid}')">✕</button></div>
            <div class="modal-body">${bodyHtml}</div>
            <div class="modal-foot">
              <button class="btn ghost" onclick="modalCancel('${escapeHtml(openPluginName)}','${escapeHtml(w.cancel || "")}','${mid}')">取消</button>
              <button class="btn" onclick="modalSubmit('${escapeHtml(openPluginName)}','${escapeHtml(w.submit)}','${mid}')">${escapeHtml(w.submit_text || "确定")}</button>
            </div>
          </div>
        </div></div>`;
    }
    case "row":
      return `<div class="widget-row">${(w.children || []).map((c) => renderWidget(c)).join("")}</div>`;
    case "pager": {
      const page = Number(w.page || 1), pages = Number(w.pages || 1), total = Number(w.total || 0);
      return `<div class="pager"><button class="btn ghost" ${page <= 1 ? "disabled" : ""} onclick="pluginBtn('${escapeHtml(openPluginName)}','${escapeHtml(w.prev)}')">上一页</button>
        <span>第 ${page} / ${pages} 页 · 共 ${total} 条</span>
        <button class="btn ghost" ${page >= pages ? "disabled" : ""} onclick="pluginBtn('${escapeHtml(openPluginName)}','${escapeHtml(w.next)}')">下一页</button></div>`;
    }
    default:
      return "";
  }
}

// 弹窗表单字段渲染（text / select / date）
function pluginFieldHtml(f) {
  const label = `<span class="lbl">${escapeHtml(f.label || f.text || "")}</span>`;
  const fn = f.refresh ? "pluginField" : "pluginFieldStay";
  const onchange = `${fn}('${escapeHtml(openPluginName)}','${escapeHtml(f.field)}',this.value)`;
  if (f.kind === "select") {
    return `<div class="setting-row">${label}<select onchange="${onchange}">${(f.options || []).map((o) => `<option value="${escapeHtml(o.value)}" ${String(o.value) === String(f.value) ? "selected" : ""}>${escapeHtml(o.label)}</option>`).join("")}</select></div>`;
  }
  if (f.kind === "date") {
    return `<div class="setting-row">${label}<input type="date" value="${escapeHtml(f.value || "")}" onchange="${onchange}"></div>`;
  }
  return `<div class="setting-row">${label}<input type="text" value="${escapeHtml(f.value || "")}" onchange="${onchange}"></div>`;
}

// ===== 插件表格选中（按分组独立管理；无分组时沿用全局多选）=====
// 选中集合：{ group: Set<id> }，group 为空串的条目合并到 "__main"
let pluginSelIds = { "__main": new Set() };
function selSet(group) {
  const g = group || "__main";
  if (!pluginSelIds[g]) pluginSelIds[g] = new Set();
  return pluginSelIds[g];
}
function selAll() {
  return Object.values(pluginSelIds).reduce((acc, s) => { s.forEach((v) => acc.add(v)); return acc; }, new Set());
}
function pluginSelectRow(tr, group, id, onselect) {
  const key = String(id);
  const set = selSet(group);
  if (group) {
    // 有分组：单选（同组内互斥）
    if (set.has(key)) {
      set.delete(key);
      tr.classList.remove("sel");
    } else {
      set.clear();
      set.add(key);
      document.querySelectorAll(`tr[data-group="${group}"]`).forEach((t) => t.classList.remove("sel"));
      tr.classList.add("sel");
      // 联动：把选中项写入插件字段并刷新（如分类→刷新子分类列表）
      if (onselect) {
        pluginField(openPluginName, onselect, key);
      }
    }
  } else {
    // 无分组：多选切换（记账记录列表）
    if (set.has(key)) {
      set.delete(key);
      tr.classList.remove("sel");
    } else {
      set.add(key);
      tr.classList.add("sel");
    }
  }
}
// sel 按钮：动作 id 拼接选中 id 列表（如 "del_" + "42,43" → del_42,43）；
// 修改仅支持单条
async function pluginBtnSel(name, id, group) {
  const set = selSet(group);
  if (set.size === 0) {
    alert("请先在列表中点击选中一项");
    return;
  }
  if (id === "edit_" && set.size > 1) {
    alert("修改仅支持选中一条记录");
    return;
  }
  await pluginBtn(name, id + [...set].join(","));
}

// ===== 插件弹窗 =====
// 已打开的弹窗集合：页面重建（如联动刷新）后弹窗保持打开
const openModals = new Set();
function modalOpen(id) {
  openModals.add(id);
  const m = document.getElementById(id);
  if (m) m.style.display = "flex";
}
function modalClose(id) {
  openModals.delete(id);
  const m = document.getElementById(id);
  if (m) m.style.display = "none";
}
// 表单字段变更：写入插件状态但不重建页面（保持弹窗与输入焦点不被打断）
async function pluginFieldStay(name, field, value) {
  try {
    await invoke("plugin_set_field", { name, field, value });
  } catch (e) {
    console.error("插件输入失败", e);
  }
}
// 弹窗提交：关闭弹窗 → 触发插件动作 → 刷新视图
async function modalSubmit(name, id, modalId) {
  modalClose(modalId);
  await pluginBtn(name, id);
}
// 弹窗内自定义按钮（如分类管理操作）：触发插件动作但弹窗保持打开
async function modalAction(name, id, modalId) {
  await pluginBtn(name, id);
  const m = document.getElementById(modalId);
  if (m) m.style.display = "flex";
}
// 弹窗取消：关闭弹窗；若插件提供了 cancel 动作（如重置编辑状态），一并触发
async function modalCancel(name, cancelId, modalId) {
  modalClose(modalId);
  if (cancelId) await pluginBtn(name, cancelId);
}

async function pluginBtn(name, id) {
  try {
    await invoke("plugin_action", { name, id });
    await renderPluginDetail(true);
  } catch (e) {
    alert("插件动作失败: " + e);
  }
}

async function pluginField(name, field, value) {
  try {
    await invoke("plugin_set_field", { name, field, value });
    await renderPluginDetail(true);
  } catch (e) {
    console.error("插件输入失败", e);
  }
}

function closePlugin() {
  openPluginName = null;
  openModals.clear(); // 离开插件页：清空弹窗打开状态，避免重开时旧弹窗自动弹出
  pluginSelIds = { "__main": new Set() };
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
