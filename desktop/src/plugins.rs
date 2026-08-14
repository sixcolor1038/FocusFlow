//! 插件交互：PluginManager 含 Lua（非 Send），只能留在主线程。
//! Tauri 同步命令跑在主线程（WebView2 IPC），用 thread_local 持久持有，
//! 保证 get_view / 按钮动作 / 输入框回写之间共享插件 Lua 状态。

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, SystemTime};

use serde::Serialize;
use tauri::{Emitter, State};

use focusflow_core::db::Database;
use focusflow_core::plugins::manager::PluginManager;
use focusflow_core::plugins::Widget;

use crate::state::AppState;

thread_local! {
    static PM: RefCell<Option<PluginManager>> = const { RefCell::new(None) };
}

/// 获取（必要时初始化）主线程插件管理器并执行操作。
pub fn with_manager<T>(db: &Arc<Database>, f: impl FnOnce(&mut PluginManager) -> T) -> T {
    PM.with(|slot| {
        let mut slot = slot.borrow_mut();
        if slot.is_none() {
            let mut pm = PluginManager::new(focusflow_core::config::instance(), Arc::clone(db));
            pm.load_all();
            *slot = Some(pm);
        }
        f(slot.as_mut().unwrap())
    })
}

/// 热重载监听开关（前端打开/关闭插件管理页时切换）。
/// 只在插件管理页打开时扫描目录，平时零后台开销。
static WATCH_TX: std::sync::OnceLock<std::sync::mpsc::Sender<bool>> =
    std::sync::OnceLock::new();

/// 设置是否监听插件目录变更（true=打开插件管理页，false=离开）。
pub fn set_watch(watch: bool) {
    if let Some(tx) = WATCH_TX.get() {
        let _ = tx.send(watch);
    }
}

/// 启动插件热重载（Tauri 版）。
/// 核心 PluginManager 自带的热重载需要 GUI 每帧 poll_reload_requests，
/// Tauri 没有这个循环，这里用独立实现：扫描线程只在插件管理页打开时
/// 监视 plugins/ 的 mtime 变更，变更通过 channel 交给投递线程，
/// 再用 run_on_main_thread 回到主线程重载（Lua 非 Send，只能主线程操作）。
pub fn start_hot_reload(app: &tauri::AppHandle, db: Arc<Database>) {
    let (ctl_tx, ctl_rx) = std::sync::mpsc::channel::<bool>();
    let _ = WATCH_TX.set(ctl_tx);
    let (tx, rx) = std::sync::mpsc::channel::<String>();

    // 扫描线程：仅监听状态下每 2 秒比对 mtime，变更时发送插件文件名
    std::thread::Builder::new()
        .name("plugin-hot-reload-scan".into())
        .spawn(move || {
            let dir = focusflow_core::paths::plugins_dir();
            let mut last: HashMap<String, Option<SystemTime>> = HashMap::new();
            let mut watching = false;
            loop {
                while let Ok(w) = ctl_rx.try_recv() {
                    watching = w;
                    if w {
                        tracing::debug!("插件热重载监听已开启");
                    } else {
                        tracing::debug!("插件热重载监听已暂停");
                    }
                }
                if !watching {
                    std::thread::sleep(Duration::from_millis(300));
                    continue;
                }
                let mut seen: HashMap<String, Option<SystemTime>> = HashMap::new();
                if let Ok(entries) = std::fs::read_dir(&dir) {
                    for entry in entries.flatten() {
                        let p = entry.path();
                        if p.extension().map(|e| e == "lua").unwrap_or(false) {
                            if let Some(name) =
                                p.file_stem().and_then(|s| s.to_str()).map(|s| s.to_string())
                            {
                                let mtime =
                                    std::fs::metadata(&p).and_then(|m| m.modified()).ok();
                                seen.insert(name, mtime);
                            }
                        }
                    }
                }
                for (name, mtime) in &seen {
                    if last.get(name) != Some(mtime) {
                        let _ = tx.send(name.clone());
                    }
                }
                last = seen;
                std::thread::sleep(Duration::from_secs(2));
            }
        })
        .expect("启动插件热重载扫描线程失败");

    // 投递线程：收到变更 → 主线程执行重载，并通知前端刷新插件列表
    let app_owned = app.clone();
    std::thread::Builder::new()
        .name("plugin-hot-reload-apply".into())
        .spawn(move || {
            while let Ok(name) = rx.recv() {
                let app = app_owned.clone();
                let app_emit = app.clone();
                let db = Arc::clone(&db);
                let _ = app.run_on_main_thread(move || {
                    tracing::info!("热重载检测到变更: {name}");
                    reload_plugin_by_key(&name, &db);
                    let _ = app_emit.emit("plugins-reloaded", name);
                });
            }
        })
        .expect("启动插件热重载应用线程失败");
    tracing::info!("插件热重载已就绪（监听随插件管理页开关）");
}

