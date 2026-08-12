//! 系统托盘：显示主界面 / 暂停记录（可勾选）/ 显示悬浮窗 / 退出程序。
//! 对齐 Python 版：tooltip 每 5 秒刷新今日活跃与速度，暂停时切换图标并勾选菜单。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use tauri::{
    menu::{CheckMenuItem, Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIcon, TrayIconBuilder, TrayIconEvent},
    App, AppHandle, Emitter, Manager,
};

use crate::state::AppState;

const MENU_SHOW: &str = "show";
const MENU_PAUSE: &str = "pause";
const MENU_FLOATING: &str = "floating";
const MENU_QUIT: &str = "quit";

/// 托盘控制器（供后台更新线程使用）。
static TRAY: Mutex<Option<TrayIcon<tauri::Wry>>> = Mutex::new(None);
static PAUSE_ITEM: Mutex<Option<CheckMenuItem<tauri::Wry>>> = Mutex::new(None);
/// 上次是否暂停（避免每次重设图标）
static LAST_PAUSED: AtomicBool = AtomicBool::new(false);

/// 设置托盘图标 + 菜单 + 事件 + 后台 tooltip/图标更新。
pub fn setup_tray(app: &mut App) -> anyhow::Result<()> {
    let show_item = MenuItem::with_id(app, MENU_SHOW, "显示统计面板", true, None::<&str>)?;
    let pause_item = CheckMenuItem::with_id(app, MENU_PAUSE, "暂停记录", true, false, None::<&str>)?;
    let floating_item = MenuItem::with_id(app, MENU_FLOATING, "显示悬浮窗", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, MENU_QUIT, "退出程序", true, None::<&str>)?;

    let menu = Menu::with_items(
        app,
        &[
            &show_item,
            &pause_item,
            &tauri::menu::PredefinedMenuItem::separator(app)?,
            &floating_item,
            &tauri::menu::PredefinedMenuItem::separator(app)?,
            &quit_item,
        ],
    )?;

    // 图标：编译期内嵌 PNG（正常/暂停）
    let icon = tauri::include_image!("assets/focusflow.png");
    let paused_icon = tauri::include_image!("assets/focusflow_paused.png");

    let tray = TrayIconBuilder::with_id("focusflow")
        .menu(&menu)
        .icon(icon)
        .tooltip("FocusFlow - 效率追踪器")
        .show_menu_on_left_click(false)
        .on_menu_event(|app, event| handle_menu(app, event.id().as_ref()))
        .on_tray_icon_event(|tray, event| {
            // 左键单击/双击 → 显示主窗口
            match event {
                TrayIconEvent::Click {
                    button: MouseButton::Left,
                    button_state: MouseButtonState::Up,
                    ..
                }
                | TrayIconEvent::DoubleClick {
                    button: MouseButton::Left,
                    ..
                } => show_main(tray.app_handle()),
                _ => {}
            }
        })
        .build(app)?;

    *TRAY.lock().unwrap() = Some(tray);
    *PAUSE_ITEM.lock().unwrap() = Some(pause_item);

    spawn_tray_updater(app, paused_icon);
    Ok(())
}

/// 后台更新：每 5 秒刷新 tooltip（今日活跃/速度/暂停状态），
/// 暂停时切换图标并勾选菜单项（对齐 Python 版）。
fn spawn_tray_updater(app: &App, paused_icon: tauri::image::Image<'static>) {
    let normal_icon = tauri::include_image!("assets/focusflow.png");
    let handle = app.handle().clone();

    std::thread::Builder::new()
        .name("tray-updater".into())
        .spawn(move || loop {
            std::thread::sleep(std::time::Duration::from_secs(5));
            let Some(state) = handle.try_state::<Arc<AppState>>() else {
                continue;
            };
            let s = state.shared.lock().unwrap();
            let paused = state.listener.is_paused();
            let paused_now = LAST_PAUSED.swap(paused, Ordering::SeqCst) != paused;
            let tooltip = format!(
                "FocusFlow - 今日 {} · 速度 {}/分{}",
                s.today_count,
                s.cpm,
                if paused { " · 已暂停" } else { "" }
            );

            let tray = TRAY.lock().unwrap().clone();
            if let Some(tray) = tray {
                let _ = tray.set_tooltip(Some(&tooltip));
                if paused_now {
                    let _ = tray.set_icon(Some(if paused { paused_icon.clone() } else { normal_icon.clone() }));
                }
            }
            if let Some(item) = PAUSE_ITEM.lock().unwrap().as_ref() {
                let _ = item.set_checked(paused);
            }
        })
        .expect("启动托盘更新线程失败");
}

fn handle_menu(app: &AppHandle, id: &str) {
    match id {
        MENU_SHOW => show_main(app),
        MENU_PAUSE => toggle_pause(app),
        MENU_FLOATING => toggle_floating(app),
        MENU_QUIT => {
            app.exit(0);
        }
        _ => {}
    }
}

fn show_main(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
    }
}

fn toggle_pause(app: &AppHandle) {
    if let Some(state) = app.try_state::<Arc<AppState>>() {
        let paused = state.listener.toggle_pause();
        // 同步前端暂停状态
        if let Some(win) = app.get_webview_window("main") {
            let _ = win.emit("pause-changed", paused);
        }
        if let Some(win) = app.get_webview_window("floating") {
            let _ = win.emit("pause-changed", paused);
        }
    }
}

fn toggle_floating(app: &AppHandle) {
    if let Some(win) = app.get_webview_window("floating") {
        if win.is_visible().unwrap_or(false) {
            let _ = win.hide();
        } else {
            let _ = win.show();
            let _ = win.set_focus();
        }
    }
}
