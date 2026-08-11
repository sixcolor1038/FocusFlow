//! 数据库维护：年度归档、备份、VACUUM、清理、迁移。
//!
//! 镜像 Python 版 `database.py` 的维护函数：
//! - `_check_yearly_archive` / `_archive_year_data`：跨年数据归档
//! - `_migrate_combo_keys`：Ctrl+X 组合键历史拆分迁移
//! - 备份（SQLite 在线备份 API）+ 轮转
//! - VACUUM + PRAGMA optimize
//! - 清理旧数据

use std::collections::HashMap;
use std::path::Path;

use chrono::{Datelike, Local, NaiveDate, TimeZone};
use rusqlite::Connection;

use crate::db::connection;
use crate::db::queries;
use crate::paths;

/// 本地时区：日期当天 00:00:00 的 Unix 秒（镜像 Python `time.mktime`）。
fn local_midnight_ts(date: NaiveDate) -> i64 {
    chrono::Local
        .from_local_datetime(&date.and_hms_opt(0, 0, 0).expect("time"))
        .single()
        .expect("本地时区转换失败")
        .timestamp()
}

/// 检查是否需要年度归档（当前年份库中存在上一年数据时）。
pub fn check_yearly_archive(yearly_archive_enabled: bool) {
    if !yearly_archive_enabled {
        return;
    }
    let current_year = Local::now().year();
    let year_start_ts = local_midnight_ts(NaiveDate::from_ymd_opt(current_year, 1, 1).expect("date"));

    let path = paths::current_year_db_path();
    let conn = match connection::open_ro(&path) {
        Ok(c) => c,
        Err(_) => return,
    };
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM key_log WHERE timestamp < ?1",
            [year_start_ts],
            |r| r.get(0),
        )
        .unwrap_or(0);
    drop(conn);

    if count == 0 {
        return;
    }
    let prev_year = current_year - 1;
    tracing::info!("检测到 {count} 条 {prev_year} 年数据在当前库中，开始归档...");
    archive_year_data(prev_year, current_year);
}

/// 将 `source_year` 库中属于 `target_year` 的数据迁移到 `target_year` 库。
pub fn archive_year_data(target_year: i32, source_year: i32) {
    let year_start = local_midnight_ts(NaiveDate::from_ymd_opt(target_year, 1, 1).expect("date"));
    let year_end =
        local_midnight_ts(NaiveDate::from_ymd_opt(target_year + 1, 1, 1).expect("date"));

    // 1. 确保 target_year 库有表结构
    let target_path = paths::year_db_path(target_year);
    let source_path = paths::year_db_path(source_year);
    if let Ok(conn) = connection::open_rw(&target_path) {
        let _ = connection::ensure_schema(&conn, target_year);
    }

    // 2-4. ATTACH 迁移
    let result = (|| -> anyhow::Result<()> {
        let conn = connection::open_rw(&target_path)?;
        conn.execute(
            "ATTACH DATABASE ?1 AS source",
            rusqlite::params![source_path.to_str().unwrap()],
        )?;
        conn.execute("BEGIN;", [])?;
        let r1 = conn.execute(
            "INSERT INTO key_log (key_name, timestamp) SELECT key_name, timestamp FROM source.key_log WHERE timestamp >= ?1 AND timestamp < ?2",
            rusqlite::params![year_start, year_end],
        );
        let _ = r1;
        let r2 = conn.execute(
            "DELETE FROM source.key_log WHERE timestamp >= ?1 AND timestamp < ?2",
            rusqlite::params![year_start, year_end],
        );
        let _ = r2;
        conn.execute("COMMIT;", [])?;
        conn.execute("DETACH DATABASE source", [])?;
        Ok(())
    })();

    match result {
        Ok(()) => {
            tracing::info!("归档完成：{target_year} 年数据已迁移到 {}", target_path.display());
            vacuum_path(&source_path);
            queries::invalidate_years_cache();
        }
        Err(e) => {
            tracing::error!("年度归档失败: {e}");
        }
    }
}

