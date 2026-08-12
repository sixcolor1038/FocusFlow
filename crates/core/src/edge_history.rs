//! Edge 浏览器历史记录模块。
//!
//! 镜像 Python 版 `edge_history.py`：
//! - 直读 Edge History SQLite（%LOCALAPPDATA%\Microsoft\Edge\User Data\Default\History）
//! - Chrome 时间戳（1601-01-01 起微秒）转换
//! - 只读连接优先，被锁时复制 WAL 兜底
//! - 本地存储每日计数（focusflow_edge_history.db）供趋势图

use std::path::PathBuf;

use chrono::{Local, NaiveDate, TimeZone, Utc};
use rusqlite::Connection;

use crate::paths;

/// Edge History 数据库路径。
pub fn edge_history_path() -> PathBuf {
    if let Ok(local_app) = std::env::var("LOCALAPPDATA") {
        PathBuf::from(local_app)
            .join(r"Microsoft\Edge\User Data\Default\History")
    } else {
        PathBuf::from(r"C:\Users\Default\AppData\Local\Microsoft\Edge\User Data\Default\History")
    }
}

/// Chrome 时间戳（1601-01-01 起微秒）转 datetime。
#[allow(dead_code)]
fn chrome_to_datetime(chrome_time: i64) -> chrono::DateTime<Utc> {
    let epoch = Utc.with_ymd_and_hms(1601, 1, 1, 0, 0, 0).unwrap();
    epoch + chrono::Duration::microseconds(chrome_time)
}

/// datetime 转 Chrome 时间戳。
fn datetime_to_chrome(dt: &chrono::DateTime<Local>) -> i64 {
    let epoch = Utc.with_ymd_and_hms(1601, 1, 1, 0, 0, 0).unwrap();
    (dt.with_timezone(&Utc) - epoch).num_microseconds().unwrap_or(0)
}

/// 打开 Edge History（只读优先，锁定则复制）。
fn open_edge_history() -> (Option<Connection>, Option<PathBuf>) {
    let path = edge_history_path();
    if !path.exists() {
        return (None, None);
    }
    // 1) 只读直连
    match Connection::open_with_flags(
        &path,
        rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
    ) {
        Ok(conn) => return (Some(conn), None),
        Err(_) => {}
    }
    // 2) 复制主库 + WAL + SHM
    let temp = paths::data_dir().join("_edge_history_temp.db");
    if std::fs::copy(&path, &temp).is_ok() {
        for suffix in ["-wal", "-shm"] {
            let src = format!("{}{}", path.display(), suffix);
            if std::path::Path::new(&src).exists() {
                let _ = std::fs::copy(&src, format!("{}{}", temp.display(), suffix));
            }
        }
        if let Ok(conn) = Connection::open(&temp) {
            return (Some(conn), Some(temp));
        }
    }
    (None, None)
}

fn close_edge_connection(conn: Option<Connection>, temp: Option<PathBuf>) {
    drop(conn);
    if let Some(temp) = temp {
        for suffix in ["", "-wal", "-shm"] {
            let _ = std::fs::remove_file(format!("{}{}", temp.display(), suffix));
        }
    }
}

/// 查询 Edge 总历史记录数。
pub fn query_edge_total_count() -> i64 {
    let (conn, temp) = open_edge_history();
    let Some(conn) = conn else { return 0 };
    let r = conn
        .query_row("SELECT COUNT(*) FROM urls", [], |r| r.get::<_, i64>(0))
        .unwrap_or(0);
    close_edge_connection(Some(conn), temp);
    r
}

/// 查询指定日期的 Edge 历史记录数。
pub fn query_edge_history_count(target_date: NaiveDate) -> i64 {
    let (conn, temp) = open_edge_history();
    let Some(conn) = conn else { return 0 };
    let day_start = Local
        .from_local_datetime(&target_date.and_hms_opt(0, 0, 0).unwrap())
        .single()
        .unwrap();
    let day_end = day_start + chrono::Duration::days(1);
    let chrome_start = datetime_to_chrome(&day_start);
    let chrome_end = datetime_to_chrome(&day_end);
    let r = conn
        .query_row(
            "SELECT COUNT(*) FROM urls WHERE last_visit_time >= ?1 AND last_visit_time < ?2",
            rusqlite::params![chrome_start, chrome_end],
            |r| r.get::<_, i64>(0),
        )
        .unwrap_or(0);
    close_edge_connection(Some(conn), temp);
    r
}

fn edge_db_path() -> PathBuf {
    paths::data_dir().join("focusflow_edge_history.db")
}

/// 保存指定日期的计数到本地。
pub fn save_edge_history_count(target_date: NaiveDate, count: i64) {
    let conn = match Connection::open(edge_db_path()) {
        Ok(c) => c,
        Err(_) => return,
    };
    let _ = conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS edge_history (
            date TEXT PRIMARY KEY,
            count INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        );",
    );
    let _ = conn.execute(
        "INSERT OR REPLACE INTO edge_history (date, count, updated_at) VALUES (?1, ?2, ?3)",
        rusqlite::params![
            target_date.format("%Y-%m-%d").to_string(),
            count,
            Utc::now().timestamp()
        ],
    );
}

/// 获取近 N 天 Edge 历史计数。
pub fn get_edge_history_counts(days: i64) -> Vec<(String, i64)> {
    let path = edge_db_path();
    if !path.exists() {
        return Vec::new();
    }
    let conn = match Connection::open(&path) {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let start = (Local::now().date_naive() - chrono::Days::new((days - 1).max(0) as u64))
        .format("%Y-%m-%d")
        .to_string();
    let result = conn
        .prepare("SELECT date, count FROM edge_history WHERE date >= ?1 ORDER BY date")
        .and_then(|mut stmt| {
            stmt.query_map([&start], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                .map(|it| it.flatten().collect())
        });
    result.unwrap_or_default()
}

/// 更新今天并返回 (今日数, 总数)。
pub fn update_today_edge_history() -> (i64, i64) {
    let today = Local::now().date_naive();
    let today_count = query_edge_history_count(today);
    save_edge_history_count(today, today_count);
    let total = query_edge_total_count();
    (today_count, total)
}

/// 本地趋势（近 N 天），供插件展示。
pub fn trend_counts(days: i64) -> Vec<(String, i64)> {
    get_edge_history_counts(days)
}
