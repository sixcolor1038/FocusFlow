//! FocusFlow Rust 版可执行程序。
//!
//! P2：系统集成——单实例、托盘、全局热键、优雅退出。
//! 主窗口仍为 eframe/egui（P3 完善界面）。

mod gui;
mod hotkey;
mod single_instance;
mod tray;

use std::sync::Arc;

use eframe::egui;

use focusflow_core::config::instance as cfg;
use focusflow_core::logger;

use crate::gui::AppHandle;

fn main() -> anyhow::Result<()> {
    // 初始化日志（幂等）
    logger::init_logging();

    // 单实例检查
    if !single_instance::check_single_instance() {
        // 已有实例：激活已有窗口后退出（P2 简化：仅提示）
        tracing::info!("已有 FocusFlow 实例在运行，退出本实例");
        std::process::exit(0);
    }

    // 触发配置加载
    let config = cfg();
    tracing::info!(
        "FocusFlow-rs v{} 启动 (theme={})",
        focusflow_core::paths::APP_VERSION,
        config.get("gui", "theme"),
    );

    // 调试模式：--listen-only 只启动监听与数据库（无 GUI），验证 rdev 集成
    if std::env::args().any(|a| a == "--listen-only") {
        tracing::info!("listen-only 调试模式：仅启动监听（无 GUI）");
        let db = focusflow_core::db::Database::init(config)?;
        let listener = focusflow_core::listener::InputListener::new(config);
        listener.start(db);
        tracing::info!("监听已启动，按 Ctrl+C 退出");
        loop {
            std::thread::sleep(std::time::Duration::from_secs(1));
        }
    }

    // 在 eframe 启动之前初始化数据库与监听（rdev 钩子先于 GUI 消息循环建立，
    // 避免 GUI 初始化后 rdev 失效）
    let db = focusflow_core::db::Database::init(config)?;
    let listener = focusflow_core::listener::InputListener::new(config);
    listener.start(Arc::clone(&db));
    tracing::info!("数据库与键鼠监听已初始化（GUI 启动前）");

    let handle = Arc::new(AppHandle::new());

    // 窗口图标
    let icon = eframe::icon_data::from_png_bytes(include_bytes!("../assets/focusflow.png"))
        .map(std::sync::Arc::new)
        .ok();

    let mut viewport = egui::ViewportBuilder::default()
        .with_title("FocusFlow - 效率追踪器")
        .with_inner_size([960.0, 720.0])
        .with_min_inner_size([640.0, 460.0]);
    if let Some(icon) = icon {
        viewport = viewport.with_icon(icon);
    }
    let options = eframe::NativeOptions {
        viewport,
        ..Default::default()
    };

    // 传入 handle、db、listener 给 GUI
    let app_handle = Arc::clone(&handle);
    let app_db = Arc::clone(&db);
    let app_listener = Arc::clone(&listener);
    eframe::run_native(
        "FocusFlow",
        options,
        Box::new(move |cc| {
            Ok(Box::new(gui::FocusFlowApp::new(
                cc,
                app_handle,
                app_db,
                app_listener,
            )))
        }),
    )
    .map_err(|e| anyhow::anyhow!("GUI 启动失败: {e}"))
}
