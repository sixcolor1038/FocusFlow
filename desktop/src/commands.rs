//! Tauri 命令：暴露统计/配置/监听/数据操作给前端。

use std::sync::Arc;

use tauri::{AppHandle, Manager, State};

use crate::state::{AppState, SharedStats};

/// 获取统计快照（前端轮询）。
#[tauri::command]
pub fn get_stats(state: State<'_, Arc<AppState>>) -> SharedStats {
    state.shared.lock().unwrap().clone()
}

/// 设置统计周期（-1=今日, N=天数, 0=总计）并触发即时重聚合。
#[tauri::command]
pub fn set_period(state: State<'_, Arc<AppState>>, period: i64) {
    state.period.store(period, std::sync::atomic::Ordering::Relaxed);
    state.refresh_now.store(true, std::sync::atomic::Ordering::Relaxed);
}

/// 读取配置值。
#[tauri::command]
pub fn get_config(state: State<'_, Arc<AppState>>, section: String, key: String) -> String {
    state.config.get(&section, &key)
}

/// 写入配置值并持久化。
#[tauri::command]
pub fn set_config(state: State<'_, Arc<AppState>>, section: String, key: String, value: String) -> Result<(), String> {
    state
        .config
        .set(&section, &key, &value)
        .map_err(|e| e.to_string())
}

/// 切换暂停记录，返回新的暂停状态。
#[tauri::command]
pub fn toggle_pause(state: State<'_, Arc<AppState>>) -> bool {
    state.listener.toggle_pause()
}

/// 是否已暂停。
#[tauri::command]
pub fn is_paused(state: State<'_, Arc<AppState>>) -> bool {
    state.listener.is_paused()
}

/// 显示主窗口。
#[tauri::command]
pub fn show_main(app: AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

/// 隐藏主窗口（到托盘）。
#[tauri::command]
pub fn hide_main(app: AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.hide();
    }
}

/// 显示悬浮窗。
#[tauri::command]
pub fn show_floating(app: AppHandle) {
    if let Some(win) = app.get_webview_window("floating") {
        let _ = win.show();
        let _ = win.set_focus();
    }
}

/// 隐藏悬浮窗。
#[tauri::command]
pub fn hide_floating(app: AppHandle) {
    if let Some(win) = app.get_webview_window("floating") {
        let _ = win.hide();
    }
}

/// 立即 flush 数据库（写线程排空队列）。
#[tauri::command]
pub fn flush_db(state: State<'_, Arc<AppState>>) {
    state.db.flush(false);
}

/// 压缩所有年度数据库。
#[tauri::command]
pub fn vacuum_db(state: State<'_, Arc<AppState>>) -> Result<(), String> {
    state.db.flush(true);
    focusflow_core::db::maintenance::vacuum_all();
    Ok(())
}

/// 退出应用（托盘"退出程序"）。
#[tauri::command]
pub fn quit(app: AppHandle) {
    app.exit(0);
}

/// 插件元数据（前端插件管理列表）。
#[derive(serde::Serialize)]
pub struct PluginMeta {
    pub name: String,
    pub desc: String,
    pub version: String,
    pub author: String,
}

/// 导入旧版 FocusFlow 数据（选择目录 → 后台导入）。
#[tauri::command]
pub async fn import_legacy(state: State<'_, Arc<AppState>>) -> Result<String, String> {
    let picked = rfd::FileDialog::new()
        .set_title("选择旧版 FocusFlow 数据目录（data 文件夹）")
        .pick_folder();
    let Some(dir) = picked else {
        return Ok("已取消".to_string());
    };

    let db = Arc::clone(&state.db);
    let dir_for_thread = dir.clone();
    let summary = tauri::async_runtime::spawn_blocking(move || {
        focusflow_core::migration::import_legacy_data(&dir_for_thread)
    })
    .await
    .map_err(|e| e.to_string())?;

    // 导入直接写库，重建缓存与今日计数保持一致
    focusflow_core::db::queries::invalidate_years_cache();
    if let Some(w) = db.writer() {
        w.recompute_today_count();
    }
    state.refresh_now.store(true, std::sync::atomic::Ordering::Relaxed);

    let mut lines: Vec<String> = Vec::new();
    if summary.year_dbs.is_empty() && summary.copied_aux.is_empty() {
        lines.push("未发现可导入的数据".to_string());
    }
    for (year, count) in &summary.records_by_year {
        lines.push(format!("{year} 年度键鼠: {count} 条"));
    }
    if !summary.copied_aux.is_empty() {
        lines.push(format!("附属数据: {}", summary.copied_aux.join(", ")));
    }
    for e in &summary.errors {
        lines.push(format!("错误: {e}"));
    }
    tracing::info!(
        "导入完成: 来源={} 结果={}",
        dir.display(),
        lines.join("；")
    );
    Ok(lines.join("；"))
}

