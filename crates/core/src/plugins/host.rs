//! 插件宿主 API：将核心功能注册为 Lua 全局表 `focusflow`。
//!
//! 插件通过 `focusflow.*` 访问：
//! - `focusflow.stats(period)` -> total, keys 表
//! - `focusflow.today_count()` -> 今日计数
//! - `focusflow.now()` -> Unix 秒
//! - `focusflow.log(msg)` -> 日志
//! - `focusflow.daily_counts(days)` -> 每日统计
//! - `focusflow.available_years()` -> 年份列表
//! - `focusflow.config_get("section.key")` -> 配置值

use std::sync::Arc;

use mlua::{Lua, Table, Value};

use crate::config::FocusFlowConfig;
use crate::db;

/// 注册宿主 API 到 Lua 全局表 `focusflow`，返回该表。
pub fn register_host_api(
    lua: &Lua,
    config: &'static FocusFlowConfig,
    database: Arc<db::Database>,
) -> mlua::Result<Table> {
    let host = lua.create_table()?;

    // 统计查询：period = -1 今日, 0 总计, N 天数。返回 (total, keys表)
    let db_stats = Arc::clone(&database);
    let stats_fn = lua.create_function(move |lua, period: i64| {
        let (total, key_stats) = match period {
            -1 => db::get_stats_by_date(chrono::Local::now().date_naive()),
            0 => db::get_stats(None, None),
            n => db::get_stats(Some(n), None),
        };
        let keys = lua.create_table()?;
        for (k, v) in &key_stats {
            keys.set(k.as_str(), *v)?;
        }
        let _ = &db_stats;
        Ok((total, keys))
    })?;
    host.set("stats", stats_fn)?;

    // 今日计数
    let db_today = Arc::clone(&database);
    let today_fn = lua.create_function(move |_, ()| {
        Ok(db::get_today_count(db_today.writer().map(|w| w.as_ref())))
    })?;
    host.set("today_count", today_fn)?;

    // 当前 Unix 秒
    let now_fn = lua.create_function(|_, ()| Ok(chrono::Utc::now().timestamp()))?;
    host.set("now", now_fn)?;

    // 日志
    let log_fn = lua.create_function(|_, msg: String| {
        tracing::info!("[plugin] {msg}");
        Ok(())
    })?;
    host.set("log", log_fn)?;

    // 每日统计（趋势）
    let daily_fn = lua.create_function(|lua, days: i64| {
        let daily = db::get_daily_counts(days.max(1), None);
        let t = lua.create_table()?;
        for (i, (date, count)) in daily.iter().enumerate() {
            let row = lua.create_table()?;
            row.set("date", date.as_str())?;
            row.set("count", *count)?;
            t.set(i + 1, row)?;
        }
        Ok(t)
    })?;
    host.set("daily_counts", daily_fn)?;

    // 可用年份
    let years_fn = lua.create_function(|_, ()| Ok(db::available_years()))?;
    host.set("available_years", years_fn)?;

    // 配置读取：key = "section.key"
    let cfg_fn = lua.create_function(move |_, key: String| {
        let (section, key) = key.split_once('.').unwrap_or(("", &key));
        let v = config.get(section, key);
        Ok(v)
    })?;
    host.set("config_get", cfg_fn)?;

    // 调试：返回环境信息
    let info_fn = lua.create_function(|_, ()| {
        Ok(format!(
            "FocusFlow {} · {}",
            crate::paths::APP_VERSION,
            crate::paths::app_dir().display()
        ))
    })?;
    host.set("app_info", info_fn)?;

    // 暴露一个用于验证 Lua 值转换的辅助（测试用）
    let _ = Value::Nil;

    // 注册为全局 `focusflow`
    lua.globals().set("focusflow", host.clone())?;
    Ok(host)
}
