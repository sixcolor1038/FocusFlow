//! 键鼠监听模块。
//!
//! 镜像 Python 版 `listener.py`：
//! - rdev 全局键盘/鼠标监听（键盘 + 鼠标点击/滚轮统一计数）
//! - 修饰键/功能键过滤
//! - 长按自动重复过滤（`_pressed` 集合 + stale 时长）
//! - 滚轮连续滚动合并（0.8s 窗口内同方向只计 1 次）
//! - Ctrl+字母控制字符还原为物理键（v1.2.1 行为）
//! - 暂停/恢复，事件回调（番茄钟 / 护眼提醒用）

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant, SystemTime};

use rdev::{listen, Button, Event, EventType, Key};

use crate::config::FocusFlowConfig;
use crate::db::Database;

/// 修饰键集合（用于过滤）
fn is_modifier(key: &Key) -> bool {
    matches!(
        key,
        Key::ShiftLeft
            | Key::ShiftRight
            | Key::ControlLeft
            | Key::ControlRight
            | Key::Alt
            | Key::AltGr
            | Key::MetaLeft
            | Key::MetaRight
    )
}

/// 功能键集合（F1-F12）
fn is_function_key(key: &Key) -> bool {
    matches!(
        key,
        Key::F1
            | Key::F2
            | Key::F3
            | Key::F4
            | Key::F5
            | Key::F6
            | Key::F7
            | Key::F8
            | Key::F9
            | Key::F10
            | Key::F11
            | Key::F12
    )
}

/// 特殊键映射表（rdev Key -> 中文显示名），对应 Python 版 `_SPECIAL_KEY_MAP`。
fn key_display_name(key: &Key) -> String {
    match key {
        Key::Space => "空格".to_string(),
        Key::Return => "回车".to_string(),
        Key::Backspace => "退格".to_string(),
        Key::Tab => "Tab".to_string(),
        Key::ShiftLeft => "左Shift".to_string(),
        Key::ShiftRight => "右Shift".to_string(),
        Key::ControlLeft => "左Ctrl".to_string(),
        Key::ControlRight => "右Ctrl".to_string(),
        Key::Alt => "左Alt".to_string(),
        Key::AltGr => "AltGr".to_string(),
        Key::MetaLeft => "左Win".to_string(),
        Key::MetaRight => "右Win".to_string(),
        Key::CapsLock => "CapsLock".to_string(),
        Key::Escape => "Esc".to_string(),
        Key::Delete => "Delete".to_string(),
        Key::Home => "Home".to_string(),
        Key::End => "End".to_string(),
        Key::PageUp => "PageUp".to_string(),
        Key::PageDown => "PageDown".to_string(),
        Key::Insert => "Insert".to_string(),
        Key::NumLock => "NumLock".to_string(),
        Key::ScrollLock => "ScrollLock".to_string(),
        Key::PrintScreen => "PrintScreen".to_string(),
        Key::Pause => "Pause".to_string(),
        Key::UpArrow => "↑".to_string(),
        Key::DownArrow => "↓".to_string(),
        Key::LeftArrow => "←".to_string(),
        Key::RightArrow => "→".to_string(),
        Key::F1 => "F1".to_string(),
        Key::F2 => "F2".to_string(),
        Key::F3 => "F3".to_string(),
        Key::F4 => "F4".to_string(),
        Key::F5 => "F5".to_string(),
        Key::F6 => "F6".to_string(),
        Key::F7 => "F7".to_string(),
        Key::F8 => "F8".to_string(),
        Key::F9 => "F9".to_string(),
        Key::F10 => "F10".to_string(),
        Key::F11 => "F11".to_string(),
        Key::F12 => "F12".to_string(),
        _ => format!("{:?}", key),
    }
}

/// 字母键映射：rdev `KeyA`..`KeyZ` -> "A".."Z"
fn letter_name(key: &Key) -> Option<String> {
    let variant = format!("{:?}", key);
    if let Some(ch) = variant.strip_prefix("Key") {
        if ch.len() == 1 && ch.chars().next().unwrap().is_ascii_alphabetic() {
            return Some(ch.to_ascii_uppercase());
        }
    }
    None
}

/// 数字键映射：`Num0`..`Num9` -> "0".."9"
fn digit_name(key: &Key) -> Option<String> {
    let variant = format!("{:?}", key);
    if let Some(d) = variant.strip_prefix("Num") {
        if d.len() == 1 && d.chars().next().unwrap().is_ascii_digit() {
            return Some(d.to_string());
        }
    }
    None
}

