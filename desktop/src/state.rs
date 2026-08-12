//! 应用共享状态：数据库、监听器、统计快照与后台统计线程。

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{App, Manager};

use focusflow_core::config::FocusFlowConfig;
use focusflow_core::db::Database;
use focusflow_core::listener::InputListener;

/// 后台线程产出的统计数据（前端只读快照，UI 线程零 DB 查询）。
#[derive(Clone, Default, Serialize)]
pub struct SharedStats {
    pub today_count: i64,
    pub cpm: i64,
    pub period: i64, // -1=今日, 0=总计, N=天数
    pub total: i64,
    pub avg: i64,
    pub max_day: i64,
    pub rank: Vec<(String, i64)>,
    pub group: Vec<(String, i64)>,
    pub trend: Vec<(String, i64)>,
    pub trend30: Vec<(String, i64)>,
    pub hourly: Vec<i64>,
    pub weekday: Vec<(i64, i64)>,
}

/// 键鼠排行显示上限。
const RANK_LIMIT: usize = 100;

/// 应用状态（由 Tauri manage 持有）。
pub struct AppState {
    pub db: Arc<Database>,
    pub listener: Arc<InputListener>,
    pub config: &'static FocusFlowConfig,
    pub shared: Arc<Mutex<SharedStats>>,
    pub period: Arc<AtomicI64>,
    pub refresh_now: Arc<AtomicBool>,
    /// 主窗口是否启动即进托盘
    pub start_to_tray: bool,
}

impl AppState {
    /// 初始化：数据库、监听器、统计线程、托盘、热键、窗口可见性。
    pub fn init(app: &mut App) -> anyhow::Result<()> {
        let config = focusflow_core::config::instance();
        let db = focusflow_core::db::Database::init(config)?;
        // 每日维护：按配置自动 VACUUM（auto_vacuum_days 天一次）
        focusflow_core::db::maintenance::maybe_auto_vacuum(config.get_int("database", "auto_vacuum_days", 7));
        let listener = InputListener::new(config);
        listener.start(Arc::clone(&db));

        // 启动即加载插件（番茄钟/定时任务等随插件 init 运行，对齐 Python 版）
        crate::plugins::with_manager(&db, |_pm| {});

        let shared = Arc::new(Mutex::new(SharedStats::default()));
        // 默认周期 = 上次退出前选择的周期（前端切换时写入 gui.default_period）
        let default_period = config.get_int("gui", "default_period", -1);
        let period = Arc::new(AtomicI64::new(default_period));
        let refresh_now = Arc::new(AtomicBool::new(false));

        spawn_stats_worker(
            Arc::clone(&db),
            config,
            Arc::clone(&shared),
            Arc::clone(&period),
            Arc::clone(&refresh_now),
        );

        let start_to_tray = config.get_bool("gui", "start_to_tray", true);

        let state = Arc::new(AppState {
            db,
            listener,
            config,
            shared,
            period,
            refresh_now,
            start_to_tray,
        });

        // 悬浮窗默认位置（持久化）
        setup_windows(app, &state);

        // 悬浮窗周期重申置顶，防止被任务栏/其他置顶窗口遮挡
        keep_floating_on_top(app);

        app.manage(state);

        crate::tray::setup_tray(app)?;
        crate::hotkey::setup_hotkey(app);

        Ok(())
    }
}

/// 设置窗口初始可见性与悬浮窗位置。
fn setup_windows(app: &App, state: &AppState) {
    let config = focusflow_core::config::instance();

    // 主窗口：启动进托盘时隐藏
    if state.start_to_tray {
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.hide();
        }
    }

    // 悬浮窗：默认顶部靠右（对齐 Python 版），位置可持久化
    if let Some(win) = app.get_webview_window("floating") {
        let x = config.get_float("floating", "pos_x", f64::NAN);
        let y = config.get_float("floating", "pos_y", f64::NAN);
        if x.is_nan() || y.is_nan() {
            if let Ok(Some(mon)) = win.current_monitor() {
                let scale = win.scale_factor().unwrap_or(1.0);
                let w = mon.size().width as f64 / scale;
                let _ = win.set_position(tauri::LogicalPosition::new(w - 120.0, 60.0));
            }
        } else {
            let _ = win.set_position(tauri::LogicalPosition::new(x, y));
        }

        // 启动时按配置显示悬浮窗
        if config.get_bool("floating", "enabled", true) {
            let _ = win.show();
        }
    }
}

