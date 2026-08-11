//! egui 主界面（P0 占位版）。
//!
//! P0 目标：空窗口 + 中文渲染 + 显示既有配置信息，验证脚手架与字体管线。
//! 后续阶段将在此基础上实现排行/分组/趋势图等完整视图。

use std::sync::Arc;

use eframe::egui;

use crate::app::AppState;
use focusflow_core::config::{default_config, FocusFlowConfig};

/// Windows 系统常见中文字体候选（按优先级）。
const CJK_FONT_CANDIDATES: &[&str] = &[
    "C:\\Windows\\Fonts\\msyh.ttc",   // 微软雅黑
    "C:\\Windows\\Fonts\\msyh.ttf",
    "C:\\Windows\\Fonts\\simhei.ttf", // 黑体
    "C:\\Windows\\Fonts\\simsun.ttc", // 宋体
    "C:\\Windows\\Fonts\\deng.ttf",   // 等线
];

/// 加载第一个存在的中文字体文件；找不到时返回 None（此时中文可能显示为方块）。
fn load_cjk_font_bytes() -> Option<Vec<u8>> {
    CJK_FONT_CANDIDATES
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .and_then(|p| std::fs::read(p).ok())
}

/// 注入中文字体到 egui 字体系统（放在中文字符集的 fallback 链中）。
fn install_cjk_font(ctx: &egui::Context, font_name: &str, data: Vec<u8>) {
    let mut fonts = egui::FontDefinitions::default();
    fonts
        .font_data
        .insert(font_name.to_owned(), Arc::new(egui::FontData::from_owned(data)));
    // 挂到各个常用字族的 fallback 尾部，保证拉丁字符仍用默认字体
    for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
        fonts
            .families
            .entry(family)
            .or_default()
            .push(font_name.to_owned());
    }
    ctx.set_fonts(fonts);
}

/// 主应用。
pub struct FocusFlowApp {
    state: AppState,
    /// 是否已注入中文字体
    font_ready: bool,
}

impl FocusFlowApp {
    pub fn new(cc: &eframe::CreationContext<'_>) -> Self {
        let mut app = Self {
            state: AppState::new(),
            font_ready: false,
        };
        app.setup_fonts(cc);
        app
    }

    fn setup_fonts(&mut self, cc: &eframe::CreationContext<'_>) {
        if let Some(data) = load_cjk_font_bytes() {
            install_cjk_font(&cc.egui_ctx, "cjk_font", data.clone());
            tracing::info!("已加载中文字体 ({} bytes)", data.len());
            self.font_ready = true;
        } else {
            tracing::warn!("未找到系统中文字体，中文可能显示异常");
        }
    }

    fn show_config_panel(&self, ui: &mut egui::Ui) {
        let cfg: &FocusFlowConfig = self.state.config;
        ui.heading("FocusFlow - Rust 迁移脚手架 (P0)");
        ui.add_space(4.0);
        ui.label(format!(
            "版本 {} · 配置已从 config.ini 加载",
            focusflow_core::paths::APP_VERSION
        ));
        ui.add_space(8.0);
        ui.separator();
        ui.add_space(8.0);

        egui::CollapsingHeader::new("当前配置（config.ini）")
            .default_open(true)
            .show(ui, |ui| {
                let defaults = default_config();
                for section in [
                    "database",
                    "stats",
                    "listener",
                    "gui",
                    "hotkey",
                    "floating",
                    "tray",
                    "pomodoro",
                    "rest",
                ] {
                    let inner = &defaults[section];
                    ui.collapsing(section, |ui| {
                        let mut keys: Vec<&String> = inner.keys().collect();
                        keys.sort();
                        for key in keys {
                            ui.label(format!("{key} = {}", cfg.get(section, key)));
                        }
                    });
                }
            });
    }
}

impl eframe::App for FocusFlowApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        egui::CentralPanel::default()
            .frame(
                egui::Frame::default()
                    .fill(egui::Color32::from_rgb(0xF5, 0xF7, 0xFA))
                    .inner_margin(egui::Margin::same(16)),
            )
            .show(ui, |ui| {
                self.show_config_panel(ui);
                ui.add_space(12.0);
                ui.separator();
                ui.add_space(8.0);
                ui.horizontal(|ui| {
                    if !self.font_ready {
                        ui.colored_label(
                            egui::Color32::from_rgb(0xE5, 0x48, 0x4D),
                            "⚠ 中文字体未加载",
                        );
                    } else {
                        ui.colored_label(
                            egui::Color32::from_rgb(0x10, 0xB9, 0x81),
                            "✓ 中文字体渲染正常",
                        );
                    }
                });
            });
    }
}
