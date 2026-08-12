//! 统计查询 API。
//!
//! 镜像 Python 版 `database.py` 的查询函数：
//! - 今日计数 / 指定周期 / 指定年度 / 指定日期
//! - 每日计数（趋势图）/ 小时分布 / 星期分布
//! - 年度列表（带缓存）
//! - 跨年查询使用 ATTACH + UNION ALL
//!
//! 所有查询打开只读连接，不阻塞写入线程。

use std::collections::HashMap;
use std::time::{Duration, Instant};

use chrono::{Datelike, Days, Local, TimeZone};
use rusqlite::Connection;

use crate::db::connection;
use crate::paths;

/// 年度列表缓存 TTL（秒）
const YEARS_CACHE_TTL: Duration = Duration::from_secs(30);

/// 年度缓存：app_dir -> (时间, 年份列表)
type YearsCache = std::sync::Mutex<HashMap<String, (Instant, Vec<i32>)>>;
static YEARS_CACHE: std::sync::OnceLock<YearsCache> = std::sync::OnceLock::new();

fn years_cache() -> &'static YearsCache {
    YEARS_CACHE.get_or_init(|| std::sync::Mutex::new(HashMap::new()))
}

fn cache_key() -> String {
    paths::data_dir().to_string_lossy().to_string()
}

/// 使年度列表缓存失效（归档/初始化后调用）。
pub fn invalidate_years_cache() {
    let mut c = years_cache().lock().unwrap();
    c.insert(cache_key(), (Instant::now() - YEARS_CACHE_TTL, vec![]));
}

/// 获取所有有数据的年份列表（降序，带 30 秒缓存，按 app_dir 隔离）。
pub fn available_years() -> Vec<i32> {
    let key = cache_key();
    {
        let c = years_cache().lock().unwrap();
        if let Some(entry) = c.get(&key) {
            if entry.0.elapsed() < YEARS_CACHE_TTL {
                return entry.1.clone();
            }
        }
    }
    let mut years: Vec<i32> = Vec::new();
    if let Ok(entries) = std::fs::read_dir(paths::data_dir()) {
        for entry in entries.flatten() {
            if let Some(year) = paths::is_year_db_file(&entry.path()) {
                years.push(year);
            }
        }
    }
    years.sort_unstable_by(|a, b| b.cmp(a));
    let mut c = years_cache().lock().unwrap();
    c.insert(key, (Instant::now(), years.clone()));
    years
}

/// 查询今日按键数（Unix 时间戳范围）。
fn query_today_count() -> i64 {
    let start = local_day_start_ts(Local::now().date_naive());
    let end = start + 86_400;
    query_count_range(paths::current_year(), start, end)
}

/// 计算指定日期的本地时区 Unix 秒范围。
///
/// 镜像 Python 版 `time.mktime(datetime(y,m,d).timetuple())`：
/// 按本地时区把当日 00:00:00 转换为 Unix 秒。
pub fn date_range_ts(date: chrono::NaiveDate) -> (i64, i64) {
    let start = local_day_start_ts(date);
    (start, start + 86_400)
}

/// 本地日期转当日起始 Unix 秒（本地时区）。
fn local_day_start_ts(date: chrono::NaiveDate) -> i64 {
    let local_dt = chrono::Local
        .from_local_datetime(&date.and_hms_opt(0, 0, 0).expect("time"))
        .single()
        .expect("本地时区转换失败");
    local_dt.timestamp()
}

/// 本地时区相对 UTC 的偏移秒数（用于 SQL 整数日分组）。
fn local_utc_offset_seconds() -> i64 {
    // 本地时区偏移（如 UTC+8 = 28800 秒）
    Local::now().offset().local_minus_utc() as i64
}

