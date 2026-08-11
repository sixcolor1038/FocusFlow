//! FocusFlow Rust 版可执行程序。
//!
//! P0 阶段：提供 eframe/egui 空窗口 + 中文渲染，读取既有 `config.ini`
//! 并显示关键配置，验证迁移脚手架。

mod app;
mod gui;

use eframe::egui;

use focusflow_core::config::instance as cfg;
use focusflow_core::logger;

fn main() -> anyhow::Result<()> {
    // 初始化日志（幂等）
    logger::init_logging();

    // 触发配置加载（读取既有 config.ini）
    let config = cfg();
    tracing::info!(
        "FocusFlow-rs v{} 启动 (theme={}, hotkey_enabled={})",
        focusflow_core::paths::APP_VERSION,
        config.get("gui", "theme"),
        config.get_bool("hotkey", "enabled", false),
    );

    // 窗口图标（读取失败不影响启动）
    let icon = eframe::icon_data::from_png_bytes(include_bytes!("../assets/focusflow.png"))
        .map(std::sync::Arc::new)
        .ok();

    // 配置 eframe 窗口
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

    eframe::run_native(
        "FocusFlow",
        options,
        Box::new(|cc| Ok(Box::new(gui::FocusFlowApp::new(cc)))),
    )
    .map_err(|e| anyhow::anyhow!("GUI 启动失败: {e}"))
}