/// 其他符号键映射
fn symbol_name(key: &Key) -> Option<String> {
    Some(match key {
        Key::BackQuote => "`".to_string(),
        Key::Minus => "-".to_string(),
        Key::Equal => "=".to_string(),
        Key::LeftBracket => "[".to_string(),
        Key::RightBracket => "]".to_string(),
        Key::SemiColon => ";".to_string(),
        Key::Quote => "'".to_string(),
        Key::BackSlash => "\\".to_string(),
        Key::IntlBackslash => "\\".to_string(),
        Key::Comma => ",".to_string(),
        Key::Dot => ".".to_string(),
        Key::Slash => "/".to_string(),
        _ => return None,
    })
}

/// 小键盘键映射
fn kp_name(key: &Key) -> Option<String> {
    Some(match key {
        Key::Kp0 => "数字键盘0".to_string(),
        Key::Kp1 => "数字键盘1".to_string(),
        Key::Kp2 => "数字键盘2".to_string(),
        Key::Kp3 => "数字键盘3".to_string(),
        Key::Kp4 => "数字键盘4".to_string(),
        Key::Kp5 => "数字键盘5".to_string(),
        Key::Kp6 => "数字键盘6".to_string(),
        Key::Kp7 => "数字键盘7".to_string(),
        Key::Kp8 => "数字键盘8".to_string(),
        Key::Kp9 => "数字键盘9".to_string(),
        Key::KpReturn => "数字键盘回车".to_string(),
        Key::KpMinus => "数字键盘-".to_string(),
        Key::KpPlus => "数字键盘+".to_string(),
        Key::KpMultiply => "数字键盘*".to_string(),
        Key::KpDivide => "数字键盘/".to_string(),
        Key::KpDelete => "Delete".to_string(),
        _ => return None,
    })
}

/// 规范化键盘按键名：rdev Key -> 中文显示名（对应 `normalize_key`）。
///
/// 注意：rdev 不提供布局层字符（只给物理键码），因此没有 pynput 的 char
/// 属性与 Ctrl 控制字符问题。组合键拆分（Ctrl+D = 左Ctrl + D 各 1 次）
/// 由物理键天然保证——每个 KeyPress 对应一个物理键。
pub fn normalize_key(key: &Key) -> String {
    if let Some(n) = letter_name(key) {
        return n;
    }
    if let Some(n) = digit_name(key) {
        return n;
    }
    if let Some(n) = symbol_name(key) {
        return n;
    }
    if let Some(n) = kp_name(key) {
        return n;
    }
    key_display_name(key)
}

/// 鼠标按键名映射（对应 `_MOUSE_BUTTON_MAP`）。
pub fn normalize_mouse_button(button: &Button) -> String {
    match button {
        Button::Left => "鼠标左键".to_string(),
        Button::Right => "鼠标右键".to_string(),
        Button::Middle => "鼠标中键".to_string(),
        Button::Unknown(code) => format!("鼠标{}", code),
    }
}

/// 滚轮方向：dy>0 向上，dy<0 向下
fn scroll_direction(dy: i64) -> &'static str {
    if dy > 0 {
        "上"
    } else {
        "下"
    }
}

/// 输入事件回调类型（番茄钟 / 护眼提醒等）。
type KeyCallback = Arc<dyn Fn(&str) + Send + Sync>;

/// 输入监听器。
pub struct InputListener {
    config: &'static FocusFlowConfig,
    /// 按下状态的按键集合：{按键名: 按下时刻}
    pressed: Mutex<HashMap<String, Instant>>,
    /// 滚轮合并状态
    scroll: Mutex<(Instant, &'static str)>,
    /// 暂停状态
    paused: Mutex<bool>,
    /// 事件回调（番茄钟 / 护眼提醒）
    key_callbacks: Mutex<Vec<KeyCallback>>,
    /// 监听线程是否存活
    alive: Arc<Mutex<bool>>,
}

impl InputListener {
    pub fn new(config: &'static FocusFlowConfig) -> Arc<Self> {
        Arc::new(Self {
            config,
            pressed: Mutex::new(HashMap::new()),
            scroll: Mutex::new((Instant::now() - Duration::from_secs(10), "上")),
            paused: Mutex::new(false),
            key_callbacks: Mutex::new(Vec::new()),
            alive: Arc::new(Mutex::new(false)),
        })
    }

    /// 注册输入回调（每个有效键鼠事件触发一次）。
    pub fn add_key_callback(&self, cb: Arc<dyn Fn(&str) + Send + Sync>) {
        self.key_callbacks.lock().unwrap().push(cb);
    }

    pub fn is_paused(&self) -> bool {
        *self.paused.lock().unwrap()
    }

    pub fn set_paused(&self, paused: bool) {
        let mut p = self.paused.lock().unwrap();
        if *p == paused {
            return;
        }
        *p = paused;
        drop(p);
        if paused {
            self.pressed.lock().unwrap().clear();
            let mut s = self.scroll.lock().unwrap();
            *s = (Instant::now() - Duration::from_secs(10), "上");
        }
        tracing::info!("监听已 {}", if paused { "暂停" } else { "恢复" });
    }

