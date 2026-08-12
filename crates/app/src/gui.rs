//! egui 主界面。
//!
//! P3：完整统计界面——顶部导航 + 主卡片 + 视图切换（排行/分组/趋势/小时/星期）。
//! 集成托盘、全局热键、单实例、优雅退出。

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use eframe::egui;

use crate::floating::FloatingWindow;
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
    pub trend30: Vec<(String, i64)>,
    pub hourly: Vec<i64>,
    pub weekday: Vec<(i64, i64)>,
}

/// 键鼠排行显示上限（控制渲染与拷贝开销）。
const RANK_LIMIT: usize = 100;

/// 从配置 `[gui]` 读取列宽比例（"a,b,c,d"），解析失败时用默认值。
fn load_cols<const N: usize>(key: &str, default: [f32; N]) -> [f32; N] {
    let raw = focusflow_core::config::instance().get_or("gui", key, "");
    if raw.is_empty() {
        return default;
    }
    let parts: Vec<&str> = raw.split(',').map(|p| p.trim()).collect();
    if parts.len() != N {
        return default;
    }
    let mut out = default;
    for (i, p) in parts.iter().enumerate() {
        if let Ok(v) = p.parse::<f32>() {
            out[i] = v;
        }
    }
    out
}

/// 列宽比例序列化为 "a,b,c,d"。
fn cols_to_string(cols: &[f32]) -> String {
    cols.iter()
        .map(|f| format!("{f:.4}"))
        .collect::<Vec<_>>()
        .join(",")
}

