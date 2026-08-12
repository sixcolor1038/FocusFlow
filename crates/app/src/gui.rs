//! egui 主界面。
//!
//! P3：完整统计界面——顶部导航 + 主卡片 + 视图切换（排行/分组/趋势/小时/星期）。
//! 集成托盘、全局热键、单实例、优雅退出。

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use eframe::egui;

use crate::hotkey::HotkeyManager;
use crate::tray::Tray;
use crate::views::{self, GroupView, HourlyView, RankView, StatsPanel, Theme, TrendView, WeekdayView};
use focusflow_core::config::FocusFlowConfig;
use focusflow_core::db;

/// 后台线程产出的统计数据（UI 线程只读，避免 UI 线程做 DB 聚合查询）。
#[derive(Default, Clone)]
pub struct SharedStats {
    pub today_count: i64,
    pub cpm: i64,
    pub period: i64, // 周期：-1=今日, 0=总计, N=天数
    pub total: i64,
    pub avg: i64,
    pub max_day: i64,
    pub rank: Vec<(String, i64)>,
    pub group: Vec<(&'static str, i64)>,
    pub trend: Vec<(String, i64)>,
    pub hourly: Vec<i64>,
    pub weekday: Vec<(i64, i64)>,
}

/// 后台统计线程：每 2 秒查询一次 DB，写入共享数据。
pub fn spawn_stats_worker(
    db: Arc<focusflow_core::db::Database>,
    config: &'static FocusFlowConfig,
    shared: Arc<Mutex<SharedStats>>,
    period: Arc<AtomicI64>,
) {
    std::thread::Builder::new()
        .name("stats-worker".into())
        .spawn(move || {
            loop {
                let period_val = period.load(Ordering::Relaxed);

                // 先做完全部 DB 查询（不持共享锁），最后一次性写入。
                // 避免 UI 线程 read_shared 被长查询阻塞导致卡顿。
                let today_count = db::get_today_count(db.writer().map(|w| w.as_ref()));
                let cpm = focusflow_core::stats::cpm(config).get_cpm();
                let (total, key_stats) = match period_val {
                    -1 => db::get_stats_by_date(chrono::Local::now().date_naive()),
                    0 => db::get_stats(None, None),
                    n => db::get_stats(Some(n), None),
                };
                let mut rank: Vec<(String, i64)> = key_stats.iter().map(|(k, v)| (k.clone(), *v)).collect();
                rank.sort_by_key(|(_, v)| std::cmp::Reverse(*v));
                let mut groups: HashMap<&'static str, i64> = HashMap::new();
                for (k, v) in &key_stats {
                    let g = views::classify_key(k);
                    *groups.entry(g).or_insert(0) += v;
                }
                let group: Vec<(&'static str, i64)> = ["字母键", "数字键", "功能键", "修饰键", "编辑键", "鼠标点击", "滚轮", "其他"]
                    .iter()
                    .filter_map(|g| groups.get(*g).map(|c| (*g, *c)))
                    .collect();
                let daily = db::get_daily_counts(7, None);
                let counts: Vec<i64> = daily.iter().map(|(_, c)| *c).collect();
                let avg = if counts.is_empty() { 0 } else { counts.iter().sum::<i64>() / counts.len() as i64 };
                let max_day = counts.iter().copied().max().unwrap_or(0);
                let trend = db::get_daily_counts(7, None);
                let hourly = db::queries::get_hourly_stats(None);
                let wd = db::queries::get_weekday_stats(30);
                let mut weekday: Vec<(i64, i64)> = wd.into_iter().collect();
                weekday.sort_by_key(|(d, _)| *d);

                // 一次性加锁写入（锁持有时间极短，仅拷贝数据）
                {
                    let mut s = shared.lock().unwrap();
                    s.today_count = today_count;
                    s.cpm = cpm;
                    s.period = period_val;
                    s.total = total;
                    s.avg = avg;
                    s.max_day = max_day;
                    s.rank = rank;
                    s.group = group;
                    s.trend = trend;
                    s.hourly = hourly;
                    s.weekday = weekday;
                }

                std::thread::sleep(Duration::from_millis(2000));
            }
        })
        .expect("启动统计线程失败");
}

use std::collections::HashMap;

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
    /// 后台线程共享统计数据
    shared: Arc<Mutex<SharedStats>>,
    /// 当前周期（后台线程读取）
    period: Arc<AtomicI64>,
    /// 插件管理器（GUI 线程专用）
    plugin_manager: Option<focusflow_core::plugins::manager::PluginManager>,
    /// 当前打开的插件窗口名
    open_plugin: Option<String>,
    /// 插件输入框缓冲：{plugin.field: 值}
    plugin_inputs: HashMap<String, String>,
}

#[derive(Clone, Copy, PartialEq)]
enum View {
    Rank,
    Group,
    Trend,
    Hourly,
    Weekday,
    Plugins,
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
        let shared = Arc::new(Mutex::new(SharedStats::default()));
        let period = Arc::new(AtomicI64::new(-1)); // 默认今日
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
            shared: Arc::clone(&shared),
            period: Arc::clone(&period),
            plugin_manager: None,
            open_plugin: None,
            plugin_inputs: HashMap::new(),
        };
        // 初始化插件管理器（加载 plugins/*.lua）
        let mut pm = focusflow_core::plugins::manager::PluginManager::new(
            app.config,
            Arc::clone(&app.db),
        );
        pm.load_all();
        pm.enable_hot_reload();
        app.plugin_manager = Some(pm);
        // 启动后台统计线程
        spawn_stats_worker(Arc::clone(&app.db), app.config, shared, period);
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

