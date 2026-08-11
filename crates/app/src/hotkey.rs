//! 全局热键。
//!
//! 镜像 Python 版 `hotkey.py`：
//! - 注册 Ctrl+Shift+F 显隐主窗口（默认关闭，可在设置中开启）
//! - 支持自定义热键组合（如 ctrl+shift+f / ctrl+alt+f 等）

use std::sync::Arc;

use global_hotkey::hotkey::{HotKey, Modifiers};
use global_hotkey::{GlobalHotKeyEvent, GlobalHotKeyManager, HotKeyState};

/// 热键触发回调。
pub trait HotkeyCallback: Send + Sync + 'static {
    fn on_hotkey(&self);
}

/// 全局热键管理器封装。
pub struct HotkeyManager {
    manager: GlobalHotKeyManager,
    /// 当前注册的热键
    registered: Vec<HotKey>,
    /// 回调（需在 set 时匹配）
    callbacks: Arc<dyn HotkeyCallback>,
}

impl HotkeyManager {
    pub fn new(callbacks: Arc<dyn HotkeyCallback>) -> anyhow::Result<Self> {
        Ok(Self {
            manager: GlobalHotKeyManager::new()?,
            registered: Vec::new(),
            callbacks,
        })
    }

    /// 注册热键。`hotkey_str` 如 "ctrl+shift+f"。
    /// 返回是否成功注册（热键被占用/格式错误返回 false）。
    pub fn register(&mut self, hotkey_str: &str) -> bool {
        // 先停止旧的
        self.unregister_all();

        let parsed = match hotkey_str.parse::<HotKey>() {
            Ok(h) => h,
            Err(e) => {
                tracing::warn!("无法解析热键 [{hotkey_str}]: {e}");
                return false;
            }
        };
        match self.manager.register(parsed) {
            Ok(()) => {
                self.registered.push(parsed);
                tracing::info!("已注册全局热键: {hotkey_str}");
                true
            }
            Err(e) => {
                tracing::warn!("注册全局热键 [{hotkey_str}] 失败: {e}");
                false
            }
        }
    }

    /// 注销所有已注册热键。
    pub fn unregister_all(&mut self) {
        for hk in self.registered.drain(..) {
            let _ = self.manager.unregister(hk);
        }
    }

    /// 启动事件监听线程。
    pub fn spawn_event_loop(&self) {
        let callbacks = Arc::clone(&self.callbacks);
        let rx = GlobalHotKeyEvent::receiver();
        std::thread::Builder::new()
            .name("global-hotkey".into())
            .spawn(move || loop {
                while let Ok(event) = rx.try_recv() {
                    if event.state() == HotKeyState::Pressed {
                        callbacks.on_hotkey();
                    }
                }
                std::thread::sleep(std::time::Duration::from_millis(50));
            })
            .expect("启动热键监听线程失败");
    }
}

/// 归一化修饰键（兼容配置中的 win/super/cmd 写法）。
#[allow(dead_code)]
fn normalize_mods(mods: Modifiers) -> Modifiers {
    mods
}