/// 后台统计线程。
///
/// 两条节奏分离，解决"切换周期卡顿 / 主界面卡顿"：
/// - **快节奏**：每 500ms 只读内存（今日活跃、当前速度），开销可忽略。
/// - **慢节奏**：周期切换、或活跃时每 5s / 空闲每 `full_refresh_interval`s 才做一次
///   重聚合（排行/分组/趋势/小时/星期）。重聚合前先本地复用上次结果，期间 UI 零阻塞。
///
/// 所有 DB 查询都在本线程完成（UI 线程 read_shared 只做一次轻量拷贝）。
pub fn spawn_stats_worker(
    db: Arc<focusflow_core::db::Database>,
    config: &'static FocusFlowConfig,
    shared: Arc<Mutex<SharedStats>>,
    period: Arc<AtomicI64>,
    refresh_now: Arc<AtomicBool>,
) {
    std::thread::Builder::new()
        .name("stats-worker".into())
        .spawn(move || {
            use std::time::Instant;
            let idle_heavy_ms = (config.get_int("gui", "full_refresh_interval", 10).max(1) as u64) * 1000;
            let active_heavy_ms = 5_000u64;
            let tick_ms = 500u64;

            // 本地持有上一次重聚合结果：快节奏轮次直接复用，避免重复扫描 DB
            let mut h_total: i64 = 0;
            let mut h_avg: i64 = 0;
            let mut h_max: i64 = 0;
            let mut h_rank: Vec<(String, i64)> = Vec::new();
            let mut h_group: Vec<(&'static str, i64)> = Vec::new();
            let mut h_trend: Vec<(String, i64)> = Vec::new();
            let mut h_trend30: Vec<(String, i64)> = Vec::new();
            let mut h_hourly: Vec<i64> = vec![0; 24];
            let mut h_weekday: Vec<(i64, i64)> = Vec::new();

            let mut prev_period: i64 = i64::MIN;
            let mut last_heavy = Instant::now() - Duration::from_secs(3600);
            let mut prev_today_count: i64 = -1;

            loop {
                let period_val = period.load(Ordering::Relaxed);
                let forced = refresh_now.swap(false, Ordering::Relaxed);
                let period_changed = period_val != prev_period;
                let active = {
                    let c = db.writer().map(|w| w.today_count()).unwrap_or(0) as i64;
                    c != prev_today_count
                };
                let heavy_elapsed_ms = last_heavy.elapsed().as_millis() as u64;
                let heavy_interval_ms = if active { active_heavy_ms } else { idle_heavy_ms };
                let do_heavy = forced || period_changed || heavy_elapsed_ms >= heavy_interval_ms;

                // —— 慢节奏：重聚合（仅周期切换 / 超时 / 强制时执行）——
                if do_heavy {
                    // 先做完全部 DB 查询（不持共享锁），最后一次性写入。
                    let (total, key_stats) = match period_val {
                        -1 => db::get_stats_by_date(chrono::Local::now().date_naive()),
                        0 => db::get_stats(None, None),
                        n => db::get_stats(Some(n), None),
                    };
                    let mut rank: Vec<(String, i64)> =
                        key_stats.iter().map(|(k, v)| (k.clone(), *v)).collect();
                    rank.sort_by_key(|(_, v)| std::cmp::Reverse(*v));
                    rank.truncate(RANK_LIMIT);
                    let mut groups: HashMap<&'static str, i64> = HashMap::new();
                    for (k, v) in &key_stats {
                        let g = views::classify_key(k);
                        *groups.entry(g).or_insert(0) += v;
                    }
                    let group: Vec<(&'static str, i64)> = [
                        "字母键",
                        "数字键",
                        "功能键",
                        "修饰键",
                        "编辑键",
                        "鼠标点击",
                        "滚轮",
                        "其他",
                    ]
                    .iter()
                    .filter_map(|g| groups.get(*g).map(|c| (*g, *c)))
                    .collect();
                    // 日均/最高单日跟随所选周期；趋势固定近7天；星期固定近30天。
                    // 一次查询取覆盖所有需求的日数，按需切片，避免重复扫描。
                    let daily_days = match period_val {
                        -1 => 1, // 今日
                        0 => 30, // 总计：近30天日均/峰值
                        n if n > 0 => n,
                        _ => 7,
                    };
                    let needed = daily_days.max(7).max(30);
                    let daily_all = db::get_daily_counts(needed, None);
                    let total_days = daily_all.len() as usize;
                    let counts: Vec<i64> = if total_days >= daily_days as usize {
                        daily_all[total_days - daily_days as usize..]
                            .iter()
                            .map(|(_, c)| *c)
                            .collect()
                    } else {
                        Vec::new()
                    };
                    let avg = if counts.is_empty() {
                        0
                    } else {
                        counts.iter().sum::<i64>() / counts.len() as i64
                    };
                    let max_day = counts.iter().copied().max().unwrap_or(0);
                    let trend: Vec<(String, i64)> = if total_days >= 7 {
                        daily_all[total_days - 7..].to_vec()
                    } else {
                        daily_all.clone()
                    };
                    let trend30: Vec<(String, i64)> = if total_days >= 30 {
                        daily_all[total_days - 30..].to_vec()
                    } else {
                        daily_all.clone()
                    };
                    let weekday_src: Vec<(String, i64)> = if total_days >= 30 {
                        daily_all[total_days - 30..].to_vec()
                    } else {
                        daily_all.clone()
                    };
                    let wd = db::queries::aggregate_weekday(&weekday_src);
                    let mut weekday: Vec<(i64, i64)> = wd.into_iter().collect();
                    weekday.sort_by_key(|(d, _)| *d);
                    let hourly = db::queries::get_hourly_stats(None);

                    h_total = total;
                    h_avg = avg;
                    h_max = max_day;
                    h_rank = rank;
                    h_group = group;
                    h_trend = trend;
                    h_trend30 = trend30;
                    h_hourly = hourly;
                    h_weekday = weekday;
                    last_heavy = Instant::now();
                    prev_period = period_val;
                }

                // —— 快节奏：内存读取（今日活跃 / 当前速度）——
                let today_count = db::get_today_count(db.writer().map(|w| w.as_ref()));
                let cpm = focusflow_core::stats::cpm(config).get_cpm();
                prev_today_count = today_count;

                // 一次性加锁写入（锁持有时间极短，仅拷贝数据）
                {
                    let mut s = shared.lock().unwrap();
                    s.today_count = today_count;
                    s.cpm = cpm;
                    s.period = period_val;
                    s.total = h_total;
                    s.avg = h_avg;
                    s.max_day = h_max;
                    s.rank.clone_from(&h_rank);
                    s.group.clone_from(&h_group);
                    s.trend.clone_from(&h_trend);
                    s.trend30.clone_from(&h_trend30);
                    s.hourly.clone_from(&h_hourly);
                    s.weekday.clone_from(&h_weekday);
                }

                std::thread::sleep(Duration::from_millis(tick_ms));
            }
        })
        .expect("启动统计线程失败");
}

use std::collections::HashMap;

/// 按配置 `[gui] font` 返回中文字体候选（按优先级）。
/// 取值：hei=黑体(默认) / yahei=微软雅黑 / song=宋体 / kai=楷体 / dengxian=等线。
fn cjk_font_candidates() -> &'static [&'static str] {
    let name = focusflow_core::config::instance().get_or("gui", "font", "hei");
    match name.as_str() {
        "yahei" | "msyh" | "y" => &[
            "C:\\Windows\\Fonts\\msyh.ttc",
            "C:\\Windows\\Fonts\\msyh.ttf",
            "C:\\Windows\\Fonts\\simhei.ttf",
        ],
        "song" | "simsun" | "s" => &[
            "C:\\Windows\\Fonts\\simsun.ttc",
            "C:\\Windows\\Fonts\\simsunb.ttf",
            "C:\\Windows\\Fonts\\simhei.ttf",
        ],
        "kai" | "kaiti" | "k" => &[
            "C:\\Windows\\Fonts\\simkai.ttf",
            "C:\\Windows\\Fonts\\simhei.ttf",
        ],
        "dengxian" | "deng" | "d" => &[
            "C:\\Windows\\Fonts\\deng.ttf",
            "C:\\Windows\\Fonts\\simhei.ttf",
        ],
        _ => &[
            "C:\\Windows\\Fonts\\simhei.ttf", // 黑体（默认，笔画粗重更醒目）
            "C:\\Windows\\Fonts\\msyh.ttc",
        ],
    }
}