/// 查询单个年份库时间范围内计数。
fn query_count_range(year: i32, start: i64, end: i64) -> i64 {
    let path = paths::year_db_path(year);
    let conn = match connection::open_ro(&path) {
        Ok(c) => c,
        Err(_) => return 0,
    };
    match table_exists(&conn) {
        false => 0,
        true => conn
            .query_row(
                "SELECT COUNT(*) FROM key_log WHERE timestamp >= ?1 AND timestamp < ?2",
                rusqlite::params![start, end],
                |r| r.get::<_, i64>(0),
            )
            .unwrap_or(0),
    }
}

fn table_exists(conn: &Connection) -> bool {
    conn.query_row(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='key_log'",
        [],
        |_| Ok(()),
    )
    .is_ok()
}

/// 获取今日按键数（写入器缓存优先，未启动写入器时查库）。
pub fn get_today_count(writer: Option<&crate::db::DbWriter>) -> i64 {
    if let Some(w) = writer {
        w.today_count() as i64
    } else {
        query_today_count()
    }
}

/// 根据查询范围确定年份列表（镜像 `_get_query_years`）。
fn query_years(days: Option<i64>, target_date: Option<chrono::NaiveDate>) -> Vec<i32> {
    if let Some(d) = target_date {
        return vec![d.year()];
    }
    if days.is_none() {
        return available_years();
    }
    let now = Local::now();
    let start = now.date_naive() - chrono::Days::new(days.unwrap() as u64);
    let years: Vec<i32> = (start.year()..=now.year()).collect();
    let available: std::collections::HashSet<i32> = available_years().into_iter().collect();
    let filtered: Vec<i32> = years.into_iter().filter(|y| available.contains(y)).collect();
    if filtered.is_empty() {
        vec![now.year()]
    } else {
        filtered
    }
}

/// 查询统计：返回 (总数, {键名: 次数})。
pub fn get_stats(days: Option<i64>, year: Option<i32>) -> (i64, HashMap<String, i64>) {
    if let Some(y) = year {
        return stats_single_year(y, days);
    }
    let years = query_years(days, None);
    if years.len() == 1 {
        return stats_single_year(years[0], days);
    }
    stats_multi_year(&years, days)
}

fn where_cutoff(days: Option<i64>) -> (String, Vec<i64>) {
    match days {
        Some(d) => {
            let cutoff = chrono::Utc::now().timestamp() - d * 86_400;
            ("WHERE timestamp >= ?1".to_string(), vec![cutoff])
        }
        None => (String::new(), vec![]),
    }
}

fn stats_single_year(year: i32, days: Option<i64>) -> (i64, HashMap<String, i64>) {
    let path = paths::year_db_path(year);
    let conn = match connection::open_ro(&path) {
        Ok(c) => c,
        Err(_) => return (0, HashMap::new()),
    };
    if !table_exists(&conn) {
        return (0, HashMap::new());
    }
    let (where_clause, params) = where_cutoff(days);
    let total: i64 = {
        let sql = format!("SELECT COUNT(*) FROM key_log {where_clause}");
        conn.query_row(&sql, rusqlite::params_from_iter(params.iter()), |r| r.get(0))
            .unwrap_or(0)
    };
    let map = {
        let sql = format!(
            "SELECT key_name, COUNT(*) as cnt FROM key_log {where_clause} GROUP BY key_name ORDER BY cnt DESC"
        );
        let mut stmt = conn.prepare(&sql).unwrap();
        let rows = stmt
            .query_map(rusqlite::params_from_iter(params.iter()), |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            })
            .unwrap();
        rows.flatten().collect()
    };
    (total, map)
}

