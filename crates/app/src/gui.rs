//! egui 主界面。
//!
//! P2：集成托盘、全局热键、单实例、优雅退出。
//! - 持有 `egui::Context`，供托盘/热键回调控制窗口显隐
//! - 显示配置信息与运行状态（P3 完善完整统计界面）

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use eframe::egui;

use crate::hotkey::HotkeyManager;
use crate::tray::Tray;
use focusflow_core::config::FocusFlowConfig;

/// Windows 系统常见中文字体候选（按优先级）。
const CJK_FONT_CANDIDATES: &[&str] = &[
    "C:\\Windows\\Fonts\\msyh.ttc",   // 微软雅黑
    "C:\\Windows\\Fonts\\msyh.ttf",
    "C:\\Windows\\Fonts\\simhei.ttf", // 黑体
    "C:\\Windows\\Fonts\\simsun.ttc", // 宋体
    "C:\\Windows\\Fonts\\deng.ttf",   // 等线
];

/// 加载第一个存在的中文字体文件；找不到时返回 None。
fn load_cjk_font_bytes() -> Option<Vec<u8>> {
    CJK_FONT_CANDIDATES
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .and_then(|p| std::fs::read(p).ok())
}

/// 注入中文字体到 egui 字体系统。
fn install_cjk_font(ctx: &egui::Context, font_name: &str, data: Vec<u8>) {
    let mut fonts = egui::FontDefinitions::default();
    fonts
        .font_data
        .insert(font_name.to_owned(), Arc::new(egui::FontData::from_owned(data)));
    for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
        fonts
            .families
            .entry(family)
            .or_default()
            .push(font_name.to_owned());
    }
    ctx.set_fonts(fonts);
}

/// 应用句柄（托盘/热键回调通过它控制窗口）。
pub struct AppHandle {
    /// egui 上下文（Send + Sync，可跨线程调用 send_viewport_cmd）
    pub ctx: std::sync::Mutex<Option<egui::Context>>,
    /// 窗口是否可见
    pub visible: AtomicBool,
    /// 是否正在退出
    pub quitting: AtomicBool,
}

impl AppHandle {
    pub fn new() -> Self {
        Self {
            ctx: std::sync::Mutex::new(None),
            visible: AtomicBool::new(true),
            quitting: AtomicBool::new(false),
        }
    }

    /// 显示主窗口。
    pub fn show_window(&self) {
        self.visible.store(true, Ordering::SeqCst);
        if let Some(ctx) = self.ctx.lock().unwrap().as_ref() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
        }
    }

    /// 隐藏主窗口（最小化到托盘）。
    pub fn hide_window(&self) {
        self.visible.store(false, Ordering::SeqCst);
        if let Some(ctx) = self.ctx.lock().unwrap().as_ref() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }
    }

    /// 切换窗口显隐。
    pub fn toggle_window(&self) {
        if self.visible.load(Ordering::SeqCst) {
            self.hide_window();
        } else {
            self.show_window();
        }
    }

    /// 请求退出。
    pub fn request_quit(&self) {
        if self.quitting.swap(true, Ordering::SeqCst) {
            return;
        }
        if let Some(ctx) = self.ctx.lock().unwrap().as_ref() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    /// 设置 egui 上下文（GUI 初始化时调用一次）。
    pub fn set_ctx(&self, ctx: egui::Context) {
        *self.ctx.lock().unwrap() = Some(ctx);
    }
}

impl Default for AppHandle {
    fn default() -> Self {
        Self::new()
    }
}

/// 托盘回调适配器（将 Tray 菜单动作映射到 AppHandle + 监听器）。
struct TrayBridge {
    handle: Arc<AppHandle>,
    listener: Option<Arc<focusflow_core::listener::InputListener>>,
    #[allow(dead_code)]
    db: Option<Arc<focusflow_core::db::Database>>,
}

impl crate::tray::TrayCallbacks for TrayBridge {
    fn show_window(&self) {
        self.handle.show_window();
    }

    fn toggle_pause(&self) -> bool {
        if let Some(l) = &self.listener {
            l.toggle_pause()
        } else {
            false
        }
    }

    fn is_paused(&self) -> bool {
        self.listener.as_ref().map(|l| l.is_paused()).unwrap_or(false)
    }

    fn toggle_floating(&self) {
        // 悬浮窗在 P2 后阶段实现，此处占位
        tracing::debug!("切换悬浮窗（P2 暂未实现）");
    }

    fn is_floating_visible(&self) -> bool {
        false
    }

    fn request_quit(&self) {
        self.handle.request_quit();
    }
}

/// 热键回调适配器。
struct HotkeyBridge {
    handle: Arc<AppHandle>,
}

impl crate::hotkey::HotkeyCallback for HotkeyBridge {
    fn on_hotkey(&self) {
        self.handle.toggle_window();
    }
}

