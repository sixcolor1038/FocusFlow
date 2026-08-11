//! egui 主界面。
//!
//! P3：完整统计界面——顶部导航 + 主卡片 + 视图切换（排行/分组/趋势/小时/星期）。
//! 集成托盘、全局热键、单实例、优雅退出。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use eframe::egui;

use crate::hotkey::HotkeyManager;
use crate::tray::Tray;
use crate::views::{self, GroupView, HourlyView, RankView, StatsPanel, Theme, TrendView, WeekdayView};
use focusflow_core::config::FocusFlowConfig;
use focusflow_core::db;

/// Windows 系统常见中文字体候选（按优先级）。
const CJK_FONT_CANDIDATES: &[&str] = &[
    "C:\\Windows\\Fonts\\msyh.ttc",   // 微软雅黑
    "C:\\Windows\\Fonts\\msyh.ttf",
    "C:\\Windows\\Fonts\\simhei.ttf", // 黑体
    "C:\\Windows\\Fonts\\simsun.ttc", // 宋体
    "C:\\Windows\\Fonts\\deng.ttf",   // 等线
];

fn load_cjk_font_bytes() -> Option<Vec<u8>> {
    CJK_FONT_CANDIDATES
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .and_then(|p| std::fs::read(p).ok())
}

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
    pub ctx: std::sync::Mutex<Option<egui::Context>>,
    pub visible: AtomicBool,
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

    pub fn show_window(&self) {
        self.visible.store(true, Ordering::SeqCst);
        if let Some(ctx) = self.ctx.lock().unwrap().as_ref() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(true));
        }
    }

    pub fn hide_window(&self) {
        self.visible.store(false, Ordering::SeqCst);
        if let Some(ctx) = self.ctx.lock().unwrap().as_ref() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }
    }

    pub fn toggle_window(&self) {
        if self.visible.load(Ordering::SeqCst) {
            self.hide_window();
        } else {
            self.show_window();
        }
    }

    pub fn request_quit(&self) {
        if self.quitting.swap(true, Ordering::SeqCst) {
            return;
        }
        if let Some(ctx) = self.ctx.lock().unwrap().as_ref() {
            ctx.send_viewport_cmd(egui::ViewportCommand::Close);
        }
    }

    pub fn set_ctx(&self, ctx: egui::Context) {
        *self.ctx.lock().unwrap() = Some(ctx);
    }
}

impl Default for AppHandle {
    fn default() -> Self {
        Self::new()
    }
}

/// 托盘回调适配器。
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
        self.listener.as_ref().map(|l| l.toggle_pause()).unwrap_or(false)
    }
    fn is_paused(&self) -> bool {
        self.listener.as_ref().map(|l| l.is_paused()).unwrap_or(false)
    }
    fn toggle_floating(&self) {
        tracing::debug!("切换悬浮窗（后续阶段实现）");
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
    db: Arc<focusflow_core::db::Database>,
    listener: Arc<focusflow_core::listener::InputListener>,
    font_ready: bool,
    _tray: Option<Tray>,
    _hotkey: Option<HotkeyManager>,

    // 视图状态
    theme: Theme,
    dark: bool,
    current_view: View,
    stats: StatsPanel,
    rank: RankView,
    group: GroupView,
    trend: TrendView,
    hourly: HourlyView,
    weekday: WeekdayView,
    last_refresh: Instant,
    last_incremental: Instant,
}

#[derive(Clone, Copy, PartialEq)]
enum View {
    Rank,
    Group,
    Trend,
    Hourly,
    Weekday,
    Settings,
}

