//! 系统托盘。
//!
//! 镜像 Python 版 `tray.py`：
//! - 右键菜单：显示统计面板 / 暂停记录 / 显示悬浮窗 / 退出程序
//! - 悬停 tooltip：今日活跃 | 速度 | 暂停状态
//! - 暂停时图标切换（红色暂停图标）
//! - 双击/单击左键打开主界面

use std::sync::Arc;

use muda::{Menu, MenuEvent, MenuItem, PredefinedMenuItem};
use tray_icon::{Icon, MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent};
use tray_icon::{TrayIconId};

use focusflow_core::paths;

/// 托盘菜单动作标识
const MENU_SHOW: &str = "show";
const MENU_PAUSE: &str = "pause";
const MENU_FLOATING: &str = "floating";
const MENU_QUIT: &str = "quit";

/// 托盘控制器回调（由 GUI 层实现）。
pub trait TrayCallbacks: Send + Sync + 'static {
    /// 显示主窗口
    fn show_window(&self);
    /// 切换暂停
    fn toggle_pause(&self) -> bool;
    /// 是否已暂停（刷新菜单勾选态）
    #[allow(dead_code)]
    fn is_paused(&self) -> bool;
    /// 切换悬浮窗
    fn toggle_floating(&self);
    /// 是否显示悬浮窗
    #[allow(dead_code)]
    fn is_floating_visible(&self) -> bool;
    /// 请求退出
    fn request_quit(&self);
}

/// 托盘控制器。
pub struct Tray {
    icon: Option<TrayIcon>,
    callbacks: Arc<dyn TrayCallbacks>,
}

impl Tray {
    pub fn new(callbacks: Arc<dyn TrayCallbacks>) -> Self {
        Self { icon: None, callbacks }
    }

    /// 启动托盘（创建图标 + 菜单 + 事件循环）。
    pub fn start(&mut self) -> anyhow::Result<()> {
        // 事件循环：菜单事件
        let _ = MenuEvent::receiver();
        let _ = TrayIconEvent::receiver();

        // 构建菜单
        let menu = Menu::new();
        let show_item = MenuItem::with_id(MENU_SHOW, "显示统计面板", true, None);
        let pause_item = MenuItem::with_id(MENU_PAUSE, "暂停记录", true, None);
        let floating_item = MenuItem::with_id(MENU_FLOATING, "显示悬浮窗", true, None);
        let quit_item = MenuItem::with_id(MENU_QUIT, "退出程序", true, None);
        menu.append_items(&[
            &show_item,
            &pause_item,
            &PredefinedMenuItem::separator(),
            &floating_item,
            &PredefinedMenuItem::separator(),
            &quit_item,
        ])?;

        // 图标（用原始项目图标）
        let icon = load_icon(false)?;

        let builder = TrayIconBuilder::new()
            .with_id(TrayIconId::new("focusflow"))
            .with_menu(Box::new(menu))
            .with_tooltip("FocusFlow - 效率追踪器")
            .with_icon(icon)
            .with_menu_on_left_click(false);
        // 设置图标为模板的替代：直接构建
        let tray = builder.build()?;
        self.icon = Some(tray);

        // 事件处理：监听菜单与托盘点击
        self.spawn_event_loop();

        tracing::info!("托盘已启动");
        Ok(())
    }

    fn spawn_event_loop(&self) {
        let callbacks = Arc::clone(&self.callbacks);
        // 菜单事件：muda MenuEvent::receiver
        let menu_rx = MenuEvent::receiver();
        // 托盘点击事件：tray-icon 全局 receiver
        let tray_rx = TrayIconEvent::receiver();
        std::thread::Builder::new()
            .name("tray-events".into())
            .spawn(move || loop {
                // 处理菜单事件
                while let Ok(event) = menu_rx.try_recv() {
                    handle_menu(event, &callbacks);
                }
                // 处理托盘点击（单击左键/双击打开主界面）
                while let Ok(event) = tray_rx.try_recv() {
                    match event {
                        TrayIconEvent::Click { button, button_state, .. } => {
                            if button == MouseButton::Left
                                && button_state == MouseButtonState::Up
                            {
                                callbacks.show_window();
                            }
                        }
                        TrayIconEvent::DoubleClick { .. } => {
                            callbacks.show_window();
                        }
                        _ => {}
                    }
                }
                std::thread::sleep(std::time::Duration::from_millis(100));
            })
            .expect("启动托盘事件线程失败");
    }

    /// 更新 tooltip（供 GUI 定时刷新调用）。
    #[allow(dead_code)]
    pub fn update_tooltip(&self, text: &str) {
        if let Some(icon) = &self.icon {
            let _ = icon.set_tooltip(Some(text));
        }
    }

    /// 切换暂停图标。
    #[allow(dead_code)]
    pub fn set_paused_icon(&self, paused: bool) {
        if let Some(tray) = &self.icon {
            if let Ok(new_icon) = load_icon(paused) {
                let _ = tray.set_icon(Some(new_icon));
            }
        }
    }

    /// 停止托盘。
    #[allow(dead_code)]
    pub fn stop(&mut self) {
        if let Some(icon) = &self.icon {
            let _ = icon.set_visible(false);
        }
        self.icon = None;
        tracing::info!("托盘已停止");
    }
}

fn handle_menu(event: MenuEvent, callbacks: &Arc<dyn TrayCallbacks>) {
    match event.id().0.as_str() {
        MENU_SHOW => callbacks.show_window(),
        MENU_PAUSE => {
            callbacks.toggle_pause();
        }
        MENU_FLOATING => {
            callbacks.toggle_floating();
        }
        MENU_QUIT => callbacks.request_quit(),
        _ => {}
    }
}

/// 加载托盘图标（正常/暂停）。
///
/// Windows 的 `Icon::from_path` 只支持 .ico，因此这里用 image crate
/// 解码 PNG 后调用 `Icon::from_rgba`。
fn load_icon(paused: bool) -> anyhow::Result<Icon> {
    let path = if paused {
        paths::app_dir().join("focusflow_paused.png")
    } else {
        paths::app_dir().join("focusflow.png")
    };
    let img = image::open(&path).map_err(|e| anyhow::anyhow!("读取图标 {} 失败: {e}", path.display()))?;
    let rgba = img.to_rgba8();
    let (w, h) = rgba.dimensions();
    Icon::from_rgba(rgba.into_raw(), w, h)
        .map_err(|e| anyhow::anyhow!("创建托盘图标失败: {e:?}"))
}
