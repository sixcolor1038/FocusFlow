//! 悬浮窗：基于 egui 原生多视口（deferred viewport）的极小置顶小窗。
//!
//! 与主窗口共用同一个事件循环（winit 0.30 每个进程只允许一个 EventLoop），
//! 通过 `Context::show_viewport_deferred` 创建独立、置顶的原生窗口。
//! 悬浮窗数据来自后台统计线程的共享快照，自身不做任何 DB 查询。
//! 主窗口隐藏到托盘时，eframe 仍以 100ms 间隔驱动不可见窗口，悬浮窗可常驻。
//!
//! 外观：物理尺寸 2cm × 1.2cm，普通半透明背景（亮度适中，非亚克力磨砂），
//! 显示"活跃"与"速度"两行加粗数据（速度按"数据/分"），按住任意位置可拖动；
//! 位置持久化到配置，重启不重置，父窗口隐藏也不乱跳；始终置顶（含任务栏上方）。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use eframe::egui;

use focusflow_core::config::FocusFlowConfig;

use crate::gui::SharedStats;
use crate::views::Theme;

/// 悬浮窗视口唯一 ID。
const FLOATING_VIEWPORT_ID: &str = "focusflow-floating";

/// 目标物理尺寸（厘米）：长 2.2cm × 宽 1.2cm（加宽以容纳更大的加粗字号）。
const SIZE_CM: (f32, f32) = (2.2, 1.2);

/// 未拖动却发生位移的判定阈值（逻辑点），超过则视为异常位移并拉回。
const MOVE_SNAP_THRESHOLD: f32 = 200.0;

/// 悬浮窗窗口标题（用于按标题定位 HWND 抬升置顶）。
const FLOATING_TITLE: &str = "FocusFlow 悬浮窗";

/// 悬浮窗控制器（线程安全的开关句柄）。
pub struct FloatingWindow {
    shared: Arc<Mutex<SharedStats>>,
    config: &'static FocusFlowConfig,
    /// 期望可见状态（托盘菜单勾选态 / 启动配置）
    wanted: Arc<AtomicBool>,
    /// 悬浮窗视口上下文（用于立即关闭）
    ctx: Mutex<Option<egui::Context>>,
    /// 当前窗口位置（逻辑点）；None 表示尚未创建
    pos: Arc<Mutex<Option<egui::Pos2>>>,
    /// 位置写盘节流时间戳
    last_pos_save: Arc<Mutex<Instant>>,
    /// 最近一次用户拖动开始时间（抑制误判拉回）
    last_drag: Arc<Mutex<Instant>>,
    /// 整窗透明度是否已应用（只应用一次）
    alpha_applied: Arc<AtomicBool>,
    /// 双击悬浮窗回调（由主界面注册，用于打开主窗口）
    on_open: Mutex<Option<Arc<dyn Fn() + Send + Sync>>>,
}

impl FloatingWindow {
    pub fn new(shared: Arc<Mutex<SharedStats>>, config: &'static FocusFlowConfig) -> Self {
        Self {
            shared,
            config,
            wanted: Arc::new(AtomicBool::new(false)),
            ctx: Mutex::new(None),
            pos: Arc::new(Mutex::new(None)),
            last_pos_save: Arc::new(Mutex::new(Instant::now())),
            last_drag: Arc::new(Mutex::new(Instant::now() - Duration::from_secs(10))),
            alpha_applied: Arc::new(AtomicBool::new(false)),
            on_open: Mutex::new(None),
        }
    }

    /// 注册双击回调（打开主窗口）。
    pub fn set_open_callback(&self, f: impl Fn() + Send + Sync + 'static) {
        *self.on_open.lock().unwrap() = Some(Arc::new(f));
    }

    /// 切换可见状态。
    pub fn set_visible(&self, show: bool) {
        self.wanted.store(show, Ordering::SeqCst);
        if !show {
            // 隐藏前把当前已记录位置立即写盘，保证再次显示时回到原位
            if let Some(p) = self.pos.lock().unwrap().as_ref() {
                self.config.set("floating", "pos_x", &p.x.to_string()).ok();
                self.config.set("floating", "pos_y", &p.y.to_string()).ok();
            }
            *self.pos.lock().unwrap() = None; // 下次显示时按保存位置重新定位
            // 立即关闭视口（显式指定悬浮窗视口，避免误关主窗口）
            let floating_id = egui::ViewportId::from_hash_of(FLOATING_VIEWPORT_ID);
            if let Some(ctx) = self.ctx.lock().unwrap().as_ref() {
                ctx.send_viewport_cmd_to(floating_id, egui::ViewportCommand::Close);
            }
        }
    }

    /// 是否应显示（菜单勾选态）。
    pub fn is_visible(&self) -> bool {
        self.wanted.load(Ordering::SeqCst)
    }

