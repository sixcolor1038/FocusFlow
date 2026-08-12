//! 调试：execute 而非 prepare 跑 UNION。

use focusflow_core::db::connection;
use focusflow_core::paths;

fn main() {
    paths::set_app_dir(r"E:\mydata\DeepSeekdata\code\FocusFlow-rs\dist\FocusFlow");
    let conn = connection::open_ro(&paths::year_db_path(2099)).unwrap();
    conn.execute(
        "ATTACH DATABASE ?1 AS y2026",
        rusqlite::params![paths::year_db_path(2026).to_str().unwrap()],
    )
    .unwrap();

    // 用 execute 跑 UNION（非 prepared）
    let sql = "SELECT key_name, COUNT(*) as cnt FROM key_log UNION ALL SELECT key_name, COUNT(*) as cnt FROM y2026.key_log";
    let mut stmt = conn.prepare(sql).unwrap();
    let rows: Vec<(String, i64)> = stmt
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))
        .unwrap()
        .flatten()
        .collect();
    println!("UNION 直接跑 (prepare+query_map): {} 行", rows.len());
    println!("Top5: {:?}", &rows[..rows.len().min(5)]);

    // 用 conn.query 手动遍历
    let mut stmt2 = conn.prepare(sql).unwrap();
    let mut q = stmt2.query([]).unwrap();
    let mut first: Vec<(String, i64)> = Vec::new();
    while let Some(row) = q.next().unwrap() {
        let k: String = row.get(0).unwrap();
        let n: i64 = row.get(1).unwrap();
        first.push((k, n));
        if first.len() >= 3 {
            break;
        }
    }
    println!("query.next 前3: {first:?}");
}
