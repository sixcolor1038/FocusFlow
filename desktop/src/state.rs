//! 应用共享状态：数据库、监听器、统计快照与后台统计线程。

use std::sync::atomic::{AtomicBool, AtomicI64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use tauri::{App, AppHandle, Emitter, Manager};

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
    /// 最高单日对应的日期（YYYY-MM-DD）
    pub max_day_date: String,
    pub rank: Vec<(String, i64)>,
    pub group: Vec<(String, i64)>,
    pub trend: Vec<(String, i64)>,
    pub trend30: Vec<(String, i64)>,
    pub hourly: Vec<i64>,
    pub weekday: Vec<(i64, i64)>,
}

/// 轻量实时数据（500ms 变化，事件 `stats-live` 推送）。
#[derive(Clone, Serialize)]
pub struct LiveStats {
    pub today_count: i64,
    pub cpm: i64,
    pub period: i64, // -1=今日, 0=总计, N=天数
    /// 当前周期最高单日（今日破纪录时随快节奏即时更新）
    pub max_day: i64,
    /// 最高单日对应的日期（YYYY-MM-DD）
    pub max_day_date: String,
}

/// 重量级图表数据（周期切换 / 定时重聚合，事件 `stats-charts` 推送）。
#[derive(Clone, Serialize)]
pub struct ChartsStats {
    pub total: i64,
    pub avg: i64,
    pub max_day: i64,
    /// 最高单日对应的日期（YYYY-MM-DD）
    pub max_day_date: String,
    pub period: i64,
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
        // 插件热重载（Tauri 无 GUI 轮询循环，用独立扫描线程 + 主线程重载）
        crate::plugins::start_hot_reload(app.handle(), Arc::clone(&db));

        let shared = Arc::new(Mutex::new(SharedStats::default()));
        // 默认周期 = 上次退出前选择的周期（前端切换时写入 gui.default_period）
        let default_period = config.get_int("gui", "default_period", -1);
        let period = Arc::new(AtomicI64::new(default_period));
        let refresh_now = Arc::new(AtomicBool::new(false));

