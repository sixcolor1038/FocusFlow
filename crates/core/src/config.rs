//! 配置管理。
//!
//! 镜像 Python 版 `config.py`：
//! - 读取/生成 `config.ini`（与 Python 版同格式，兼容用户既有配置）
//! - 缺失的 section/key 自动补默认值并回写
//! - 提供类型化读取 API 与线程安全的写入 API
//!
//! 注意：Python 版配置大量使用中文值与按键名，config crate 需按 UTF-8 处理。

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};


use crate::paths;

/// 与 Python 版 `config.py` 中 DEFAULT_CONFIG 一致的默认配置。
pub fn default_config() -> HashMap<String, HashMap<String, String>> {
    let mut map = HashMap::new();
    let mut s = |section: &str, items: &[(&str, &str)]| {
        let inner: HashMap<String, String> = items
            .iter()
            .map(|(k, v)| (k.to_string(), v.to_string()))
            .collect();
        map.insert(section.to_string(), inner);
    };

    s("database", &[
        ("batch_size", "50"),
        ("flush_interval", "10"),
        ("backup_on_exit", "true"),
        ("max_backups", "5"),
        ("auto_vacuum_days", "7"),
        ("yearly_archive", "true"),
    ]);
    s("stats", &[("cpm_window", "60"), ("today_count_cache_ttl", "10")]);
    s("listener", &[
        ("ignore_modifier_keys", "false"),
        ("ignore_function_keys", "false"),
        ("ignore_key_repeat", "true"),
        ("key_repeat_stale_seconds", "15"),
        ("mouse_enabled", "true"),
        ("scroll_burst_window", "0.8"),
    ]);
    s("gui", &[
        ("refresh_interval", "2"),
        ("full_refresh_interval", "10"),
        ("show_first_run_tip", "true"),
        ("theme", "light"),
        ("show_trend_chart", "true"),
        ("show_key_groups", "true"),
        ("start_to_tray", "true"),
        ("font", "hei"),
    ]);
    s("hotkey", &[
        ("enabled", "false"),
        ("toggle_window", "ctrl+shift+f"),
    ]);
    s("floating", &[("enabled", "true"), ("opacity", "0.85")]);
    s("tray", &[("tooltip_interval", "5")]);
    s("pomodoro", &[
        ("enabled", "true"),
        ("work_minutes", "25"),
        ("break_minutes", "5"),
        ("auto_break", "true"),
    ]);
    s("rest", &[
        ("enabled", "true"),
        ("window_minutes", "30"),
        ("key_threshold", "10000"),
        ("cooldown_minutes", "10"),
        ("rest_seconds", "20"),
        ("check_interval", "10"),
    ]);
    map
}

/// 线程安全的配置管理器。
///
/// 通过 `FocusFlowConfig::instance()` 获得进程级单例（镜像 Python 的全局 `config`）。
pub struct FocusFlowConfig {
    /// section -> (key -> value)
    values: Mutex<HashMap<String, HashMap<String, String>>>,
    path: PathBuf,
}

impl FocusFlowConfig {
    /// 从指定路径加载配置；`path` 不存在时生成默认配置并保存。
    pub fn load(path: impl Into<PathBuf>) -> anyhow::Result<Self> {
        let path = path.into();
        let defaults = default_config();
        let mut values = defaults.clone();

        if path.exists() {
            if let Ok(text) = std::fs::read_to_string(&path) {
                // 解析 INI：兼容 Python configparser 的 `#`/`;` 注释与 `key = value` 语法。
                let mut current_section: Option<String> = None;
                for line in text.lines() {
                    let line = line.trim();
                    if line.is_empty() || line.starts_with('#') || line.starts_with(';') {
                        continue;
                    }
                    if line.starts_with('[') && line.ends_with(']') {
                        current_section = Some(line[1..line.len() - 1].trim().to_string());
                        continue;
                    }
                    let Some(section) = current_section.clone() else {
                        continue;
                    };
                    if let Some(eq) = line.find('=') {
                        let key = line[..eq].trim().to_string();
                        let val = line[eq + 1..].trim().to_string();
                        // 去掉可能带有的引号
                        let val = val
                            .strip_prefix('"')
                            .and_then(|v| v.strip_suffix('"'))
                            .unwrap_or(&val)
                            .to_string();
                        values
                            .entry(section.clone())
                            .or_default()
                            .insert(key, val);
                    }
                }
            }
        }

        let cfg = Self {
            values: Mutex::new(values),
            path,
        };
        cfg.save()?;
        Ok(cfg)
    }