/// 跨年查询：第一个年份库为主库，ATTACH 其他。
fn stats_multi_year(years: &[i32], days: Option<i64>) -> (i64, HashMap<String, i64>) {
    // 稳健性：无年份时返回空
    let Some(main_year) = years.first() else {
        return (0, HashMap::new());
    };
    let main_year = *main_year;
    let path = paths::year_db_path(main_year);
    let conn = match connection::open_ro(&path) {
        Ok(c) => c,
        Err(_) => return (0, HashMap::new()),
    };
    if !table_exists(&conn) {
        return (0, HashMap::new());
    }

    // ATTACH 其他年份库
    let mut aliases: Vec<String> = Vec::new();
    for y in &years[1..] {
        let alias = format!("y{y}");
        let ypath = paths::year_db_path(*y);
        if ypath.exists()
            && conn
                .execute(
                    &format!("ATTACH DATABASE ?1 AS {alias}"),
                    rusqlite::params![ypath.to_str().unwrap()],
                )
                .is_ok()
        {
            aliases.push(alias);
        }
    }

    let (where_clause, params) = where_cutoff(days);
    // 构建 UNION ALL 查询（每个子查询必须 GROUP BY key_name）
    let mut parts = vec![format!(
        "SELECT key_name, COUNT(*) as cnt FROM key_log {where_clause} GROUP BY key_name"
    )];
    for alias in &aliases {
        parts.push(format!(
            "SELECT key_name, COUNT(*) as cnt FROM {alias}.key_log {where_clause} GROUP BY key_name"
        ));
    }
    let union_sql = parts.join(" UNION ALL ");

    let all_params: Vec<i64> = params.repeat(1 + aliases.len());

    let total: i64 = {
        let sql = format!("SELECT SUM(cnt) FROM ({union_sql})");
        conn.query_row(&sql, rusqlite::params_from_iter(all_params.iter()), |r| r.get(0))
            .unwrap_or(0)
    };
    let map: HashMap<String, i64> = {
        let sql = format!(
            "SELECT key_name, SUM(cnt) as total FROM ({union_sql}) GROUP BY key_name ORDER BY total DESC"
        );
        let mut stmt = conn.prepare(&sql).unwrap();
        let rows = stmt
            .query_map(rusqlite::params_from_iter(all_params.iter()), |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            })
            .unwrap();
        rows.flatten().collect()
    };

    for alias in &aliases {
        let _ = conn.execute(&format!("DETACH DATABASE {alias}"), []);
    }
    (total, map)
}

/// 查询指定日期统计。
pub fn get_stats_by_date(target_date: chrono::NaiveDate) -> (i64, HashMap<String, i64>) {
    let path = paths::year_db_path(target_date.year());
    let conn = match connection::open_ro(&path) {
        Ok(c) => c,
        Err(_) => return (0, HashMap::new()),
    };
    if !table_exists(&conn) {
        return (0, HashMap::new());
    }
    let (start, end) = date_range_ts(target_date);
    let total: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM key_log WHERE timestamp >= ?1 AND timestamp < ?2",
            rusqlite::params![start, end],
            |r| r.get(0),
        )
        .unwrap_or(0);
    let map: HashMap<String, i64> = {
        let mut stmt = conn
            .prepare(
                "SELECT key_name, COUNT(*) as cnt FROM key_log WHERE timestamp >= ?1 AND timestamp < ?2 GROUP BY key_name ORDER BY cnt DESC",
            )
            .unwrap();
        let rows = stmt
            .query_map(rusqlite::params![start, end], |r| {
                Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?))
            })
            .unwrap();
        rows.flatten().collect()
    };
    (total, map)
}