impl FocusFlowApp {
    pub fn new(
        cc: &eframe::CreationContext<'_>,
        handle: Arc<AppHandle>,
        db: Arc<focusflow_core::db::Database>,
        listener: Arc<focusflow_core::listener::InputListener>,
    ) -> Self {
        let dark = focusflow_core::config::instance().get("gui", "theme") == "dark";
        let mut app = Self {
            config: focusflow_core::config::instance(),
            handle: Arc::clone(&handle),
            db,
            listener,
            font_ready: false,
            _tray: None,
            _hotkey: None,
            theme: if dark { Theme::dark() } else { Theme::light() },
            dark,
            current_view: View::Rank,
            stats: StatsPanel::default(),
            rank: RankView::default(),
            group: GroupView::default(),
            trend: TrendView::default(),
            hourly: HourlyView::default(),
            weekday: WeekdayView::default(),
            last_refresh: Instant::now(),
            last_incremental: Instant::now(),
        };
        app.setup_fonts(cc);
        handle.set_ctx(cc.egui_ctx.clone());
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

    fn init_system(&mut self) {
        let handle = Arc::clone(&self.handle);
        let listener = Arc::clone(&self.listener);
        let db = Arc::clone(&self.db);

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

    fn toggle_theme(&mut self, ctx: &egui::Context) {
        self.dark = !self.dark;
        self.theme = if self.dark { Theme::dark() } else { Theme::light() };
        self.config
            .set("gui", "theme", if self.dark { "dark" } else { "light" })
            .ok();
        self.theme.apply(ctx);
        ctx.request_repaint();
    }

    /// 增量刷新：今日计数 + CPM（轻量）。
    fn incremental_refresh(&mut self) {
        let now = Instant::now();
        if now.duration_since(self.last_incremental) < Duration::from_millis(1000) {
            return;
        }
        self.last_incremental = now;
        self.stats.today_count = db::get_today_count(self.db.writer().map(|w| w.as_ref()));
        self.stats.cpm = focusflow_core::stats::cpm(self.config).get_cpm();
    }

    /// 全量刷新：统计查询 + 当前视图数据。
    fn full_refresh(&mut self) {
        let now = Instant::now();
        if now.duration_since(self.last_refresh) < Duration::from_millis(2000) {
            return;
        }
        self.last_refresh = now;
        self.stats.refresh(self.config);

        let (total, key_stats) = match self.stats.period {
            views::Period::Today => db::get_stats_by_date(chrono::Local::now().date_naive()),
            views::Period::Days(d) => db::get_stats(Some(d), None),
            views::Period::Total => db::get_stats(None, None),
        };
        self.rank.total = total;
        self.rank.rows = key_stats
            .iter()
            .map(|(k, v)| (k.clone(), *v))
            .collect();
        self.rank.rows.sort_by_key(|(_, v)| std::cmp::Reverse(*v));
        self.group.update(&key_stats, total);
    }

    fn show_nav(&self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label(
                egui::RichText::new("FocusFlow")
                    .color(self.theme.accent)
                    .size(16.0)
                    .strong(),
            );
            ui.label(
                egui::RichText::new("效率追踪器")
                    .color(self.theme.muted)
                    .size(11.0),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("设置").clicked() {
                    // 切到设置视图
                }
            });
        });
    }

    fn show_view_nav(&mut self, ui: &mut egui::Ui) {
        ui.horizontal_wrapped(|ui| {
            for (view, label) in [
                (View::Rank, "键鼠排行"),
                (View::Group, "分组统计"),
                (View::Trend, "趋势图"),
                (View::Hourly, "小时分布"),
                (View::Weekday, "星期分布"),
                (View::Settings, "设置"),
            ] {
                if ui.selectable_label(self.current_view == view, label).clicked() {
                    self.current_view = view;
                    match view {
                        View::Trend => self.trend.refresh(),
                        View::Hourly => self.hourly.refresh(),
                        View::Weekday => self.weekday.refresh(),
                        View::Settings => {}
                        _ => self.full_refresh(),
                    }
                }
            }
        });
    }

    fn show_settings(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        ui.heading("设置");
        ui.add_space(8.0);
        ui.separator();
        ui.add_space(8.0);

        // 暗色模式
        let mut dark = self.dark;
        if ui.checkbox(&mut dark, "暗色模式").changed() && dark != self.dark {
            self.toggle_theme(ctx);
        }

        // 暂停记录
        let mut paused = self.listener.is_paused();
        if ui.checkbox(&mut paused, "暂停记录").changed() {
            self.listener.set_paused(paused);
        }

        // 全局热键开关
        let mut hotkey_enabled = self.config.get_bool("hotkey", "enabled", false);
        if ui.checkbox(&mut hotkey_enabled, "启用全局热键（显示/隐藏主窗口）").changed() {
            self.config.set("hotkey", "enabled", if hotkey_enabled { "true" } else { "false" }).ok();
        }
        // 热键组合（可编辑）
        ui.horizontal(|ui| {
            ui.label("热键组合:");
            let mut hotkey_str = self.config.get_or("hotkey", "toggle_window", "ctrl+shift+f");
            if ui.text_edit_singleline(&mut hotkey_str).changed() {
                self.config.set("hotkey", "toggle_window", &hotkey_str).ok();
            }
        });

        ui.add_space(8.0);
        ui.separator();
        ui.add_space(8.0);
        ui.heading("数据操作");
        ui.add_space(4.0);
        ui.horizontal(|ui| {
            if ui.button("导出数据").clicked() {
                // 占位
            }
            if ui.button("压缩数据库").clicked() {
                self.db.flush(true);
                focusflow_core::db::maintenance::vacuum_all();
            }
        });

        ui.add_space(12.0);
        ui.label(egui::RichText::new(format!(
            "FocusFlow v{} · Rust 迁移版",
            focusflow_core::paths::APP_VERSION
        ))
        .color(self.theme.muted)
        .size(11.0));
    }

    fn show_content(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        // 顶部导航
        egui::Frame::new()
            .fill(self.theme.card_bg)
            .corner_radius(10.0)
            .inner_margin(egui::Margin::symmetric(16, 10))
            .show(ui, |ui| {
                self.show_nav(ui);
            });
        ui.add_space(8.0);

        // 主卡片：核心数据
        egui::Frame::new()
            .fill(self.theme.card_bg)
            .corner_radius(10.0)
            .inner_margin(egui::Margin::symmetric(16, 12))
            .show(ui, |ui| {
                self.stats.show(ui, &self.theme, self.config);
            });
        ui.add_space(8.0);

        // 视图切换
        egui::Frame::new()
            .fill(self.theme.card_bg)
            .corner_radius(10.0)
            .inner_margin(egui::Margin::symmetric(16, 8))
            .show(ui, |ui| {
                self.show_view_nav(ui);
            });
        ui.add_space(8.0);

        // 内容区
        egui::Frame::new()
            .fill(self.theme.card_bg)
            .corner_radius(10.0)
            .inner_margin(egui::Margin::same(12))
            .show(ui, |ui| {
                egui::ScrollArea::vertical()
                    .auto_shrink([false, false])
                    .show(ui, |ui| {
                        match self.current_view {
                            View::Rank => self.rank.show(ui, &self.theme),
                            View::Group => self.group.show(ui),
                            View::Trend => self.trend.show(ui, &self.theme),
                            View::Hourly => self.hourly.show(ui, &self.theme),
                            View::Weekday => self.weekday.show(ui, &self.theme),
                            View::Settings => self.show_settings(ui, ctx),
                        }
                    });
            });
    }
}

impl eframe::App for FocusFlowApp {
    fn ui(&mut self, ui: &mut egui::Ui, _frame: &mut eframe::Frame) {
        let ctx = ui.ctx().clone();
        if ctx.input(|i| i.viewport().close_requested()) && !self.handle.quitting.load(Ordering::SeqCst) {
            self.handle.hide_window();
            return;
        }

        // 应用主题
        self.theme.apply(&ctx);

        // 定时刷新
        self.incremental_refresh();
        self.full_refresh();

        egui::CentralPanel::default()
            .frame(
                egui::Frame::default()
                    .fill(self.theme.bg)
                    .inner_margin(egui::Margin::same(16)),
            )
            .show(ui, |ui| {
                self.show_content(ui, &ctx);
            });

        // 持续重绘（统计实时更新）
        ctx.request_repaint_after(Duration::from_millis(1000));
    }
}