    /// 从共享数据读取统计（后台线程已计算，UI 线程零 DB 查询）。
    fn read_shared(&self) -> SharedStats {
        self.shared.lock().unwrap().clone()
    }

    fn show_nav(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            ui.label(
                egui::RichText::new("FocusFlow")
                    .color(self.theme.accent)
                    .size(18.0)
                    .strong(),
            );
            ui.label(
                egui::RichText::new("效率追踪器")
                    .color(self.theme.muted)
                    .size(13.0),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button(egui::RichText::new("设置").size(14.0)).clicked() {
                    self.current_view = View::Settings;
                }
            });
        });
    }

    fn show_view_nav(&mut self, ui: &mut egui::Ui) {
        ui.horizontal(|ui| {
            for (view, label) in [
                (View::Rank, "键鼠排行"),
                (View::Group, "分组统计"),
                (View::Trend, "趋势图"),
                (View::Hourly, "小时分布"),
                (View::Weekday, "星期分布"),
                (View::Plugins, "插件管理"),
                (View::Settings, "设置"),
            ] {
                let selected = self.current_view == view;
                let btn = egui::Button::new(
                    egui::RichText::new(label)
                        .size(14.0)
                        .strong()
                        .color(if selected { egui::Color32::WHITE } else { self.theme.fg }),
                )
                .fill(if selected { self.theme.accent } else { egui::Color32::TRANSPARENT })
                .corner_radius(6.0)
                .min_size(egui::vec2(72.0, 30.0));
                if ui.add(btn).clicked() {
                    self.current_view = view;
                }
            }
        });
    }

    fn show_settings(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        ui.heading(egui::RichText::new("设置").size(20.0).strong());
        ui.add_space(12.0);
        ui.separator();
        ui.add_space(12.0);

        // 常规
        ui.label(egui::RichText::new("常规").size(15.0).strong().color(self.theme.accent));
        ui.add_space(6.0);
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

        ui.add_space(12.0);
        ui.separator();
        ui.add_space(12.0);

        // 全局热键
        ui.label(egui::RichText::new("全局热键").size(15.0).strong().color(self.theme.accent));
        ui.add_space(6.0);
        let mut hotkey_enabled = self.config.get_bool("hotkey", "enabled", false);
        if ui.checkbox(&mut hotkey_enabled, "启用全局热键（显示/隐藏主窗口）").changed() {
            self.config.set("hotkey", "enabled", if hotkey_enabled { "true" } else { "false" }).ok();
        }
        ui.horizontal(|ui| {
            ui.label(egui::RichText::new("热键组合:").size(14.0));
            let mut hotkey_str = self.config.get_or("hotkey", "toggle_window", "ctrl+shift+f");
            if ui.text_edit_singleline(&mut hotkey_str).changed() {
                self.config.set("hotkey", "toggle_window", &hotkey_str).ok();
            }
        });

        ui.add_space(12.0);
        ui.separator();
        ui.add_space(12.0);
        ui.label(egui::RichText::new("数据操作").size(15.0).strong().color(self.theme.accent));
        ui.add_space(6.0);
        ui.horizontal(|ui| {
            if ui.button("导出数据").clicked() {
                // 占位
            }
            if ui.button("压缩数据库").clicked() {
                self.db.flush(true);
                focusflow_core::db::maintenance::vacuum_all();
            }
        });

        ui.add_space(16.0);
        ui.label(egui::RichText::new(format!(
            "FocusFlow v{} · Rust 迁移版",
            focusflow_core::paths::APP_VERSION
        ))
        .color(self.theme.muted)
        .size(12.0));
    }

    fn show_content(&mut self, ui: &mut egui::Ui, ctx: &egui::Context) {
        // 从后台线程快照统计数据（UI 线程不做 DB 查询）
        let s = self.read_shared();

        // 整体垂直滚动：任何窗口大小下都能查看全部内容
        egui::ScrollArea::vertical()
            .auto_shrink([false, false])
            .show(ui, |ui| {
                ui.set_min_width(ui.available_width());

                // 顶部导航
                egui::Frame::new()
                    .fill(self.theme.card_bg)
                    .corner_radius(10.0)
                    .inner_margin(egui::Margin::symmetric(16, 10))
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        self.show_nav(ui);
                    });
                ui.add_space(8.0);

                // 主卡片：核心数据
                let mut stats = StatsPanel {
                    today_count: s.today_count,
                    cpm: s.cpm,
                    period: match s.period {
                        -1 => views::Period::Today,
                        0 => views::Period::Total,
                        n => views::Period::Days(n),
                    },
                    total: s.total,
                    avg: s.avg,
                    max_day: s.max_day,
                };
                egui::Frame::new()
                    .fill(self.theme.card_bg)
                    .corner_radius(10.0)
                    .inner_margin(egui::Margin::symmetric(16, 12))
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        stats.show(ui, &self.theme, self.config, &self.db, &self.period);
                    });
                ui.add_space(8.0);

                // 视图切换
                egui::Frame::new()
                    .fill(self.theme.card_bg)
                    .corner_radius(10.0)
                    .inner_margin(egui::Margin::symmetric(16, 8))
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        self.show_view_nav(ui);
                    });
                ui.add_space(8.0);

                // 内容区
                egui::Frame::new()
                    .fill(self.theme.card_bg)
                    .corner_radius(10.0)
                    .inner_margin(egui::Margin::same(12))
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        match self.current_view {
                            View::Rank => {
                                let mut rank = RankView {
                                    rows: s.rank.clone(),
                                    total: s.total,
                                    clear_key: None,
                                };
                                rank.show(ui, &self.theme);
                            }
                            View::Group => {
                                let group = GroupView {
                                    rows: s.group.clone(),
                                    total: s.total,
                                };
                                group.show(ui, &self.theme);
                            }
                            View::Trend => {
                                let mut trend = TrendView {
                                    days: 7,
                                    data: s.trend.clone(),
                                };
                                trend.show(ui, &self.theme);
                            }
                            View::Hourly => {
                                let mut hourly = HourlyView { data: s.hourly.clone() };
                                hourly.show(ui, &self.theme);
                            }
                            View::Weekday => {
                                let weekday_map: HashMap<i64, i64> = s.weekday.iter().copied().collect();
                                let mut weekday = WeekdayView { data: weekday_map };
                                weekday.show(ui, &self.theme);
                            }
                            View::Plugins => self.show_plugins_view(ui, ctx),
                            View::Settings => self.show_settings(ui, ctx),
                        }
                    });
            });
    }

    /// 插件管理视图：列出插件 + 打开插件窗口。
    fn show_plugins_view(&mut self, ui: &mut egui::Ui, _ctx: &egui::Context) {
        ui.heading(egui::RichText::new("插件管理").size(20.0).strong());
        ui.add_space(8.0);
        ui.label(
            egui::RichText::new("插件位于 plugins/ 目录（*.lua），修改文件后自动热重载")
                .color(self.theme.muted)
                .size(13.0),
        );
        ui.add_space(8.0);
        ui.separator();
        ui.add_space(8.0);

        // 处理热重载请求
        if let Some(pm) = &mut self.plugin_manager {
            let reloaded = pm.poll_reload_requests();
            if !reloaded.is_empty() {
                tracing::info!("插件已热重载: {reloaded:?}");
            }
        }

        let mut open_name: Option<String> = None;
        let mut unload_name: Option<String> = None;
        let plugins = self
            .plugin_manager
            .as_ref()
            .map(|pm| pm.get_all_plugins())
            .unwrap_or_default();

        use egui_extras::{Column, TableBuilder};
        TableBuilder::new(ui)
            .striped(true)
            .cell_layout(egui::Layout::centered_and_justified(egui::Direction::LeftToRight))
            .column(Column::auto().at_least(120.0).clip(true))
            .column(Column::auto().at_least(200.0).clip(true))
            .column(Column::auto().at_least(70.0).clip(true))
            .column(Column::auto().at_least(80.0).clip(true))
            .header(26.0, |mut header| {
                header.col(|ui| { ui.strong("名称"); });
                header.col(|ui| { ui.strong("描述"); });
                header.col(|ui| { ui.strong("版本"); });
                header.col(|ui| { ui.strong("操作"); });
            })
            .body(|mut body| {
                for info in &plugins {
                    body.row(34.0, |mut row| {
                        row.col(|ui| { ui.label(egui::RichText::new(&info.name).size(14.0).strong()); });
                        row.col(|ui| { ui.label(egui::RichText::new(&info.desc).size(13.0).color(self.theme.muted)); });
                        row.col(|ui| { ui.label(&info.version); });
                        row.col(|ui| {
                            ui.horizontal(|ui| {
                                if ui.button("打开").clicked() {
                                    open_name = Some(info.name.clone());
                                }
                                if ui.button("卸载").clicked() {
                                    unload_name = Some(info.name.clone());
                                }
                            });
                        });
                    });
                }
            });

        if let Some(name) = unload_name {
            if let Some(pm) = &mut self.plugin_manager {
                pm.unload_plugin(&name);
            }
        }
        if let Some(name) = open_name {
            self.open_plugin = Some(name);
        }

        // 打开插件窗口（模态）
        if let Some(name) = self.open_plugin.clone() {
            self.show_plugin_window(ui, &name);
        }
    }

    /// 渲染插件窗口内容。
    fn show_plugin_window(&mut self, ui: &mut egui::Ui, name: &str) {
        let Some(pm) = &self.plugin_manager else {
            return;
        };
        let Some(info) = pm.get_plugin(name) else {
            self.open_plugin = None;
            return;
        };
        let Some(view) = &info.view else {
            ui.label(egui::RichText::new("插件未提供视图").size(14.0));
            return;
        };

        // 窗口头
        ui.horizontal(|ui| {
            ui.label(egui::RichText::new(&info.name).size(18.0).strong());
            ui.label(
                egui::RichText::new(format!("v{} · {}", info.version, info.author))
                    .color(self.theme.muted)
                    .size(13.0),
            );
            ui.with_layout(egui::Layout::right_to_left(egui::Align::Center), |ui| {
                if ui.button("关闭").clicked() {
                    self.open_plugin = None;
                }
            });
        });
        ui.separator();
        ui.add_space(6.0);

        // 渲染视图，按钮触发 on_action，输入框触发 set_field
        let mut on_button = |id: &str| {
            if let Some(pm) = &self.plugin_manager {
                if let Err(e) = pm.plugin_action(name, id) {
                    tracing::warn!("插件动作失败: {e}");
                }
            }
        };
        let mut on_field = |field: &str, value: &str| {
            if let Some(pm) = &self.plugin_manager {
                if let Err(e) = pm.plugin_set_field(name, field, value) {
                    tracing::debug!("插件 set_field 失败: {e}");
                }
            }
        };
        views::show_plugin_view(ui, view, &self.theme, &mut on_button, &mut on_field, &mut self.plugin_inputs);
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