    /// 保存当前配置到文件（缺失 section/key 已补默认值）。
    pub fn save(&self) -> anyhow::Result<()> {
        let values = self.values.lock().unwrap();
        let mut out = String::new();
        // 固定 section 顺序，与 Python 版一致，便于阅读与 diff。
        let order = [
            "database",
            "stats",
            "listener",
            "gui",
            "hotkey",
            "floating",
            "tray",
            "pomodoro",
            "rest",
        ];
        let mut sections: Vec<&String> = values.keys().collect();
        sections.sort_by_key(|s| order.iter().position(|o| o == s).unwrap_or(usize::MAX));
        for section in sections {
            out.push_str(&format!("[{}]\n", section));
            let mut keys: Vec<&String> = values[section].keys().collect();
            keys.sort();
            for key in keys {
                out.push_str(&format!("{} = {}\n", key, values[section][key]));
            }
            out.push('\n');
        }
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent).ok();
        }
        std::fs::write(&self.path, out)?;
        Ok(())
    }

    // ---------- 读取 API ----------

    fn get_raw(&self, section: &str, key: &str) -> String {
        let values = self.values.lock().unwrap();
        values
            .get(section)
            .and_then(|s| s.get(key))
            .cloned()
            .unwrap_or_default()
    }

    /// 读取字符串值，缺省返回空串。
    pub fn get(&self, section: &str, key: &str) -> String {
        self.get_raw(section, key)
    }

    /// 读取字符串值，缺省返回 `default`。
    pub fn get_or(&self, section: &str, key: &str, default: &str) -> String {
        let v = self.get_raw(section, key);
        if v.is_empty() {
            default.to_string()
        } else {
            v
        }
    }

    /// 读取整数。
    pub fn get_int(&self, section: &str, key: &str, default: i64) -> i64 {
        self.get_raw(section, key)
            .trim()
            .parse::<i64>()
            .unwrap_or(default)
    }

    /// 读取浮点数。
    pub fn get_float(&self, section: &str, key: &str, default: f64) -> f64 {
        self.get_raw(section, key)
            .trim()
            .parse::<f64>()
            .unwrap_or(default)
    }

    /// 读取布尔值（`true/1/yes/on` 视为真）。
    pub fn get_bool(&self, section: &str, key: &str, default: bool) -> bool {
        let v = self.get_raw(section, key).trim().to_lowercase();
        match v.as_str() {
            "true" | "1" | "yes" | "on" => true,
            "false" | "0" | "no" | "off" => false,
            "" => default,
            _ => default,
        }
    }

    // ---------- 写入 API ----------

    /// 设置字符串值并持久化。
    pub fn set(&self, section: &str, key: &str, value: &str) -> anyhow::Result<()> {
        {
            let mut values = self.values.lock().unwrap();
            values
                .entry(section.to_string())
                .or_default()
                .insert(key.to_string(), value.to_string());
        }
        self.save()
    }
}

/// 全局配置单例（与 Python 版全局 `config` 对应）。
///
/// 首次访问时加载，仅一次。
pub static INSTANCE: OnceLock<FocusFlowConfig> = OnceLock::new();

/// 获取全局配置实例；未初始化时用默认路径加载并初始化。
pub fn instance() -> &'static FocusFlowConfig {
    INSTANCE.get_or_init(|| {
        FocusFlowConfig::load(paths::config_path())
            .unwrap_or_else(|e| panic!("加载配置失败: {e}"))
    })
}

/// 显式初始化配置（供需要自定义路径/错误处理的场景）。
pub fn init_with_path(path: impl AsRef<Path>) -> anyhow::Result<&'static FocusFlowConfig> {
    // 先构造，再放入 OnceLock
    let cfg = FocusFlowConfig::load(path.as_ref().to_path_buf())?;
    let _ = INSTANCE.set(cfg);
    Ok(INSTANCE.get().unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Arc;

    #[test]
    fn defaults_and_overrides() {
        let dir = std::env::temp_dir().join("ff_rs_cfg_test");
        std::fs::create_dir_all(&dir).ok();
        let path = dir.join("config.ini");
        std::fs::write(
            &path,
            "[gui]\ntheme = dark\nrefresh_interval = 5\n",
        )
        .unwrap();

        let cfg = FocusFlowConfig::load(&path).unwrap();
        // 覆盖值
        assert_eq!(cfg.get("gui", "theme"), "dark");
        assert_eq!(cfg.get_int("gui", "refresh_interval", 0), 5);
        // 默认值补齐
        assert_eq!(cfg.get_bool("listener", "ignore_key_repeat", false), true);
        assert_eq!(cfg.get("hotkey", "toggle_window"), "ctrl+shift+f");
        assert_eq!(cfg.get_int("rest", "key_threshold", 0), 10000);
        assert_eq!(cfg.get_float("floating", "opacity", 0.0), 0.85);

        let _ = Arc::new(());
        std::fs::remove_dir_all(&dir).ok();
    }
}