/// 导出统计报告（CSV / HTML）。
#[tauri::command]
pub async fn export_report(fmt: String) -> Result<String, String> {
    let ext = if fmt == "csv" { "csv" } else { "html" };
    let file = rfd::FileDialog::new()
        .set_title("导出统计报告")
        .set_file_name(format!("focusflow_export.{ext}"))
        .add_filter(if fmt == "csv" { "CSV" } else { "HTML" }, &[ext])
        .save_file();
    let Some(path) = file else {
        return Ok("已取消".to_string());
    };

    let (total, stats) = focusflow_core::db::get_stats(None, None);
    let ok = if fmt == "csv" {
        crate::export::export_csv(&path, total, &stats)
    } else {
        crate::export::export_html(&path, total, &stats)
    };
    if ok {
        Ok(path.display().to_string())
    } else {
        Err("导出失败".to_string())
    }
}

/// 维护信息：上次压缩时间 / 备份数量 / 最新备份。
#[tauri::command]
pub fn get_maintenance_info() -> serde_json::Value {
    // 上次 VACUUM 时间（meta 表）
    let last_vacuum = focusflow_core::db::connection::with_ro_conn(
        &focusflow_core::paths::current_year_db_path(),
        |conn| {
            conn.query_row(
                "SELECT value FROM meta WHERE key='last_vacuum'",
                [],
                |r| r.get::<_, String>(0),
            )
            .ok()
        },
    )
    .flatten();

    // 备份目录信息
    let mut backups: Vec<std::path::PathBuf> = std::fs::read_dir(focusflow_core::paths::backup_dir())
        .map(|it| it.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|e| e == "db")).collect())
        .unwrap_or_default();
    backups.sort_by_key(|p| std::fs::metadata(p).and_then(|m| m.modified()).ok());
    let latest = backups.last().map(|p| p.file_name().unwrap().to_string_lossy().to_string());

    serde_json::json!({
        "last_vacuum": last_vacuum,
        "backup_count": backups.len(),
        "latest_backup": latest,
    })
}

/// 立即备份数据库，返回备份文件路径。
#[tauri::command]
pub fn do_backup(state: State<'_, Arc<AppState>>) -> Result<String, String> {
    let max_backups = state
        .config
        .get_int("database", "max_backups", 5)
        .max(1);
    state.db.flush(true);
    let path = focusflow_core::db::maintenance::backup_database(max_backups);
    path.map(|p| p.display().to_string())
        .ok_or_else(|| "备份失败".to_string())
}

/// 调试：前端写入日志（定位悬浮窗拖动问题）。
#[tauri::command]
pub fn dbg_log(msg: String) {
    tracing::info!("[floating-debug] {msg}");
}

/// 列出插件（复用主线程共享的插件管理器）。
#[tauri::command]
pub fn get_plugins(state: State<'_, Arc<AppState>>) -> Vec<PluginMeta> {
    crate::plugins::with_manager(&state.db, |pm| {
        pm.get_all_plugins()
            .iter()
            .map(|p| PluginMeta {
                name: p.name.clone(),
                desc: p.desc.clone(),
                version: p.version.clone(),
                author: p.author.clone(),
            })
            .collect()
    })
}