        spawn_stats_worker(
            app.handle().clone(),
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

/// 悬浮窗改为工具窗口（对齐 Python 版 WS_EX_TOOLWINDOW）：
/// 任务管理器把"只有工具窗口可见"的进程归类为后台进程，而非应用。
/// 注意：tauri 的 skipTaskbar 只调用 TaskbarList::DeleteTab 去掉任务栏按钮，
/// 并不会设置 WS_EX_TOOLWINDOW，所以需要手动加扩展样式。
fn make_floating_tool_window(win: &tauri::WebviewWindow) {
    #[cfg(windows)]
    {
        let Ok(hwnd) = win.hwnd() else {
            return;
        };
        unsafe {
            use windows::Win32::Foundation::HWND;
            use windows::Win32::UI::WindowsAndMessaging::{
                GetWindowLongW, SetWindowLongW, SetWindowPos, GWL_EXSTYLE, HWND_TOPMOST,
                SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, WS_EX_APPWINDOW,
                WS_EX_TOOLWINDOW,
            };
            let hwnd = HWND(hwnd.0 as *mut _);
            let style = GetWindowLongW(hwnd, GWL_EXSTYLE);
            // 置 TOOLWINDOW 并清 APPWINDOW：任务管理器据此归类为后台进程
            let want = (style | WS_EX_TOOLWINDOW.0 as i32) & !WS_EX_APPWINDOW.0 as i32;
            if style != want {
                SetWindowLongW(hwnd, GWL_EXSTYLE, want);
            }
            let _ = SetWindowPos(
                hwnd,
                Some(HWND_TOPMOST),
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
            );
        }
    }
}

/// 悬浮窗目标尺寸（逻辑像素）。
/// 注意：WebView2 在创建控制器时会把窗口强制拉宽到至少 120px，
/// 导致 tauri.conf.json 里小于 120 的宽度被静默放大、右侧出现大片空白；
/// 这里在窗口+WebView 构建完成后主动 set_size 缩回目标尺寸。
/// 尺寸可在 config.ini 的 [floating] 段用 width/height 覆盖（免重编译）。
fn enforce_floating_size(win: &tauri::WebviewWindow, config: &FocusFlowConfig) {
    let w = config.get_float("floating", "width", 90.0);
    let h = config.get_float("floating", "height", 46.0);
    let _ = win.set_size(tauri::LogicalSize::new(w, h));
    // WebView2 控制器是异步创建的，若放大发生在 setup 之后需要兜底；
    // 启动后 4 秒内每秒重申一次（窗口不可手动缩放，不会与用户冲突）。
    let handle = win.app_handle().clone();
    std::thread::Builder::new()
        .name("floating-size-enforce".into())
        .spawn(move || {
            for _ in 0..4 {
                std::thread::sleep(Duration::from_secs(1));
                if let Some(win_h) = handle.get_webview_window("floating") {
                    let _ = win_h.set_size(tauri::LogicalSize::new(w, h));
                }
            }
        })
        .expect("启动悬浮窗尺寸修正线程失败");
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
        make_floating_tool_window(&win);
        // WebView2 创建控制器时会把窗口强制放宽到至少 120px（内容实际只需 ~81px），
        // 因此在窗口构建完成后主动缩回目标宽度，并延时重复几次兜底异步放大。
        enforce_floating_size(&win, &config);
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

    // 启动即按当前活跃状态设置悬浮窗内存档位（启动进托盘=非活跃=Low）。
    // 注意：WebView2 控制器是异步创建的，setup 阶段直接调用会静默失败，
    // 因此后台线程重试直到控制器就绪（每次重试前重新判断主窗口可见性，
    // 用户可能已打开主界面，此时应保持 Normal）。
    let handle = app.handle().clone();
    std::thread::Builder::new()
        .name("mem-level-init".into())
        .spawn(move || {
            // 冷启动 WebView2 环境创建可能较慢，最多重试 60 秒
            for i in 0..60 {
                let main_visible = handle
                    .get_webview_window("main")
                    .map(|w| w.is_visible().unwrap_or(false))
                    .unwrap_or(false);
                if set_floating_memory_level(&handle, !main_visible) {
                    tracing::info!(
                        "WebView2 内存档位已设置 (low={})，重试 {} 次",
                        !main_visible,
                        i
                    );
                    return;
                }
                std::thread::sleep(Duration::from_secs(1));
            }
            tracing::warn!("WebView2 内存档位设置失败：控制器长时间未就绪");
        })
        .expect("启动内存档位初始化线程失败");
}

/// 设置各窗口 WebView2 内存档位：
/// 应用活跃（主窗口可见）→ Normal；仅托盘/悬浮窗（非活跃）→ Low。
/// WebView2 官方 MemoryUsageTargetLevel API，非活跃时设 Low 可显著降低内存占用。
/// 主窗口虽隐藏但其页面仍在运行，同样要降档才能把内存压下来。
/// 返回是否全部设置成功（WebView2 控制器未就绪时返回 false，调用方可重试）。
pub fn set_floating_memory_level(app: &tauri::AppHandle, low: bool) -> bool {
    let mut all_ok = true;
    for label in ["main", "floating"] {
        if let Some(win) = app.get_webview_window(label) {
            let low = low;
            // with_webview 闭包无返回值，用共享标志记录是否真正设置成功
            let done = Arc::new(AtomicBool::new(false));
            let done_cb = Arc::clone(&done);
            let result = win.with_webview(move |webview| {
                #[cfg(windows)]
                unsafe {
                    use webview2_com::Microsoft::Web::WebView2::Win32::{
                        ICoreWebView2_19, COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW,
                        COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_NORMAL,
                    };
                    use windows::core::Interface;
                    let controller = webview.controller();
                    if let Ok(core) = controller.CoreWebView2() {
                        if let Ok(v19) = core.cast::<ICoreWebView2_19>() {
                            let level = if low {
                                COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_LOW
                            } else {
                                COREWEBVIEW2_MEMORY_USAGE_TARGET_LEVEL_NORMAL
                            };
                            done_cb.store(
                                v19.SetMemoryUsageTargetLevel(level).is_ok(),
                                Ordering::SeqCst,
                            );
                        } else {
                            tracing::warn!("WebView2 运行时过旧，不支持内存档位 API（需 1.0.2390+）");
                        }
                    }
                    // CoreWebView2 未就绪：静默，等待调用方重试
                }
                #[cfg(not(windows))]
                {
                    let _ = low;
                    done_cb.store(true, Ordering::SeqCst);
                }
            });
            all_ok = all_ok && result.is_ok() && done.load(Ordering::SeqCst);
        }
    }
    all_ok
}

/// 设置 WebView2 控制器可见性（IsVisible）。
/// 窗口隐藏时 WebView2 控制器并不知道自身不可见，仍会维持渲染合成管线；
/// 调用 put_IsVisible(false) 可停止渲染、进一步释放内存与 CPU（WebView2 官方建议）。
/// 显示窗口前必须先恢复 true，否则内容不会重绘。
pub fn set_webview_rendering(app: &tauri::AppHandle, label: &str, visible: bool) {
    if let Some(win) = app.get_webview_window(label) {
        let _ = win.with_webview(move |webview| {
            #[cfg(windows)]
            unsafe {
                let controller = webview.controller();
                let _ = controller.SetIsVisible(visible);
            }
            #[cfg(not(windows))]
            let _ = visible;
        });
    }
}

/// 显示主窗口（不存在则重建，对齐 tauri.conf.json 的 main 窗口配置；
/// 正常流程窗口常驻，重建仅作兜底）。
pub fn show_main_window(app: &tauri::AppHandle) {
    if app.get_webview_window("main").is_none() {
        tracing::warn!("show_main: main 窗口不存在，重建");
        let result = tauri::WebviewWindowBuilder::new(
            app,
            "main",
            tauri::WebviewUrl::App("index.html".into()),
        )
        .title("FocusFlow - 效率追踪器")
        .inner_size(1100.0, 760.0)
        .min_inner_size(820.0, 560.0)
        .resizable(true)
        .visible(false)
        .build();
        match &result {
            Ok(_) => tracing::info!("show_main: 重建成功"),
            Err(e) => tracing::error!("show_main: 重建失败: {e}"),
        }
    }
    if let Some(win) = app.get_webview_window("main") {
        // 恢复 WebView2 渲染（隐藏时已停用），再显示窗口
        set_webview_rendering(app, "main", true);
        // 恢复任务栏按钮（隐藏时切走过）
        let _ = win.set_skip_taskbar(false);
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
    // 主窗口打开：立即触发一次重聚合，图表数据马上刷新（隐藏期间重聚合已停用）
    if let Some(state) = app.try_state::<Arc<AppState>>() {
        state.refresh_now.store(true, Ordering::Relaxed);
    }
    // 应用进入活跃状态：悬浮窗回到 Normal 内存档位
    set_floating_memory_level(app, false);
}

/// 隐藏主窗口（到托盘）：仅隐藏，不销毁。
///
/// 说明：WebView2 在同一进程内"销毁后重建控制器"不可靠（0x8007139F，
/// 与是否先 Close() 无关），所以放弃销毁方案，窗口常驻内存、显示即恢复。
/// 任务管理器重新分类用任务栏注册表项切换触发（见 hide_main_window）。
pub fn hide_main_window(app: &tauri::AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.hide();
        // 停止 WebView2 渲染合成，释放渲染管线内存（窗口仍常驻，显示时再恢复）
        set_webview_rendering(app, "main", false);
        // 促使任务管理器重新评估"应用/后台进程"：隐藏窗口不会触发窗口销毁
        // 通知，TM 不会重新分类；AddTab/DeleteTab 产生 shell 事件，
        // 让 TM 重新枚举（窗口已隐藏，切换无视觉影响）。
        let _ = win.set_skip_taskbar(false);
        let _ = win.set_skip_taskbar(true);
    }
    // 应用转入非活跃状态：悬浮窗降为 Low 内存档位
    set_floating_memory_level(app, true);
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
                    GetWindowLongW, SetWindowLongW, SetWindowPos, GWL_EXSTYLE, HWND_TOPMOST,
                    SWP_FRAMECHANGED, SWP_NOACTIVATE, SWP_NOMOVE, SWP_NOSIZE, WS_EX_APPWINDOW,
                    WS_EX_TOOLWINDOW,
                };
                let hwnd = HWND(raw_hwnd as *mut _);
                // 工具窗口样式：tao 的 skip_taskbar 只做 DeleteTab，仍会带 WS_EX_APPWINDOW，
                // 任务管理器会把它当"应用"；这里每轮重申：置 TOOLWINDOW、清 APPWINDOW，
                // 进程即可归类为后台进程（对齐 Python 版方案）。
                let style = GetWindowLongW(hwnd, GWL_EXSTYLE);
                let want = (style | WS_EX_TOOLWINDOW.0 as i32) & !WS_EX_APPWINDOW.0 as i32;
                if style != want {
                    SetWindowLongW(hwnd, GWL_EXSTYLE, want);
                    let _ = SetWindowPos(
                        hwnd,
                        Some(HWND_TOPMOST),
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                    );
                }
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
/// 快节奏 500ms 更新今日/CPM 并推送 `stats-live`；
/// 重聚合在周期切换/超时/强制时执行并推送 `stats-charts`。
fn spawn_stats_worker(
    app: AppHandle,
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
            // "统计更新"日志限频时间戳（打字时计数每秒变化，避免刷屏）
            let mut last_stats_log = Instant::now() - Duration::from_secs(61);
            let tick_ms = 500u64;

            let mut prev_period: i64 = i64::MIN;
            let mut last_heavy = Instant::now() - Duration::from_secs(3600);
            let mut prev_today_count: i64 = -1;
            let mut prev_cpm: i64 = -1;
            // 上次重聚合时的今日计数：空闲且数据未变时跳过重聚合，避免无谓的整库查询
            let mut last_heavy_today: i64 = -1;
            // 各周期最高单日缓存：period -> (次数, 日期)；重聚合播种，今日破纪录时快节奏即时更新
            let mut period_max: std::collections::HashMap<i64, (i64, String)> =
                std::collections::HashMap::new();

            loop {
                let period_val = period.load(Ordering::Relaxed);
                let forced = refresh_now.swap(false, Ordering::Relaxed);
                let period_changed = period_val != prev_period;
                let cur_today = db.writer().map(|w| w.today_count()).unwrap_or(0) as i64;
                let active = cur_today != prev_today_count;
                let heavy_elapsed_ms = last_heavy.elapsed().as_millis() as u64;

                // 重聚合节奏随主窗口可见性自适应：
                // - 主窗口打开：活跃（打字）时每 active_refresh_interval 秒刷新一次图表；
                //   空闲时按 full_refresh_interval（配置）刷新。
                // - 主窗口隐藏：悬浮窗/托盘只需要今日计数与速度（快节奏 500ms），
                //   重聚合完全停掉，只在强制/周期切换时执行（打开窗口会触发强制刷新）。
                let main_visible = app
                    .get_webview_window("main")
                    .map(|w| w.is_visible().unwrap_or(false))
                    .unwrap_or(false);
                let heavy_interval_ms = if !main_visible {
                    u64::MAX
                } else if active {
                    (config.get_int("gui", "active_refresh_interval", 2).max(1) as u64) * 1000
                } else if cur_today != last_heavy_today {
                    // 空闲但数据自上次重聚合后有变化：按空闲周期刷新
                    (config.get_int("gui", "full_refresh_interval", 10).max(1) as u64) * 1000
                } else {
                    // 空闲且数据未变：无需重算，等有输入或强制/周期切换
                    u64::MAX
                };
                let do_heavy = forced || period_changed || heavy_elapsed_ms >= heavy_interval_ms;

                if do_heavy {
                    // 先把写线程内存中的增量落库，图表查询才能看到最新按键
                    // （否则排行/趋势/最高单日最多滞后一个 flush 周期）
                    if let Some(w) = db.writer() {
                        if w.has_pending() {
                            w.flush(true);
                        }
                    }
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
                    // 最高单日：今日/总计 = 全历史纪录（含日期）；N天 = 窗口内最大（含日期）。
                    // 窗口值从 daily_all 取（已含落库后的今日），历史纪录跨库取。
                    let today_str = chrono::Local::now().date_naive().format("%Y-%m-%d").to_string();
                    let (max_day, max_day_date) = if period_val == -1 || period_val == 0 {
                        // get_alltime_max_day 返回 (日期, 次数)
                        let (d, c) = focusflow_core::db::get_alltime_max_day()
                            .unwrap_or((today_str.clone(), 0));
                        (c, d)
                    } else {
                        let window: Vec<(String, i64)> = if total_days >= daily_days as usize {
                            daily_all[total_days - daily_days as usize..].to_vec()
                        } else {
                            daily_all.clone()
                        };
                        window
                            .iter()
                            .max_by_key(|(_, c)| *c)
                            .map(|(d, c)| (*c, d.clone()))
                            .unwrap_or((0, today_str.clone()))
                    };
                    period_max.insert(period_val, (max_day, max_day_date.clone()));
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

                    last_heavy = Instant::now();
                    last_heavy_today = cur_today;

                    let charts = ChartsStats {
                        total,
                        avg,
                        max_day,
                        max_day_date,
                        period: period_val,
                        rank,
                        group,
                        trend,
                        trend30,
                        hourly,
                        weekday,
                    };
                    {
                        let mut s = shared.lock().unwrap();
                        s.total = charts.total;
                        s.avg = charts.avg;
                        s.max_day = charts.max_day;
                        s.max_day_date.clone_from(&charts.max_day_date);
                        s.rank.clone_from(&charts.rank);
                        s.group.clone_from(&charts.group);
                        s.trend.clone_from(&charts.trend);
                        s.trend30.clone_from(&charts.trend30);
                        s.hourly.clone_from(&charts.hourly);
                        s.weekday.clone_from(&charts.weekday);
                    }
                    let _ = app.emit("stats-charts", charts);
                }

                let today_count = cur_today;
                let cpm = focusflow_core::stats::cpm(config).get_cpm();

                // 增量维护各周期最高单日：今日计数超过纪录立即更新（零 DB 查询）。
                // 今日包含在一切周期窗口内，一次比较对所有周期成立。
                if today_count > 0 {
                    let today_str =
                        chrono::Local::now().date_naive().format("%Y-%m-%d").to_string();
                    for (_, (v, d)) in period_max.iter_mut() {
                        if today_count > *v {
                            *v = today_count;
                            *d = today_str.clone();
                        }
                    }
                }
                let (max_day, max_day_date) = period_max
                    .get(&period_val)
                    .cloned()
                    .unwrap_or((0, String::new()));

                {
                    let mut s = shared.lock().unwrap();
                    s.today_count = today_count;
                    s.cpm = cpm;
                    s.period = period_val;
                    s.max_day = max_day;
                    s.max_day_date = max_day_date.clone();
                }

                let live_changed =
                    today_count != prev_today_count || cpm != prev_cpm || period_val != prev_period;
                if live_changed {
                    let _ = app.emit(
                        "stats-live",
                        LiveStats {
                            today_count,
                            cpm,
                            period: period_val,
                            max_day,
                            max_day_date,
                        },
                    );
                }
                prev_today_count = today_count;
                prev_cpm = cpm;
                prev_period = period_val;

                if today_count != prev_logged_today {
                    // 限频：打字时今日计数每秒都在变，每 60 秒最多记一条，避免日志刷屏
                    if last_stats_log.elapsed() >= Duration::from_secs(60) {
                        tracing::info!("统计更新: today={today_count} cpm={cpm}");
                        last_stats_log = Instant::now();
                    }
                    prev_logged_today = today_count;
                }

                std::thread::sleep(Duration::from_millis(tick_ms));
            }
        })
        .expect("启动统计线程失败");
}