fn load_cjk_font_bytes() -> Option<Vec<u8>> {
    cjk_font_candidates()
        .iter()
        .find(|p| std::path::Path::new(p).exists())
        .and_then(|p| std::fs::read(p).ok())
}

/// Segoe UI 候选（Python 版主字体，中文由 CJK 字体回退）。
const SEGOE_FONT_CANDIDATES: &[&str] = &[
    "C:\\Windows\\Fonts\\segoeui.ttf",
    "C:\\Windows\\Fonts\\segoeui.ttc",
];

/// 加粗字体候选：Segoe UI Bold（拉丁）+ 微软雅黑 Bold（中文）。
/// egui 的 FontId 无字重字段，`.strong()` 无法加粗，只能注册粗体字体文件。
const BOLD_FONT_CANDIDATES: &[&str] = &[
    "C:\\Windows\\Fonts\\segoeuib.ttf",  // Segoe UI Bold
    "C:\\Windows\\Fonts\\msyhbd.ttc",    // 微软雅黑 Bold（中文粗体）
];

/// 安装 Segoe UI（主字体）+ CJK 回退（中文），对齐 Python 版观感。
fn install_cjk_font(ctx: &egui::Context, cjk_name: &str, cjk_data: Vec<u8>) {
    let mut fonts = egui::FontDefinitions::default();

    // 注册 CJK 回退字体（保持原始字形大小）
    fonts.font_data.insert(
        cjk_name.to_owned(),
        Arc::new(egui::FontData::from_owned(cjk_data)),
    );

    // 注册 Segoe UI 主字体（存在时优先，否则纯用 CJK）
    let segoe_name = "segoe_ui";
    if let Some(segoe_path) = SEGOE_FONT_CANDIDATES
        .iter()
        .find(|p| std::path::Path::new(p).exists())
    {
        if let Ok(data) = std::fs::read(segoe_path) {
            fonts.font_data.insert(
                segoe_name.to_owned(),
                Arc::new(egui::FontData::from_owned(data)),
            );
        }
    }

    // 家族列表：Segoe UI 在前（拉丁/数字用 Segoe UI），CJK 在后（中文回退）
    for family in [egui::FontFamily::Proportional, egui::FontFamily::Monospace] {
        let list = fonts.families.entry(family).or_default();
        if fonts.font_data.contains_key(segoe_name) {
            list.push(segoe_name.to_owned());
        }
        list.push(cjk_name.to_owned());
    }

    // 注册"加粗"字体家族：egui 无字重字段，用粗体字体文件实现真加粗
    let bold_family = egui::FontFamily::Name(crate::views::BOLD_FONT_FAMILY.into());
    let bold_list = fonts.families.entry(bold_family).or_default();
    for path in BOLD_FONT_CANDIDATES {
        if std::path::Path::new(path).exists() {
            if let Ok(data) = std::fs::read(path) {
                let name = format!("bold_{}", bold_list.len());
                fonts
                    .font_data
                    .insert(name.clone(), Arc::new(egui::FontData::from_owned(data)));
                bold_list.push(name);
            }
        }
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
    floating: Arc<FloatingWindow>,
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
        let show = !self.floating.is_visible();
        self.floating.set_visible(show);
        tracing::info!("悬浮窗已{}", if show { "显示" } else { "隐藏" });
    }
    fn is_floating_visible(&self) -> bool {
        self.floating.is_visible()
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
    /// 主题是否已应用到 egui（避免每帧 set_visuals 触发重排）
    theme_applied: bool,
    current_view: View,
    /// 后台线程共享统计数据
    shared: Arc<Mutex<SharedStats>>,
    /// 当前周期（后台线程读取）
    period: Arc<AtomicI64>,
    /// 强制刷新信号（周期切换时置 true，worker 立即响应）
    refresh_now: Arc<AtomicBool>,
    /// 插件管理器（GUI 线程专用）
    plugin_manager: Option<focusflow_core::plugins::manager::PluginManager>,
    /// 当前打开的插件窗口名
    open_plugin: Option<String>,
    /// 插件输入框缓冲：{plugin.field: 值}
    plugin_inputs: HashMap<String, String>,
    /// 导入旧数据结果提示
    import_result: Option<String>,
    /// 导入线程结果槽（后台线程写入，UI 每帧轮询）
    import_result_slot: Option<Arc<std::sync::Mutex<Option<String>>>>,
    /// 悬浮窗控制器
    floating: Arc<FloatingWindow>,
    /// 趋势图选中的天数（7/30）
    trend_days: i64,
    /// 排行表列宽比例（排名/键鼠/次数/占比，总和=1）
    rank_cols: [f32; 4],
    /// 分组表列宽比例（分组/次数/占比，总和=1）
    group_cols: [f32; 3],
    /// 已持久化的列宽（用于检测变化后写回配置）
    saved_rank_cols: [f32; 4],
    /// 已持久化的分组列宽
    saved_group_cols: [f32; 3],
    /// 列宽写盘节流时间戳
    last_cols_save: std::time::Instant,
    /// 启动即进托盘（eframe 首帧会强制显示窗口，需在启动前几帧补发隐藏）
    start_to_tray: bool,
    /// 已渲染帧数（用于补发隐藏）
    frames: u32,
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
        let refresh_now = Arc::new(AtomicBool::new(false));
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
            theme_applied: false,
            current_view: View::Rank,
            shared: Arc::clone(&shared),
            period: Arc::clone(&period),
            refresh_now: Arc::clone(&refresh_now),
            plugin_manager: None,
            open_plugin: None,
            plugin_inputs: HashMap::new(),
            import_result: None,
            import_result_slot: None,
            floating: Arc::new(FloatingWindow::new(
                Arc::clone(&shared),
                focusflow_core::config::instance(),
            )),
            trend_days: 7,
            rank_cols: load_cols("rank_cols", [0.14, 0.42, 0.22, 0.22]),
            group_cols: load_cols("group_cols", [0.50, 0.25, 0.25]),
            saved_rank_cols: [0.0; 4],
            saved_group_cols: [0.0; 3],
            last_cols_save: std::time::Instant::now(),
            start_to_tray: focusflow_core::config::instance().get_bool("gui", "start_to_tray", true),
            frames: 0,
        };
        app.saved_rank_cols = app.rank_cols;
        app.saved_group_cols = app.group_cols;
        // 双击悬浮窗 → 打开主窗口
        {
            let handle = Arc::clone(&app.handle);
            app.floating.set_open_callback(move || handle.show_window());
        }
        // 初始化插件管理器（加载 plugins/*.lua）
        let mut pm = focusflow_core::plugins::manager::PluginManager::new(
            app.config,
            Arc::clone(&app.db),
        );
        pm.load_all();
        pm.enable_hot_reload();
        app.plugin_manager = Some(pm);
        // 启动后台统计线程
        spawn_stats_worker(Arc::clone(&app.db), app.config, shared, period, refresh_now);
        app.setup_fonts(cc);
        handle.set_ctx(cc.egui_ctx.clone());
        app.init_system();
        // 启动时按配置显示悬浮窗
        if app.config.get_bool("floating", "enabled", false) {
            app.floating.set_visible(true);
        }
        app
    }

    fn setup_fonts(&mut self, cc: &eframe::CreationContext<'_>) {
        if let Some(data) = load_cjk_font_bytes() {
            install_cjk_font(&cc.egui_ctx, "cjk_font", data.clone());
            tracing::info!("已加载字体 (Segoe UI + CJK, {} bytes)", data.len());
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
            floating: Arc::clone(&self.floating),
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
        self.theme_applied = true;
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
        // 轮询导入线程结果
        self.poll_import_result();
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
            if ui.button("导入旧数据").clicked() {
                self.do_import_legacy();
            }
        });
        if let Some(result) = &self.import_result {
            ui.add_space(6.0);
            ui.label(
                egui::RichText::new(result)
                    .size(13.0)
                    .color(if result.contains("错误") || result.contains("失败") {
                        self.theme.danger
                    } else {
                        self.theme.success
                    }),
            );
        }

        ui.add_space(16.0);
        ui.label(egui::RichText::new(format!(
            "FocusFlow v{} · Rust 迁移版",
            focusflow_core::paths::APP_VERSION
        ))
        .color(self.theme.muted)
        .size(12.0));
    }

    /// 执行旧数据导入（选择目录 → 后台执行 → 更新结果）。
    fn do_import_legacy(&mut self) {
        let picked = rfd::FileDialog::new()
            .set_title("选择旧版 FocusFlow 数据目录（data 文件夹）")
            .pick_folder();
        let Some(dir) = picked else {
            return; // 用户取消
        };
        // 后台线程执行导入，结果写入槽供 UI 轮询
        let slot: Arc<std::sync::Mutex<Option<String>>> = Arc::new(std::sync::Mutex::new(None));
        let slot_thread = Arc::clone(&slot);
        let dir_for_thread = dir.clone();
        std::thread::Builder::new()
            .name("import-legacy".into())
            .spawn(move || {
                let summary = focusflow_core::migration::import_legacy_data(&dir_for_thread);
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
                if !summary.errors.is_empty() {
                    for e in &summary.errors {
                        lines.push(format!("错误: {e}"));
                    }
                }
                let text = lines.join("；");
                *slot_thread.lock().unwrap() = Some(text);
            })
            .expect("启动导入线程失败");
        self.import_result_slot = Some(slot);
        self.import_result = Some(format!("正在从 {} 导入旧数据...", dir.display()));
        // 触发后台统计刷新以反映导入数据
        self.db.flush(false);
        focusflow_core::db::queries::invalidate_years_cache();
    }

    /// 轮询导入线程结果（每帧调用）。
    fn poll_import_result(&mut self) {
        let slot = match self.import_result_slot.take() {
            Some(s) => s,
            None => return,
        };
        let done = {
            let mut guard = slot.lock().unwrap();
            guard.take()
        };
        match done {
            Some(result) => {
                self.import_result = Some(result);
                // 导入完成后：导入直接写库（绕过写线程），重建今日计数缓存保持一致
                if let Some(w) = self.db.writer() {
                    w.recompute_today_count();
                }
            }
            None => self.import_result_slot = Some(slot), // 未完成，下帧再查
        }
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
                    .corner_radius(16.0)
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
                    .corner_radius(16.0)
                    .inner_margin(egui::Margin::symmetric(16, 12))
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        stats.show(ui, &self.theme, self.config, &self.db, &self.period, &self.refresh_now);
                    });
                ui.add_space(8.0);

                // 视图切换
                egui::Frame::new()
                    .fill(self.theme.card_bg)
                    .corner_radius(16.0)
                    .inner_margin(egui::Margin::symmetric(16, 8))
                    .show(ui, |ui| {
                        ui.set_min_width(ui.available_width());
                        self.show_view_nav(ui);
                    });
                ui.add_space(8.0);

                // 内容区
                egui::Frame::new()
                    .fill(self.theme.card_bg)
                    .corner_radius(16.0)
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
                                rank.show(ui, &self.theme, &mut self.rank_cols);
                            }
                            View::Group => {
                                let group = GroupView {
                                    rows: s.group.clone(),
                                    total: s.total,
                                };
                                group.show(ui, &self.theme, &mut self.group_cols);
                            }
                            View::Trend => {
                                let mut trend = TrendView {
                                    days: self.trend_days,
                                };
                                trend.show(ui, &self.theme, &s.trend, &s.trend30);
                                self.trend_days = trend.days;
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
        let close_requested = ctx.input(|i| i.viewport().close_requested());
        let quitting = self.handle.quitting.load(Ordering::SeqCst);
        if close_requested && !quitting {
            // 点 X → 取消关闭，隐藏到托盘（托盘"退出程序"才会真正退出）
            ctx.send_viewport_cmd(egui::ViewportCommand::CancelClose);
            self.handle.hide_window();
            ctx.request_repaint_after(Duration::from_millis(500));
            return;
        }
        // quitting=true（托盘"退出程序"发起）时放行真正关闭

        // 启动即进托盘：eframe 首帧渲染后会强制显示窗口，这里在启动前几帧补发隐藏
        self.frames += 1;
        if self.start_to_tray && self.frames <= 4 {
            ctx.send_viewport_cmd(egui::ViewportCommand::Visible(false));
        }

        // 应用主题（仅在构造时 + 切换主题时调用；每帧调用会触发 egui 全量重排导致卡顿）
        if !self.theme_applied {
            self.theme.apply(&ctx);
            self.theme_applied = true;
        }

        egui::CentralPanel::default()
            .frame(
                egui::Frame::default()
                    .fill(self.theme.grad_bottom)
                    .inner_margin(egui::Margin::same(16)),
            )
            .show(ui, |ui| {
                // 页面渐变背景（对齐 Python 版液态玻璃），铺满整个面板避免露黑边
                let bg_rect = ui.max_rect().expand2(egui::vec2(16.0, 16.0));
                views::paint_vertical_gradient(
                    ui.painter(),
                    bg_rect,
                    self.theme.grad_top,
                    self.theme.grad_bottom,
                );
                self.show_content(ui, &ctx);
            });

        // 悬浮窗（需要显示时注册独立置顶视口）
        self.floating.show(&ctx);

        // 列宽变化后节流写盘，重启后保持（拖动期间最多每 2 秒写一次）
        if (self.rank_cols != self.saved_rank_cols || self.group_cols != self.saved_group_cols)
            && self.last_cols_save.elapsed() >= Duration::from_secs(2)
        {
            self.config
                .set("gui", "rank_cols", &cols_to_string(&self.rank_cols))
                .ok();
            self.config
                .set("gui", "group_cols", &cols_to_string(&self.group_cols))
                .ok();
            self.saved_rank_cols = self.rank_cols;
            self.saved_group_cols = self.group_cols;
            self.last_cols_save = std::time::Instant::now();
        }

        // 持续重绘（统计实时更新）：1 秒一次，减少常驻 GPU/CPU 开销避免卡顿
        ctx.request_repaint_after(Duration::from_millis(1000));
    }
}
