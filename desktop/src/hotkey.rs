//! 全局热键：显示/隐藏主窗口。

use tauri::{App, Manager};
use tauri_plugin_global_shortcut::GlobalShortcutExt;

/// 注册全局热键（配置 `[hotkey] toggle_window`，默认 ctrl+shift+f）。
pub fn setup_hotkey(app: &App) {
    let config = focusflow_core::config::instance();
    let hotkey_str = config.get_or("hotkey", "toggle_window", "ctrl+shift+f");
    let enabled = config.get_bool("hotkey", "enabled", false);
    if !enabled {
        return;
    }

    let result = app.global_shortcut().on_shortcut(hotkey_str.as_str(), |app, _shortcut, _event| {
        // 显示/隐藏主窗口（隐藏=销毁，显示=重建）
        if let Some(win) = app.get_webview_window("main") {
            if win.is_visible().unwrap_or(false) {
                crate::state::hide_main_window(app);
            } else {
                crate::state::show_main_window(app);
            }
        } else {
            crate::state::show_main_window(app);
        }
    });

    match result {
        Ok(()) => tracing::info!("全局热键已注册: {hotkey_str}"),
        Err(e) => tracing::warn!("全局热键注册失败: {e}"),
    }
}