    /// 读取持久化的窗口位置（`[floating] pos_x/pos_y`，单位为逻辑点）。
    fn load_pos(&self) -> Option<egui::Pos2> {
        let x = self.config.get_float("floating", "pos_x", f64::NAN);
        let y = self.config.get_float("floating", "pos_y", f64::NAN);
        if x.is_nan() || y.is_nan() {
            None
        } else {
            Some(egui::pos2(x as f32, y as f32))
        }
    }

    /// 主界面每帧调用：当需要显示时注册悬浮窗视口。
    pub fn show(&self, parent_ctx: &egui::Context) {
        if !self.wanted.load(Ordering::SeqCst) {
            return;
        }
        let id = egui::ViewportId::from_hash_of(FLOATING_VIEWPORT_ID);

        // 目标物理尺寸 → 逻辑点（point = 物理像素 / ppp）
        let ppp = parent_ctx.pixels_per_point().max(0.5);
        let w_pt = SIZE_CM.0 / 2.54 * 96.0 / ppp; // ≈ 75 / ppp
        let h_pt = SIZE_CM.1 / 2.54 * 96.0 / ppp; // ≈ 45 / ppp

        // 首次创建才应用位置：优先持久化值；否则等显示器尺寸就绪后放右下角。
        // 避免用"左上角兜底"导致窗口出现在错误位置并被误保存。
        let mut builder = egui::ViewportBuilder::default()
            .with_title("FocusFlow 悬浮窗")
            .with_inner_size([w_pt, h_pt])
            .with_min_inner_size([w_pt, h_pt])
            .with_max_inner_size([w_pt, h_pt])
            .with_resizable(false)
            .with_decorations(false)
            .with_always_on_top()
            .with_transparent(false)
            .with_taskbar(false);
        {
            let mut pos = self.pos.lock().unwrap();
            if pos.is_none() {
                let p = if let Some(saved) = self.load_pos() {
                    saved
                } else if let Some(sz) = parent_ctx.input(|i| i.viewport().monitor_size) {
                    // 默认顶部靠右（对齐 Python 版），距屏幕顶部 60
                    egui::pos2((sz.x - w_pt - 24.0).max(20.0), 60.0)
                } else if let Some(rect) = parent_ctx.input(|i| i.viewport().inner_rect) {
                    // 显示器信息未就绪时，退而锚定主窗口右上角附近
                    egui::pos2((rect.right() - w_pt - 20.0).max(20.0), 60.0)
                } else {
                    // 主窗口信息也尚未就绪：本帧不创建，下一帧再定位（避免落在左上角）
                    return;
                };
                builder = builder.with_position(p);
                *pos = Some(p);
            }
        }

        let shared = Arc::clone(&self.shared);
        let config = self.config;
        let wanted = Arc::clone(&self.wanted);
        let pos_cb = Arc::clone(&self.pos);
        let last_save_cb = Arc::clone(&self.last_pos_save);
        let last_drag_cb = Arc::clone(&self.last_drag);
        let alpha_applied_cb = Arc::clone(&self.alpha_applied);
        let on_open_cb = Arc::new(Mutex::new(None));
        {
            let src = self.on_open.lock().unwrap();
            *on_open_cb.lock().unwrap() = src.as_ref().map(Arc::clone);
        }
        let viewport_id = id;
        let ctx_slot = Arc::new(Mutex::new(None::<egui::Context>));
        let ctx_slot_cb = Arc::clone(&ctx_slot);
        *self.ctx.lock().unwrap() = None;

        parent_ctx.show_viewport_deferred(id, builder, move |ui, _class| {
            let child_ctx = ui.ctx().clone();
            *ctx_slot_cb.lock().unwrap() = Some(child_ctx.clone());

            // 窗口尺寸由 ViewportBuilder 的 min/max 内尺寸锁定，无需每帧发送 InnerSize
            //（随当前视口路由的命令可能误发到主窗口，把主界面缩到悬浮窗大小）。

            // 显式置顶抬回任务栏之上：winit 的 set_window_level 对已置顶窗口是空操作，
            // 这里用 SetWindowPos(HWND_TOPMOST) 真正把 z-order 抬到任务栏上方。
            raise_above_taskbar();

            // 首次创建后应用整窗统一透明度（等价 Python 版 Tkinter -alpha）
            if !alpha_applied_cb.swap(true, Ordering::SeqCst) {
                apply_global_alpha();
            }

            // 用户通过 Alt+F4 / 系统菜单关闭悬浮窗时，同步状态
            if child_ctx.input(|i| i.viewport().close_requested()) {
                wanted.store(false, Ordering::SeqCst);
            }
            if !wanted.load(Ordering::SeqCst) {
                return;
            }

            // 位置：记录真实外框位置；若未拖动却发生大幅位移（如父窗口隐藏被系统挪走），
            // 拉回原位置，避免悬浮窗自行跳到左上角。
            if let Some(rect) = child_ctx.input(|i| i.viewport().outer_rect) {
                let cur = rect.min;
                let pointer_down = child_ctx.input(|i| i.pointer.any_down());
                let mut saved = pos_cb.lock().unwrap();
                let mut last_save = last_save_cb.lock().unwrap();
                let last_drag = last_drag_cb.lock().unwrap();
                let recent_drag = last_drag.elapsed() < Duration::from_secs(2);
                let snap_back = saved.is_some()
                    && !recent_drag
                    && !pointer_down
                    && saved.unwrap().distance(cur) > MOVE_SNAP_THRESHOLD;
                if snap_back {
                    child_ctx.send_viewport_cmd_to(
                        viewport_id,
                        egui::ViewportCommand::OuterPosition(saved.unwrap()),
                    );
                } else if saved.as_ref() != Some(&cur) {
                    *saved = Some(cur);
                    if last_save.elapsed() >= Duration::from_secs(2) {
                        config.set("floating", "pos_x", &cur.x.to_string()).ok();
                        config.set("floating", "pos_y", &cur.y.to_string()).ok();
                        *last_save = Instant::now();
                    }
                }
            }

            let s = shared.lock().unwrap().clone();
            let theme = if config.get("gui", "theme") == "dark" {
                Theme::dark()
            } else {
                Theme::light()
            };
            // 字体按 DPI 同步缩放：物理字号固定，高 DPI 下不再显得偏小。
            // 标签（活跃/速度）与数值统一字号；用注册的粗体字体家族实现真加粗。
            let ppp = child_ctx.pixels_per_point().max(0.5);
            let label_size = 13.0 / ppp;
            let value_size = 13.0 / ppp;

            // 不透明渲染背景（整窗透明度由 SetLayeredWindowAttributes 统一控制，
            // 避免 per-pixel 透明在部分 GPU 上渲染成黑底）
            let bg = theme.card_bg;

            egui::CentralPanel::default()
                .frame(
                    egui::Frame::default()
                        .fill(bg)
                        .stroke(egui::Stroke::new(1.0, theme.border))
                        .corner_radius(4.0)
                        .inner_margin(egui::Margin::symmetric(2, 1)),
                )
                .show(ui, |ui| {
                    // 压缩行距，保证两行内容不把窗口撑高
                    ui.spacing_mut().item_spacing = egui::vec2(4.0, 1.0);
                    // 两行：活跃 / 速度（速度按"数据/分"显示），加粗
                    stat_line(ui, &theme, "活跃", &s.today_count.to_string(), label_size, value_size);
                    stat_line_speed(ui, &theme, "速度", s.cpm, label_size, value_size);

                    // 整窗交互：按住任意位置拖动；双击打开主界面（对齐 Python 版）
                    let drag = ui.interact(
                        ui.max_rect(),
                        egui::Id::new("floating-drag"),
                        egui::Sense::click_and_drag(),
                    );
                    if drag.double_clicked() {
                        if let Some(cb) = on_open_cb.lock().unwrap().as_ref() {
                            cb();
                        }
                    }
                    if drag.drag_started() {
                        *last_drag_cb.lock().unwrap() = Instant::now();
                        child_ctx.send_viewport_cmd_to(viewport_id, egui::ViewportCommand::StartDrag);
                    }
                });

            // 刷新节奏：1 秒一次，减少常驻 GPU 开销
            child_ctx.request_repaint_after(Duration::from_millis(1000));
        });

        // 每帧回写 child ctx 供 set_visible(false) 立即关闭（先出锁再回写）
        let child_ctx = ctx_slot.lock().unwrap().as_ref().cloned();
        if let Some(child_ctx) = child_ctx {
            *self.ctx.lock().unwrap() = Some(child_ctx);
        }
    }
}