/// 查询最近 N 天每日按键数：返回 [(YYYY-MM-DD, 次数)]。
pub fn get_daily_counts(days: i64, year: Option<i32>) -> Vec<(String, i64)> {
    let now = Local::now();
    let start = now.date_naive() - Days::new((days - 1).max(0) as u64);

    let mut years_to_query: Vec<i32> = match year {
        Some(y) => vec![y],
        None => {
            let years: Vec<i32> = (start.year()..=now.year()).collect();
            let available: std::collections::HashSet<i32> = available_years().into_iter().collect();
            let f: Vec<i32> = years.into_iter().filter(|y| available.contains(y)).collect();
            if f.is_empty() {
                vec![now.year()]
            } else {
                f
            }
        }
    };
    years_to_query.sort_unstable();

    // 初始化所有日期为 0
    let mut daily_map: HashMap<chrono::NaiveDate, i64> = HashMap::new();
    for i in 0..days {
        let d = start + chrono::Days::new(i as u64);
        daily_map.insert(d, 0);
    }

    let end_ts = local_day_start_ts(now.date_naive()) + 86_400;

    for y in &years_to_query {
        let path = paths::year_db_path(*y);
        if !path.exists() {
            continue;
        }
        let conn = match connection::open_ro(&path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        if !table_exists(&conn) {
            continue;
        }
        let start_ts = local_day_start_ts(start);
        // 优化：用本地时区偏移的整数分组代替 date() 函数（避免逐行函数调用）
        let tz_offset = local_utc_offset_seconds();
        let mut stmt = conn
            .prepare(
                &format!(
                    "SELECT (timestamp + {tz_offset}) / 86400 as day, COUNT(*) as cnt \
                     FROM key_log WHERE timestamp >= ?1 AND timestamp < ?2 \
                     GROUP BY day"
                ),
            )
            .unwrap();
        let rows = stmt
            .query_map(rusqlite::params![start_ts, end_ts], |r| {
                Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?))
            })
            .unwrap();
        for row in rows.flatten() {
            let day_epoch = row.0;
            let cnt = row.1;
            // day_epoch = 该日 00:00 的 UTC 秒（已含时区偏移）
            let d = chrono::DateTime::from_timestamp(day_epoch, 0)
                .map(|dt| dt.with_timezone(&chrono::Local).date_naive());
            if let Some(d) = d {
                if let Some(e) = daily_map.get_mut(&d) {
                    *e += cnt;
                }
            }
        }
    }

    let mut result: Vec<(String, i64)> = daily_map
        .into_iter()
        .map(|(d, c)| (d.format("%Y-%m-%d").to_string(), c))
        .collect();
    result.sort();
    result
}

/// 查询指定日期每小时按键数（返回长度 24 的列表）。
pub fn get_hourly_stats(target_date: Option<chrono::NaiveDate>) -> Vec<i64> {
    let d = target_date.unwrap_or_else(|| Local::now().date_naive());
    let mut hourly = vec![0i64; 24];
    let path = paths::year_db_path(d.year());
    let conn = match connection::open_ro(&path) {
        Ok(c) => c,
        Err(_) => return hourly,
    };
    if !table_exists(&conn) {
        return hourly;
    }
    let (start, end) = date_range_ts(d);
    let mut stmt = conn
        .prepare(
            "SELECT (timestamp - ?1) / 3600 as hour, COUNT(*) as cnt FROM key_log WHERE timestamp >= ?1 AND timestamp < ?2 GROUP BY hour",
        )
        .unwrap();
    let rows = stmt
        .query_map(rusqlite::params![start, end], |r| {
            Ok((r.get::<_, i64>(0)?, r.get::<_, i64>(1)?))
        })
        .unwrap();
    for row in rows.flatten() {
        let hour = row.0;
        if (0..24).contains(&hour) {
            hourly[hour as usize] = row.1;
        }
    }
    hourly
}

/// 查询最近 N 天按星期统计（0=周一 ... 6=周日）。
pub fn get_weekday_stats(days: i64) -> HashMap<i64, i64> {
    let mut result: HashMap<i64, i64> = HashMap::new();
    let daily = get_daily_counts(days, None);
    for (date_str, count) in daily {
        if let Ok(d) = chrono::NaiveDate::parse_from_str(&date_str, "%Y-%m-%d") {
            let weekday = d.weekday().num_days_from_monday() as i64;
            *result.entry(weekday).or_insert(0) += count;
        }
    }
    result
}

/// 今日 Unix 秒起始。
pub fn today_start_ts() -> i64 {
    local_day_start_ts(Local::now().date_naive())
}

/// 当前 Unix 秒。
pub fn now_ts() -> i64 {
    chrono::Utc::now().timestamp()
}
