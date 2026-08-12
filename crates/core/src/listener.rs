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
fn key_display_name(key: &Key) -> &'static str {
    match key {
        Key::Space => "空格",
        Key::Return => "回车",
        Key::Backspace => "退格",
        Key::Tab => "Tab",
        Key::ShiftLeft => "左Shift",
        Key::ShiftRight => "右Shift",
        Key::ControlLeft => "左Ctrl",
        Key::ControlRight => "右Ctrl",
        Key::Alt => "左Alt",
        Key::AltGr => "AltGr",
        Key::MetaLeft => "左Win",
        Key::MetaRight => "右Win",
        Key::CapsLock => "CapsLock",
        Key::Escape => "Esc",
        Key::Delete => "Delete",
        Key::Home => "Home",
        Key::End => "End",
        Key::PageUp => "PageUp",
        Key::PageDown => "PageDown",
        Key::Insert => "Insert",
        Key::NumLock => "NumLock",
        Key::ScrollLock => "ScrollLock",
        Key::PrintScreen => "PrintScreen",
        Key::Pause => "Pause",
        Key::UpArrow => "↑",
        Key::DownArrow => "↓",
        Key::LeftArrow => "←",
        Key::RightArrow => "→",
        Key::F1 => "F1",
        Key::F2 => "F2",
        Key::F3 => "F3",
        Key::F4 => "F4",
        Key::F5 => "F5",
        Key::F6 => "F6",
        Key::F7 => "F7",
        Key::F8 => "F8",
        Key::F9 => "F9",
        Key::F10 => "F10",
        Key::F11 => "F11",
        Key::F12 => "F12",
        // Unknown 键：无静态名，调用方用 Debug 格式
        Key::Unknown(_) => "Unknown",
        _ => "Unknown",
    }
}

/// 处理热路径：按键名（优先零分配静态串，未知键才分配）。
pub fn normalize_key(key: &Key) -> String {
    if let Some(n) = letter_name(key) {
        return n.to_string();
    }
    if let Some(n) = digit_name(key) {
        return n.to_string();
    }
    if let Some(n) = symbol_name(key) {
        return n.to_string();
    }
    if let Some(n) = kp_name(key) {
        return n.to_string();
    }
    let name = key_display_name(key);
    if name == "Unknown" {
        format!("{:?}", key)
    } else {
        name.to_string()
    }
}

/// 字母键映射：rdev `KeyA`..`KeyZ` -> "A".."Z"（零分配）
fn letter_name(key: &Key) -> Option<&'static str> {
    use Key::*;
    Some(match key {
        KeyA => "A",
        KeyB => "B",
        KeyC => "C",
        KeyD => "D",
        KeyE => "E",
        KeyF => "F",
        KeyG => "G",
        KeyH => "H",
        KeyI => "I",
        KeyJ => "J",
        KeyK => "K",
        KeyL => "L",
        KeyM => "M",
        KeyN => "N",
        KeyO => "O",
        KeyP => "P",
        KeyQ => "Q",
        KeyR => "R",
        KeyS => "S",
        KeyT => "T",
        KeyU => "U",
        KeyV => "V",
        KeyW => "W",
        KeyX => "X",
        KeyY => "Y",
        KeyZ => "Z",
        _ => return None,
    })
}

/// 数字键映射：`Num0`..`Num9` -> "0".."9"（零分配）
fn digit_name(key: &Key) -> Option<&'static str> {
    use Key::*;
    Some(match key {
        Num0 => "0",
        Num1 => "1",
        Num2 => "2",
        Num3 => "3",
        Num4 => "4",
        Num5 => "5",
        Num6 => "6",
        Num7 => "7",
        Num8 => "8",
        Num9 => "9",
        _ => return None,
    })
}

/// 其他符号键映射（零分配）
fn symbol_name(key: &Key) -> Option<&'static str> {
    use Key::*;
    Some(match key {
        BackQuote => "`",
        Minus => "-",
        Equal => "=",
        LeftBracket => "[",
        RightBracket => "]",
        SemiColon => ";",
        Quote => "'",
        BackSlash => "\\",
        IntlBackslash => "\\",
        Comma => ",",
        Dot => ".",
        Slash => "/",
        _ => return None,
    })
}

/// 小键盘键映射（零分配）
fn kp_name(key: &Key) -> Option<&'static str> {
    use Key::*;
    Some(match key {
        Kp0 => "数字键盘0",
        Kp1 => "数字键盘1",
        Kp2 => "数字键盘2",
        Kp3 => "数字键盘3",
        Kp4 => "数字键盘4",
        Kp5 => "数字键盘5",
        Kp6 => "数字键盘6",
        Kp7 => "数字键盘7",
        Kp8 => "数字键盘8",
        Kp9 => "数字键盘9",
        KpReturn => "数字键盘回车",
        KpMinus => "数字键盘-",
        KpPlus => "数字键盘+",
        KpMultiply => "数字键盘*",
        KpDivide => "数字键盘/",
        KpDelete => "Delete",
        _ => return None,
    })
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
        tracing::debug!("record_event: {key_name} ts={ts}");
        db.record_key(key_name, ts);
        // 记录 CPM（当前速度统计）
        crate::stats::cpm(self.config).record();
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
            .spawn(move || loop {
                if !*this.alive.lock().unwrap() {
                    break;
                }
                let this2 = Arc::clone(&this);
                let db2 = Arc::clone(&db);
                let result = listen(move |event: Event| {
                    this2.process_event(&db2, &event);
                });
                if !*this.alive.lock().unwrap() {
                    break;
                }
                match result {
                    Ok(()) => tracing::warn!("键鼠监听意外结束，5 秒后自动重启"),
                    Err(e) => tracing::error!("键鼠监听失败，5 秒后自动重启: {e:?}"),
                }
                thread::sleep(Duration::from_secs(5));
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