/// 固定宽度的标签槽位：两行（活跃/速度）标签宽度一致，数值严格左对齐。
fn label_slot(
    ui: &mut egui::Ui,
    theme: &Theme,
    label: &str,
    label_size: f32,
    row_h: f32,
) {
    ui.allocate_ui_with_layout(
        egui::vec2(label_size * 2.2, row_h),
        egui::Layout::left_to_right(egui::Align::Center),
        |ui| {
            ui.label(
                egui::RichText::new(label)
                    .size(label_size)
                    .family(crate::views::bold_family())
                    .color(theme.muted),
            );
        },
    );
}

/// 一行：标签槽位 + 数值，固定行高 + 垂直居中（避免 CJK 与 Segoe 字体混排错位）。
fn stat_line(
    ui: &mut egui::Ui,
    theme: &Theme,
    label: &str,
    value: &str,
    label_size: f32,
    value_size: f32,
) {
    let row_h = label_size.max(value_size) * 1.45;
    ui.allocate_ui_with_layout(
        egui::vec2(ui.available_width(), row_h),
        egui::Layout::left_to_right(egui::Align::Center),
        |ui| {
            ui.spacing_mut().item_spacing = egui::vec2(0.0, 0.0);
            label_slot(ui, theme, label, label_size, row_h);
            ui.add_space(2.0);
            ui.label(
                egui::RichText::new(value)
                    .size(value_size)
                    .family(crate::views::bold_family())
                    .color(theme.fg),
            );
        },
    );
}