/// 按插件名或文件名重载插件（主线程调用）。
fn reload_plugin_by_key(key: &str, db: &Arc<Database>) {
    with_manager(db, |pm| {
        if pm.reload_plugin(key) {
            tracing::info!("插件已热重载: {key}");
        } else {
            tracing::warn!("插件热重载失败或未找到: {key}");
        }
    });
}

/// 前端可序列化的控件描述。
#[derive(Serialize, Clone, Default)]
pub struct WidgetDto {
    pub kind: String,
    pub text: Option<String>,
    pub key: Option<String>,
    pub value: Option<String>,
    pub headers: Option<Vec<String>>,
    pub rows: Option<Vec<Vec<String>>>,
    pub id: Option<String>,
    pub field: Option<String>,
}

/// 前端可序列化的插件视图。
#[derive(Serialize, Clone)]
pub struct PluginViewDto {
    pub title: String,
    pub widgets: Vec<WidgetDto>,
}

impl From<&focusflow_core::plugins::PluginView> for PluginViewDto {
    fn from(v: &focusflow_core::plugins::PluginView) -> Self {
        let widgets = v
            .widgets
            .iter()
            .map(|w| match w {
                Widget::Label(t) => WidgetDto {
                    kind: "label".into(),
                    text: Some(t.clone()),
                    ..Default::default()
                },
                Widget::Heading(t) => WidgetDto {
                    kind: "heading".into(),
                    text: Some(t.clone()),
                    ..Default::default()
                },
                Widget::KeyValue(k, v) => WidgetDto {
                    kind: "keyvalue".into(),
                    key: Some(k.clone()),
                    value: Some(v.clone()),
                    ..Default::default()
                },
                Widget::Table { headers, rows } => WidgetDto {
                    kind: "table".into(),
                    headers: Some(headers.clone()),
                    rows: Some(rows.clone()),
                    ..Default::default()
                },
                Widget::Button { id, text } => WidgetDto {
                    kind: "button".into(),
                    id: Some(id.clone()),
                    text: Some(text.clone()),
                    ..Default::default()
                },
                Widget::Separator => WidgetDto {
                    kind: "separator".into(),
                    ..Default::default()
                },
                Widget::TextArea(t) => WidgetDto {
                    kind: "textarea".into(),
                    text: Some(t.clone()),
                    ..Default::default()
                },
                Widget::TextInput { field, label } => WidgetDto {
                    kind: "textinput".into(),
                    field: Some(field.clone()),
                    text: Some(label.clone()),
                    ..Default::default()
                },
            })
            .collect();
        PluginViewDto {
            title: v.title.clone(),
            widgets,
        }
    }
}

/// 插件视图转 DTO（插件无视图时返回 None）。
fn view_dto(pm: &PluginManager, name: &str) -> Option<PluginViewDto> {
    pm.get_plugin(name)
        .and_then(|p| p.view.as_ref())
        .map(PluginViewDto::from)
}

/// 获取插件视图。
#[tauri::command]
pub fn get_plugin_view(state: State<'_, Arc<AppState>>, name: String) -> Option<PluginViewDto> {
    with_manager(&state.db, |pm| view_dto(pm, &name))
}

/// 触发插件按钮动作，返回刷新后的视图。
#[tauri::command]
pub fn plugin_action(
    state: State<'_, Arc<AppState>>,
    name: String,
    id: String,
) -> Result<Option<PluginViewDto>, String> {
    with_manager(&state.db, |pm| {
        pm.plugin_action(&name, &id)?;
        Ok(view_dto(pm, &name))
    })
}

/// 插件输入框回写，返回刷新后的视图。
#[tauri::command]
pub fn plugin_set_field(
    state: State<'_, Arc<AppState>>,
    name: String,
    field: String,
    value: String,
) -> Result<Option<PluginViewDto>, String> {
    with_manager(&state.db, |pm| {
        pm.plugin_set_field(&name, &field, &value)?;
        Ok(view_dto(pm, &name))
    })
}