/// 悬浮窗周期重申置顶（对齐 Python 版方案）：
/// 任务栏本身是置顶窗口，鼠标指向时会把悬浮窗压到下面；
/// 每 500ms 用 SetWindowPos(HWND_TOPMOST) 把它重新抬到任务栏之上。
fn keep_floating_on_top(app: &App) {
    let Some(win) = app.get_webview_window("floating") else {
        return;
    };
    let Ok(hwnd) = win.hwnd() else {
        return;
    };
    // HWND 含裸指针不可跨线程，先转 isize 再在线程内还原
    let raw_hwnd = hwnd.0 as isize;
    std::thread::Builder::new()
        .name("floating-topmost".into())
        .spawn(move || loop {
            #[cfg(windows)]
            unsafe {
                use windows::Win32::Foundation::HWND;
                use windows::Win32::UI::WindowsAndMessaging::{
                    SetWindowPos, HWND_TOPMOST, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE,
                };
                let hwnd = HWND(raw_hwnd as *mut _);
                let _ = SetWindowPos(
                    hwnd,
                    Some(HWND_TOPMOST),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
                );
            }
            std::thread::sleep(Duration::from_millis(500));
        })
        .expect("启动悬浮窗置顶线程失败");
}

/// 按键分类（与 app 版一致）。
fn classify_key(key_name: &str) -> &'static str {
    if key_name.starts_with("滚轮") {
        return "滚轮";
    }
    if key_name.starts_with("鼠标") {
        return "鼠标点击";
    }
    if matches!(
        key_name,
        "Shift" | "左Shift" | "右Shift" | "Ctrl" | "左Ctrl" | "右Ctrl"
            | "Alt" | "左Alt" | "右Alt" | "Win" | "左Win" | "右Win"
    ) {
        return "修饰键";
    }
    if key_name.starts_with('F')
        && key_name.len() > 1
        && key_name[1..].chars().all(|c| c.is_ascii_digit())
    {
        return "功能键";
    }
    if key_name.len() == 1 && key_name.chars().next().unwrap().is_ascii_digit() {
        return "数字键";
    }
    if key_name.len() == 1 && key_name.chars().next().unwrap().is_ascii_alphabetic() {
        return "字母键";
    }
    if matches!(
        key_name,
        "空格" | "回车" | "退格" | "Tab" | "Esc" | "Delete" | "Insert"
            | "Home" | "End" | "PageUp" | "PageDown" | "↑" | "↓" | "←" | "→"
    ) {
        return "编辑键";
    }
    "其他"
}

/// 后台统计线程（与 app 版同逻辑）：
/// 快节奏 500ms 更新今日/CPM；重聚合在周期切换/超时/强制时执行。
fn spawn_stats_worker(
    db: Arc<Database>,
    config: &'static FocusFlowConfig,
    shared: Arc<Mutex<SharedStats>>,
    period: Arc<AtomicI64>,
    refresh_now: Arc<AtomicBool>,
) {
    std::thread::Builder::new()
        .name("stats-worker".into())
        .spawn(move || {
            tracing::info!("统计线程已启动");
            let mut prev_logged_today: i64 = -1;
            let idle_heavy_ms = (config.get_int("gui", "full_refresh_interval", 10).max(1) as u64) * 1000;
            let active_heavy_ms = 5_000u64;
            let tick_ms = 500u64;

            let mut h_total: i64 = 0;
            let mut h_avg: i64 = 0;
            let mut h_max: i64 = 0;
            let mut h_rank: Vec<(String, i64)> = Vec::new();
            let mut h_group: Vec<(String, i64)> = Vec::new();
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

                if do_heavy {
                    let (total, key_stats) = match period_val {
                        -1 => focusflow_core::db::get_stats_by_date(chrono::Local::now().date_naive()),
                        0 => focusflow_core::db::get_stats(None, None),
                        n => focusflow_core::db::get_stats(Some(n), None),
                    };
                    let mut rank: Vec<(String, i64)> =
                        key_stats.iter().map(|(k, v)| (k.clone(), *v)).collect();
                    rank.sort_by_key(|(_, v)| std::cmp::Reverse(*v));
                    rank.truncate(RANK_LIMIT);
                    let mut groups: std::collections::HashMap<&'static str, i64> =
                        std::collections::HashMap::new();
                    for (k, v) in &key_stats {
                        let g = classify_key(k);
                        *groups.entry(g).or_insert(0) += v;
                    }
                    let group: Vec<(String, i64)> = [
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
                    .filter_map(|g| groups.get(*g).map(|c| (g.to_string(), *c)))
                    .collect();
                    let daily_days = match period_val {
                        -1 => 1,
                        0 => 30,
                        n if n > 0 => n,
                        _ => 7,
                    };
                    let needed = daily_days.max(7).max(30);
                    let daily_all = focusflow_core::db::get_daily_counts(needed, None);
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
                    let wd = focusflow_core::db::queries::aggregate_weekday(&weekday_src);
                    let mut weekday: Vec<(i64, i64)> = wd.into_iter().collect();
                    weekday.sort_by_key(|(d, _)| *d);
                    let hourly = focusflow_core::db::queries::get_hourly_stats(None);

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

                let today_count = focusflow_core::db::get_today_count(db.writer().map(|w| w.as_ref()));
                let cpm = focusflow_core::stats::cpm(config).get_cpm();
                prev_today_count = today_count;

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

                if today_count != prev_logged_today {
                    tracing::info!("统计更新: today={today_count} cpm={cpm}");
                    prev_logged_today = today_count;
                }

                std::thread::sleep(Duration::from_millis(tick_ms));
            }
        })
        .expect("启动统计线程失败");
}