/// 主应用。
pub struct FocusFlowApp {
    config: &'static FocusFlowConfig,
    handle: Arc<AppHandle>,
    /// 数据库（main 传入）
    db: Arc<focusflow_core::db::Database>,
    /// 监听器（main 传入）
    listener: Arc<focusflow_core::listener::InputListener>,
    /// 是否已注入中文字体
    font_ready: bool,
    /// 托盘控制器
    _tray: Option<Tray>,
    /// 热键管理器
    _hotkey: Option<HotkeyManager>,
}

impl FocusFlowApp {
    pub fn new(
        cc: &eframe::CreationContext<'_>,
        handle: Arc<AppHandle>,
        db: Arc<focusflow_core::db::Database>,
        listener: Arc<focusflow_core::listener::InputListener>,
    ) -> Self {
        let mut app = Self {
            config: focusflow_core::config::instance(),
            handle: Arc::clone(&handle),
            db,
            listener,
            font_ready: false,
            _tray: None,
            _hotkey: None,
        };
        app.setup_fonts(cc);

        // 保存 egui context 供托盘/热键回调使用
        handle.set_ctx(cc.egui_ctx.clone());

        // 初始化系统集成（托盘/热键）
        app.init_system();
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

    /// 初始化托盘与热键（db/listener 已在 main 中启动）。
    fn init_system(&mut self) {
        let handle = Arc::clone(&self.handle);
        let listener = Arc::clone(&self.listener);
        let db = Arc::clone(&self.db);

        // 托盘
        let bridge = TrayBridge {
            handle: Arc::clone(&handle),
            listener: Some(listener),
            db: Some(db),
        };
        let mut tray = Tray::new(Arc::new(bridge));
        if let Err(e) = tray.start() {
            tracing::warn!("托盘启动失败: {e}");
        }
        self._tray = Some(tray);

        // 热键
        if self.config.get_bool("hotkey", "enabled", false) {
            let hotkey_str = self
                .config
                .get_or("hotkey", "toggle_window", "ctrl+shift+f");
            match HotkeyManager::new(Arc::new(HotkeyBridge {
                handle: Arc::clone(&handle),
            })) {
                Ok(mut hm) => {
                    hm.register(&hotkey_str);
                    hm.spawn_event_loop();
                    self._hotkey = Some(hm);
                }
                Err(e) => tracing::warn!("热键初始化失败: {e}"),
            }
        }
    }

    fn show_status_panel(&self, ui: &mut egui::Ui) {
        ui.heading("FocusFlow - Rust 迁移");
        ui.add_space(4.0);
        ui.label(format!("版本 {}", focusflow_core::paths::APP_VERSION));
        ui.add_space(8.0);
        ui.separator();
        ui.add_space(8.0);

        egui::Grid::new("status_grid")
            .num_columns(2)
            .spacing([12.0, 6.0])
            .show(ui, |ui| {
                ui.label("主题");
                ui.label(self.config.get("gui", "theme"));
                ui.end_row();

                ui.label("全局热键");
                if self.config.get_bool("hotkey", "enabled", false) {
                    ui.label(self.config.get_or("hotkey", "toggle_window", "ctrl+shift+f"));
                } else {
                    ui.label("（已关闭）");
                }
                ui.end_row();

                ui.label("窗口可见");
                ui.label(if self.handle.visible.load(Ordering::SeqCst) { "是" } else { "否" });
                ui.end_row();

                ui.label("中文字体");
                if self.font_ready {
                    ui.colored_label(egui::Color32::from_rgb(0x10, 0xB9, 0x81), "已加载");
                } else {
                    ui.colored_label(egui::Color32::from_rgb(0xE5, 0x48, 0x4D), "未加载");
                }
                ui.end_row();
            });

        ui.add_space(12.0);
        ui.separator();
        ui.add_space(8.0);
        ui.horizontal(|ui| {
            if ui.button("隐藏到托盘").clicked() {
                self.handle.hide_window();
            }
            if ui.button("显示统计").clicked() {
                self.handle.show_window();
            }
            if ui.button("退出").clicked() {
                self.handle.request_quit();
            }
        });
    }
}

impl eframe::App for FocusFlowApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        // 处理关闭请求：拦截 -> 最小化到托盘（除非正在退出）
        let ctx = ui.ctx().clone();
        if ctx.input(|i| i.viewport().close_requested()) && !self.handle.quitting.load(Ordering::SeqCst) {
            // 关闭窗口 = 最小化到托盘
            self.handle.hide_window();
            return;
        }

        egui::CentralPanel::default()
            .frame(
                egui::Frame::default()
                    .fill(egui::Color32::from_rgb(0xF5, 0xF7, 0xFA))
                    .inner_margin(egui::Margin::same(16)),
            )
            .show(ui, |ui| {
                self.show_status_panel(ui);
            });
    }
}