/// 速度行：标签槽位 + 数字 + 小号斜杠 + "分"。
/// 斜杠略小于数字（75%）并紧贴两侧，避免在混排中显得过大/占位过宽。
fn stat_line_speed(
    ui: &mut egui::Ui,
    theme: &Theme,
    label: &str,
    cpm: i64,
    label_size: f32,
    value_size: f32,
) {
    let row_h = label_size.max(value_size) * 1.45;
    let slash_size = value_size * 0.75;
    ui.allocate_ui_with_layout(
        egui::vec2(ui.available_width(), row_h),
        egui::Layout::left_to_right(egui::Align::Center),
        |ui| {
            ui.spacing_mut().item_spacing = egui::vec2(0.0, 0.0);
            label_slot(ui, theme, label, label_size, row_h);
            ui.add_space(2.0);
            ui.label(
                egui::RichText::new(cpm.to_string())
                    .size(value_size)
                    .family(crate::views::bold_family())
                    .color(theme.fg),
            );
            ui.add_space(1.0);
            ui.label(
                egui::RichText::new("/")
                    .size(slash_size)
                    .family(crate::views::bold_family())
                    .color(theme.fg),
            );
            ui.add_space(1.0);
            ui.label(
                egui::RichText::new("分")
                    .size(value_size)
                    .family(crate::views::bold_family())
                    .color(theme.fg),
            );
        },
    );
}

/// 应用整窗统一透明度（等价 Python 版 Tkinter 的 `-alpha`）。
/// 窗口内容照常不透明渲染，再由 Windows 按全局 alpha 淡出，保证任何 GPU 上都可用，
/// 且能真实透出桌面（随背景变化）。
fn apply_global_alpha() {
    #[cfg(windows)]
    {
        use windows::Win32::UI::WindowsAndMessaging::{
            FindWindowW, GetWindowLongPtrW, SetLayeredWindowAttributes, SetWindowLongPtrW, GWL_EXSTYLE,
            LAYERED_WINDOW_ATTRIBUTES_FLAGS, LWA_ALPHA, WS_EX_LAYERED,
        };
        use windows::core::PCWSTR;
        use windows::Win32::Foundation::COLORREF;

        let alpha = ((focusflow_core::config::instance().get_float("floating", "opacity", 0.75).clamp(0.3, 1.0))
            * 255.0) as u8;
        let title: Vec<u16> = FLOATING_TITLE
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect();
        unsafe {
            if let Ok(hwnd) = FindWindowW(PCWSTR::null(), PCWSTR(title.as_ptr())) {
                if !hwnd.is_invalid() {
                    // 确保窗口带 WS_EX_LAYERED 扩展样式（SetLayeredWindowAttributes 的前置条件）
                    let style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE);
                    if style & WS_EX_LAYERED.0 as isize == 0 {
                        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED.0 as isize);
                    }
                    let _ = SetLayeredWindowAttributes(
                        hwnd,
                        COLORREF(0),
                        alpha,
                        LAYERED_WINDOW_ATTRIBUTES_FLAGS(LWA_ALPHA.0),
                    );
                }
            }
        }
    }
}

/// 把悬浮窗抬升到所有窗口（含任务栏）之上。即使已置顶，
/// 点击任务栏后其 z-order 也会被压到下方，这里显式置顶抬回。
fn raise_above_taskbar() {
    #[cfg(windows)]
    {
        use windows::Win32::UI::WindowsAndMessaging::{
            FindWindowW, SetWindowPos, HWND_TOPMOST, SET_WINDOW_POS_FLAGS, SWP_NOACTIVATE, SWP_NOMOVE,
            SWP_NOSIZE,
        };
        use windows::core::PCWSTR;

        let title: Vec<u16> = FLOATING_TITLE
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect();
        unsafe {
            if let Ok(hwnd) = FindWindowW(PCWSTR::null(), PCWSTR(title.as_ptr())) {
                if !hwnd.is_invalid() {
                    let flags = SET_WINDOW_POS_FLAGS(SWP_NOACTIVATE.0 | SWP_NOMOVE.0 | SWP_NOSIZE.0);
                    let _ = SetWindowPos(hwnd, Some(HWND_TOPMOST), 0, 0, 0, 0, flags);
                }
            }
        }
    }
}
