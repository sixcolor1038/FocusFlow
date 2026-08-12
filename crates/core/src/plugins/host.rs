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

use crate::accounting;
use crate::config::FocusFlowConfig;
use crate::db;
use crate::pomodoro::{self, PomodoroTimer};
use crate::scheduler;

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

    // ---- 番茄钟 API ----
    // 共享番茄钟实例（进程级单例）
    static POMODORO: std::sync::OnceLock<Arc<PomodoroTimer>> = std::sync::OnceLock::new();
    let pomo = Arc::clone(POMODORO.get_or_init(|| {
        let _ = pomodoro::init_db();
        PomodoroTimer::new()
    }));

    let pomo_state = Arc::clone(&pomo);
    let pomo_state_fn = lua.create_function(move |lua, ()| {
        let info = pomo_state.get_state_info();
        let t = lua.create_table()?;
        for (k, v) in &info {
            t.set(k.as_str(), *v)?;
        }
        Ok(t)
    })?;
    host.set("pomodoro_state", pomo_state_fn)?;

    let pomo_start_work = Arc::clone(&pomo);
    host.set(
        "pomodoro_start_work",
        lua.create_function(move |_, ()| {
            pomo_start_work.start_work();
            Ok(())
        })?,
    )?;

    let pomo_start_break = Arc::clone(&pomo);
    host.set(
        "pomodoro_start_break",
        lua.create_function(move |_, ()| {
            pomo_start_break.start_break();
            Ok(())
        })?,
    )?;

    let pomo_toggle = Arc::clone(&pomo);
    host.set(
        "pomodoro_toggle_pause",
        lua.create_function(move |_, ()| Ok(pomo_toggle.toggle_pause()))?,
    )?;

    let pomo_stop = Arc::clone(&pomo);
    host.set(
        "pomodoro_stop",
        lua.create_function(move |_, ()| {
            pomo_stop.stop();
            Ok(())
        })?,
    )?;

    let pomo_skip = Arc::clone(&pomo);
    host.set(
        "pomodoro_skip",
        lua.create_function(move |_, ()| {
            pomo_skip.skip();
            Ok(())
        })?,
    )?;

    let pomo_durations = Arc::clone(&pomo);
    host.set(
        "pomodoro_set_durations",
        lua.create_function(move |_, (work, brk): (i64, i64)| {
            pomo_durations.set_durations(work, brk);
            Ok(())
        })?,
    )?;

    // 番茄钟历史
    let sessions_fn = lua.create_function(|lua, limit: i64| {
        let sessions = pomodoro::get_recent_sessions(limit.clamp(1, 100));
        let t = lua.create_table()?;
        for (i, s) in sessions.iter().enumerate() {
            let row = lua.create_table()?;
            row.set("id", s.id)?;
            row.set("type", s.rtype.as_str())?;
            row.set("start", s.start_time.as_str())?;
            row.set("end", s.end_time.as_str())?;
            row.set("actual", s.actual_seconds)?;
            row.set("keys", s.key_count)?;
            t.set(i + 1, row)?;
        }
        Ok(t)
    })?;
    host.set("pomodoro_sessions", sessions_fn)?;

    let summary_fn = lua.create_function(|_, ()| {
        let (count, keys, secs) = pomodoro::today_summary();
        Ok((count, keys, secs))
    })?;
    host.set("pomodoro_summary", summary_fn)?;

    // 番茄钟按键联动（监听器回调调用）
    let pomo_record = Arc::clone(&pomo);
    host.set(
        "pomodoro_record_key",
        lua.create_function(move |_, key: String| {
            pomo_record.record_key(&key);
            Ok(())
        })?,
    )?;

    // ---- 定时任务 API ----
    // 启动调度器（进程级单例）
    static SCHEDULER: std::sync::OnceLock<Arc<scheduler::Scheduler>> = std::sync::OnceLock::new();
    let _sched = SCHEDULER.get_or_init(scheduler::Scheduler::start);

    let tasks_fn = lua.create_function(|lua, ()| {
        let tasks = scheduler::get_all_tasks();
        let t = lua.create_table()?;
        for (i, task) in tasks.iter().enumerate() {
            let row = lua.create_table()?;
            row.set("id", task.id)?;
            row.set("name", task.name.as_str())?;
            row.set("target", task.target_path.as_str())?;
            row.set("args", task.args.as_str())?;
            row.set("type", task.schedule_type.as_str())?;
            row.set("time", task.schedule_time.as_str())?;
            row.set("enabled", task.enabled)?;
            row.set("last_run", task.last_run.clone().unwrap_or_default())?;
            row.set("desc", scheduler::describe_schedule(&task.schedule_type, &task.schedule_time))?;
            t.set(i + 1, row)?;
        }
        Ok(t)
    })?;
    host.set("scheduler_tasks", tasks_fn)?;

    let add_fn = lua.create_function(
        |_, (name, target, args, stype, stime, enabled): (String, String, String, String, String, bool)| {
            Ok(scheduler::add_task(&name, &target, &args, &stype, &stime, enabled))
        },
    )?;
    host.set("scheduler_add", add_fn)?;

    let update_fn = lua.create_function(
        |_, (id, name, target, args, stype, stime, enabled): (i64, String, String, String, String, String, bool)| {
            let r = scheduler::update_task(
                id,
                Some(&name),
                Some(&target),
                Some(&args),
                Some(&stype),
                Some(&stime),
                Some(enabled),
            );
            Ok(r)
        },
    )?;
    host.set("scheduler_update", update_fn)?;

    let delete_fn = lua.create_function(|_, id: i64| Ok(scheduler::delete_task(id)))?;
    host.set("scheduler_delete", delete_fn)?;

    let toggle_fn = lua.create_function(|_, (id, enabled): (i64, bool)| {
        scheduler::toggle_task(id, enabled);
        Ok(())
    })?;
    host.set("scheduler_toggle", toggle_fn)?;

    let validate_fn = lua.create_function(
        |_, (stype, stime): (String, String)| {
            let (ok, msg) = scheduler::validate_schedule(&stype, &stime);
            Ok((ok, msg))
        },
    )?;
    host.set("scheduler_validate", validate_fn)?;

    // ---- 记账本 API ----
    let _ = accounting::init_db();

    let acc_add = lua.create_function(
        |_, (rtype, item, store, date, amount, cat, sub): (String, String, String, String, f64, String, String)| {
            Ok(accounting::add_expense(
                &rtype,
                &item,
                if store.is_empty() { None } else { Some(store.as_str()) },
                &date,
                amount,
                if cat.is_empty() { None } else { Some(cat.as_str()) },
                if sub.is_empty() { None } else { Some(sub.as_str()) },
                None,
            ))
        },
    )?;
    host.set("accounting_add", acc_add)?;

    let acc_list = lua.create_function(|lua, limit: i64| {
        let expenses = accounting::get_all_expenses(limit.clamp(1, 500));
        let t = lua.create_table()?;
        for (i, e) in expenses.iter().enumerate() {
            let row = lua.create_table()?;
            row.set("id", e.id)?;
            row.set("type", e.rtype.as_str())?;
            row.set("item", e.item_name.as_str())?;
            row.set("date", e.purchase_date.as_str())?;
            row.set("amount", e.amount)?;
            row.set("category", e.category.clone().unwrap_or_default())?;
            row.set("subcategory", e.subcategory.clone().unwrap_or_default())?;
            t.set(i + 1, row)?;
        }
        Ok(t)
    })?;
    host.set("accounting_list", acc_list)?;

    let acc_delete = lua.create_function(|_, id: i64| Ok(accounting::delete_expense(id)))?;
    host.set("accounting_delete", acc_delete)?;

    let acc_summary = lua.create_function(|_, ym: String| {
        let (expense, income) = accounting::monthly_summary(&ym);
        Ok((expense, income))
    })?;
    host.set("accounting_summary", acc_summary)?;

    // 注册为全局 `focusflow`
    lua.globals().set("focusflow", host.clone())?;
    Ok(host)
}