    pub fn toggle_pause(&self) -> bool {
        let new_state = !self.is_paused();
        self.set_paused(new_state);
        new_state
    }

    /// 长按自动重复过滤：按键已在按下集合中（且未超 stale 时长）视为重复，不计数。
    fn is_new_press(&self, key_name: &str) -> bool {
        let stale = self
            .config
            .get_float("listener", "key_repeat_stale_seconds", 15.0)
            .max(0.1);
        let now = Instant::now();
        let mut pressed = self.pressed.lock().unwrap();
        // 安全阀：集合过大时清理超时残留
        if pressed.len() > 256 {
            pressed.retain(|_, t| now.duration_since(*t) <= Duration::from_secs_f64(stale));
        }
        match pressed.get(key_name) {
            None => {
                pressed.insert(key_name.to_string(), now);
                true
            }
            Some(last) if now.duration_since(*last) > Duration::from_secs_f64(stale) => {
                pressed.insert(key_name.to_string(), now);
                true
            }
            Some(_) => false,
        }
    }

    /// 滚轮连续滚动合并：窗口内同方向只计 1 次。
    fn is_new_scroll_burst(&self, direction: &'static str) -> bool {
        let window = self
            .config
            .get_float("listener", "scroll_burst_window", 0.8)
            .max(0.01);
        let now = Instant::now();
        let mut scroll = self.scroll.lock().unwrap();
        let is_new = now.duration_since(scroll.0) > Duration::from_secs_f64(window)
            || scroll.1 != direction;
        *scroll = (now, direction);
        is_new
    }

    /// 处理单个键鼠事件：记录到数据库 + 触发回调。
    fn record_event(&self, db: &Database, key_name: &str) {
        if self.is_paused() {
            return;
        }
        let ts = SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_secs() as i64)
            .unwrap_or(0);
        db.record_key(key_name, ts);
        // 通知回调（番茄钟 / 护眼提醒）
        let callbacks = self.key_callbacks.lock().unwrap();
        for cb in callbacks.iter() {
            cb(key_name);
        }
    }

    /// 处理一个事件（供监听线程调用）。
    fn process_event(&self, db: &Database, event: &Event) {
        // 鼠标统计开关
        let mouse_enabled = self.config.get_bool("listener", "mouse_enabled", true);
        let ignore_key_repeat = self.config.get_bool("listener", "ignore_key_repeat", true);
        let ignore_modifiers = self.config.get_bool("listener", "ignore_modifier_keys", false);
        let ignore_functions = self.config.get_bool("listener", "ignore_function_keys", false);

        match &event.event_type {
            EventType::KeyPress(key) => {
                // 过滤修饰键/功能键
                if ignore_modifiers && is_modifier(key) {
                    return;
                }
                if ignore_functions && is_function_key(key) {
                    return;
                }
                let name = normalize_key(key);
                // 长按自动重复过滤
                if ignore_key_repeat && !self.is_new_press(&name) {
                    return;
                }
                self.record_event(db, &name);
            }
            EventType::KeyRelease(key) => {
                let name = normalize_key(key);
                self.pressed.lock().unwrap().remove(&name);
            }
            EventType::ButtonPress(button) => {
                if !mouse_enabled {
                    return;
                }
                let name = normalize_mouse_button(button);
                self.record_event(db, &name);
            }
            EventType::ButtonRelease(_) => {}
            EventType::Wheel { delta_y, .. } => {
                if !mouse_enabled {
                    return;
                }
                if *delta_y == 0 {
                    return;
                }
                let direction = scroll_direction(*delta_y);
                // 连续滚动合并
                if !self.is_new_scroll_burst(direction) {
                    return;
                }
                let name = format!("滚轮{}滑", direction);
                self.record_event(db, &name);
            }
            EventType::MouseMove { .. } => {}
        }
    }

    /// 启动监听（在后台线程运行）。
    pub fn start(self: &Arc<Self>, db: Arc<Database>) {
        let mut alive = self.alive.lock().unwrap();
        if *alive {
            return;
        }
        *alive = true;
        drop(alive);

        let this = Arc::clone(self);
        thread::Builder::new()
            .name("input-listener".into())
            .spawn(move || {
                let callback = move |event: Event| {
                    this.process_event(&db, &event);
                };
                if let Err(e) = listen(callback) {
                    tracing::error!("键鼠监听启动失败: {e:?}");
                }
            })
            .expect("启动输入监听线程失败");
        tracing::info!(
            "键鼠监听已启动 (ignore_modifiers={}, ignore_functions={}, mouse_enabled={})",
            self.config.get_bool("listener", "ignore_modifier_keys", false),
            self.config.get_bool("listener", "ignore_function_keys", false),
            self.config.get_bool("listener", "mouse_enabled", true),
        );
    }

    /// 停止监听。
    pub fn stop(&self) {
        *self.alive.lock().unwrap() = false;
        tracing::info!("键鼠监听已停止");
    }
}
