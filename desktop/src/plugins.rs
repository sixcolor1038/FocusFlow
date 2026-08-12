//! 插件交互：PluginManager 含 Lua（非 Send），只能留在主线程。
//! Tauri 同步命令跑在主线程（WebView2 IPC），用 thread_local 持久持有，
//! 保证 get_view / 按钮动作 / 输入框回写之间共享插件 Lua 状态。

use std::cell::RefCell;
use std::sync::Arc;

use serde::Serialize;
use tauri::State;

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