#[cfg(test)]
mod classify_key_tests {
    use super::classify_key;

    #[test]
    fn categories() {
        assert_eq!(classify_key("滚轮下滑"), "滚轮");
        assert_eq!(classify_key("鼠标左键"), "鼠标点击");
        assert_eq!(classify_key("左Ctrl"), "修饰键");
        assert_eq!(classify_key("Alt"), "修饰键");
        assert_eq!(classify_key("F5"), "功能键");
        assert_eq!(classify_key("3"), "数字键");
        assert_eq!(classify_key("A"), "字母键");
        assert_eq!(classify_key("空格"), "编辑键");
        assert_eq!(classify_key("回车"), "编辑键");
        assert_eq!(classify_key("Delete"), "编辑键");
        assert_eq!(classify_key("→"), "编辑键");
        assert_eq!(classify_key("自定义"), "其他");
    }

    #[test]
    fn function_key_boundary() {
        // F 开头 + 数字才算功能键；单个 "F" 或 "F0" 不是
        assert_eq!(classify_key("F1"), "功能键");
        assert_eq!(classify_key("F12"), "功能键");
        assert_eq!(classify_key("F"), "字母键"); // 单字母 F 归为字母键
        assert_eq!(classify_key("F12x"), "其他"); // 非纯数字后缀 → 其他
    }
}