/// 拆分旧版 "Ctrl+X" 组合键记录（幂等）。
/// - `Ctrl+字母` -> 字母本身
/// - `Ctrl+127` -> Delete
pub fn migrate_combo_keys() -> i64 {
    let mut migrated = 0i64;
    for year in queries::available_years() {
        let path = paths::year_db_path(year);
        let conn = match connection::open_rw(&path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let result = (|| -> anyhow::Result<i64> {
            let mut stmt = conn.prepare(
                "SELECT DISTINCT key_name FROM key_log WHERE key_name GLOB 'Ctrl+*'",
            )?;
            let names: Vec<String> = stmt
                .query_map([], |r| r.get(0))?
                .collect::<Result<_, _>>()?;
            let mut count = 0i64;
            for old in names {
                let new = combo_key_mapping(&old);
                if let Some(new) = new {
                    if new != old {
                        let n = conn.execute(
                            "UPDATE key_log SET key_name=?1 WHERE key_name=?2",
                            rusqlite::params![new, old],
                        )?;
                        count += n as i64;
                    }
                }
            }
            Ok(count)
        })();
        match result {
            Ok(n) => migrated += n,
            Err(e) => tracing::warn!("组合键数据迁移失败（{year} 年）: {e}"),
        }
    }
    if migrated > 0 {
        tracing::info!("组合键数据迁移完成：{migrated} 条 Ctrl+X 记录已按物理键拆分");
    }
    migrated
}

/// `Ctrl+X` -> 物理键名映射。
fn combo_key_mapping(old: &str) -> Option<String> {
    // Ctrl+A-Z / Ctrl+a-z -> 字母大写
    if let Some(rest) = old.strip_prefix("Ctrl+") {
        if rest.len() == 1 {
            let c = rest.chars().next()?;
            if c.is_ascii_alphabetic() {
                return Some(c.to_ascii_uppercase().to_string());
            }
            if c.is_ascii_digit() {
                return Some(c.to_string());
            }
        }
    }
    if old == "Ctrl+127" {
        return Some("Delete".to_string());
    }
    None
}

/// 清理 keep_days 天前的数据，返回删除条数。
pub fn cleanup_old_data(keep_days: i64) -> i64 {
    let cutoff = chrono::Utc::now().timestamp() - keep_days * 86_400;
    let mut total = 0i64;
    for year in queries::available_years() {
        let path = paths::year_db_path(year);
        let conn = match connection::open_rw(&path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM key_log WHERE timestamp < ?1", [cutoff], |r| r.get(0))
            .unwrap_or(0);
        if count > 0 {
            let n = conn.execute("DELETE FROM key_log WHERE timestamp < ?1", [cutoff]).unwrap_or(0);
            total += n as i64;
            tracing::info!("从 {year} 年库删除 {count} 条旧数据");
        }
    }
    if total > 0 {
        tracing::info!("共清理 {total} 条旧数据");
    }
    total
}

/// VACUUM 指定数据库。
pub fn vacuum_path(path: &Path) {
    let result = (|| -> anyhow::Result<()> {
        let conn = connection::open_rw(path)?;
        conn.pragma_update(None, "wal_checkpoint", "TRUNCATE")?;
        conn.execute("VACUUM;", [])?;
        conn.execute("PRAGMA optimize;", [])?;
        Ok(())
    })();
    match result {
        Ok(()) => tracing::info!("已压缩 {}", path.display()),
        Err(e) => tracing::error!("VACUUM {} 失败: {e}", path.display()),
    }
}

/// 压缩所有年度数据库。
pub fn vacuum_all() {
    for year in queries::available_years() {
        vacuum_path(&paths::year_db_path(year));
    }
}

/// 按配置自动 VACUUM（检查 meta 表中的 last_vacuum）。
pub fn maybe_auto_vacuum(auto_vacuum_days: i64) {
    if auto_vacuum_days <= 0 {
        return;
    }
    let path = paths::current_year_db_path();
    let conn = match connection::open_ro(&path) {
        Ok(c) => c,
        Err(_) => return,
    };
    let last: Option<String> = conn
        .query_row(
            "SELECT value FROM meta WHERE key = 'last_vacuum'",
            [],
            |r| r.get(0),
        )
        .ok();
    drop(conn);

    if let Some(last) = last {
        if let Ok(last_dt) = chrono::DateTime::parse_from_rfc3339(&last) {
            let last_local = last_dt.with_timezone(&Local);
            let diff = Local::now().signed_duration_since(last_local);
            if diff.num_days() < auto_vacuum_days {
                return;
            }
        }
    }

    vacuum_all();
    let now_str = chrono::DateTime::to_rfc3339(&chrono::Utc::now());
    if let Ok(conn) = connection::open_rw(&path) {
        let _ = conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('last_vacuum', ?1)",
            [now_str],
        );
    }
    tracing::info!("自动 VACUUM 完成");
}

/// 用 SQLite 在线备份 API 生成一致快照。
fn backup_db_file(src: &Path, dst: &Path) -> bool {
    let result = (|| -> anyhow::Result<()> {
        let src_conn = Connection::open(src)?;
        src_conn.backup(rusqlite::DatabaseName::Main, dst, None)?;
        Ok(())
    })();
    match result {
        Ok(()) => true,
        Err(e) => {
            tracing::error!("SQLite 在线备份失败 {} -> {}: {e}", src.display(), dst.display());
            false
        }
    }
}

/// 备份所有年度数据库到 backup/ 目录，返回首个备份路径。
pub fn backup_database(max_backups: i64) -> Option<std::path::PathBuf> {
    std::fs::create_dir_all(paths::backup_dir()).ok();
    let timestamp = Local::now().format("%Y%m%d_%H%M%S").to_string();
    let mut backed_up: Vec<std::path::PathBuf> = Vec::new();
    for year in queries::available_years() {
        let src = paths::year_db_path(year);
        if !src.exists() {
            continue;
        }
        let dst = paths::backup_dir().join(format!("focusflow_{year}_{timestamp}.db"));
        let mut ok = backup_db_file(&src, &dst);
        if !ok {
            // 兜底：checkpoint 后直接复制主文件
            if let Ok(conn) = connection::open_rw(&src) {
                let _ = conn.pragma_update(None, "wal_checkpoint", "TRUNCATE");
                drop(conn);
            }
            ok = std::fs::copy(&src, &dst).is_ok();
        }
        if dst.exists() {
            backed_up.push(dst);
        }
    }
    if !backed_up.is_empty() {
        rotate_backups(max_backups);
        tracing::info!("已备份 {} 个年度库到 {}", backed_up.len(), paths::backup_dir().display());
        backed_up.first().cloned()
    } else {
        None
    }
}

/// 保留最近 N 个备份（按年份分组）。
fn rotate_backups(max_keep: i64) {
    let mut groups: HashMap<String, Vec<std::path::PathBuf>> = HashMap::new();
    if let Ok(entries) = std::fs::read_dir(paths::backup_dir()) {
        for entry in entries.flatten() {
            let name = entry.file_name().to_string_lossy().to_string();
            // focusflow_2026_20260723_075125.db
            if let Some(stem) = name.strip_prefix("focusflow_").and_then(|s| s.strip_suffix(".db")) {
                let parts: Vec<&str> = stem.split('_').collect();
                if parts.len() >= 3 {
                    let year = parts[1].to_string();
                    groups.entry(year).or_default().push(entry.path());
                }
            }
        }
    }
    for files in groups.values_mut() {
        files.sort_by_key(|p| std::fs::metadata(p).and_then(|m| m.modified()).ok());
        files.reverse();
        for old in files.iter().skip(max_keep.max(0) as usize) {
            let _ = std::fs::remove_file(old);
        }
    }
}

/// 清空所有年度库中的 key_log 数据，返回删除条数。
pub fn reset_all_data() -> i64 {
    let mut total = 0i64;
    for year in queries::available_years() {
        let path = paths::year_db_path(year);
        let conn = match connection::open_rw(&path) {
            Ok(c) => c,
            Err(_) => continue,
        };
        let count: i64 = conn
            .query_row("SELECT COUNT(*) FROM key_log", [], |r| r.get(0))
            .unwrap_or(0);
        if count > 0 {
            let n = conn.execute("DELETE FROM key_log", []).unwrap_or(0);
            total += n as i64;
        }
    }
    queries::invalidate_years_cache();
    tracing::info!("已清空全部键鼠记录 {total} 条");
    total
}

/// 删除今日指定按键的所有记录（含队列中未入库数据由调用方处理），返回删除条数。
pub fn delete_key_today(key_name: &str) -> i64 {
    let key_name = key_name.trim();
    if key_name.is_empty() {
        return 0;
    }
    let start = queries::today_start_ts();
    let end = start + 86_400;
    let path = paths::current_year_db_path();
    let conn = match connection::open_rw(&path) {
        Ok(c) => c,
        Err(_) => return 0,
    };
    let n = conn
        .execute(
            "DELETE FROM key_log WHERE key_name=?1 AND timestamp >= ?2 AND timestamp < ?3",
            rusqlite::params![key_name, start, end],
        )
        .unwrap_or(0);
    tracing::info!("已删除今日按键 [{key_name}] 的记录 {n} 条");
    n as i64
}
