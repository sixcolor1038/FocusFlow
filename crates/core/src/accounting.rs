//! 记账模块。
//!
//! 镜像 Python 版 `accounting.py`：
//! - 收入/支出记录 CRUD
//! - 分类/子分类
//! - 按日期/类型/分类筛选
//! - 持久化到 `data/focusflow_accounting.db`

use rusqlite::Connection;

use crate::paths;

/// 记账记录。
#[derive(Debug, Clone)]
pub struct Expense {
    pub id: i64,
    pub rtype: String,
    pub item_name: String,
    pub store: Option<String>,
    pub purchase_date: String,
    pub amount: f64,
    pub category: Option<String>,
    pub subcategory: Option<String>,
    pub delivery_date: Option<String>,
    pub record_time: String,
    pub note: Option<String>,
}

/// 分类。
#[derive(Debug, Clone)]
pub struct Category {
    pub id: i64,
    pub name: String,
    pub ctype: String,
    pub subs: Vec<String>,
}

fn db_path() -> std::path::PathBuf {
    paths::data_dir().join("focusflow_accounting.db")
}

fn open() -> rusqlite::Result<Connection> {
    std::fs::create_dir_all(paths::data_dir()).ok();
    let conn = Connection::open(db_path())?;
    conn.pragma_update(None, "journal_mode", "WAL").ok();
    conn.pragma_update(None, "synchronous", "NORMAL").ok();
    conn.pragma_update(None, "foreign_keys", "ON").ok();
    Ok(conn)
}

/// 初始化表结构（幂等）。
pub fn init_db() -> anyhow::Result<()> {
    let conn = open()?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS expenses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL DEFAULT '支出',
            item_name TEXT NOT NULL,
            store TEXT,
            purchase_date TEXT NOT NULL,
            amount REAL NOT NULL,
            category TEXT,
            subcategory TEXT,
            delivery_date TEXT,
            record_time TEXT NOT NULL,
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            type TEXT NOT NULL DEFAULT 'both',
            subs TEXT NOT NULL DEFAULT '[]'
        );",
    )?;
    Ok(())
}

fn row_to_expense(r: &rusqlite::Row) -> rusqlite::Result<Expense> {
    Ok(Expense {
        id: r.get(0)?,
        rtype: r.get(1)?,
        item_name: r.get(2)?,
        store: r.get(3)?,
        purchase_date: r.get(4)?,
        amount: r.get(5)?,
        category: r.get(6)?,
        subcategory: r.get(7)?,
        delivery_date: r.get(8)?,
        record_time: r.get(9)?,
        note: r.get(10)?,
    })
}

/// 添加记账记录，返回 id。
#[allow(clippy::too_many_arguments)]
pub fn add_expense(
    rtype: &str,
    item_name: &str,
    store: Option<&str>,
    purchase_date: &str,
    amount: f64,
    category: Option<&str>,
    subcategory: Option<&str>,
    note: Option<&str>,
) -> i64 {
    let conn = match open() {
        Ok(c) => c,
        Err(_) => return -1,
    };
    let record_time = chrono::Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let r = conn.execute(
        "INSERT INTO expenses
         (type, item_name, store, purchase_date, amount, category, subcategory, record_time, note)
         VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)",
        rusqlite::params![
            rtype, item_name, store, purchase_date, amount, category, subcategory,
            record_time, note
        ],
    );
    match r {
        Ok(_) => conn.last_insert_rowid(),
        Err(e) => {
            tracing::error!("添加记账失败: {e}");
            -1
        }
    }
}

/// 更新记账记录。
pub fn update_expense(id: i64, e: &Expense) -> bool {
    let conn = match open() {
        Ok(c) => c,
        Err(_) => return false,
    };
    conn.execute(
        "UPDATE expenses SET type=?1, item_name=?2, store=?3, purchase_date=?4,
         amount=?5, category=?6, subcategory=?7, note=?8 WHERE id=?9",
        rusqlite::params![
            e.rtype, e.item_name, e.store, e.purchase_date, e.amount,
            e.category, e.subcategory, e.note, id
        ],
    )
    .map(|n| n > 0)
    .unwrap_or(false)
}

/// 删除记账记录。
pub fn delete_expense(id: i64) -> bool {
    open()
        .and_then(|conn| conn.execute("DELETE FROM expenses WHERE id=?1", [id]))
        .map(|n| n > 0)
        .unwrap_or(false)
}

/// 获取全部记账记录（按 id 降序）。
pub fn get_all_expenses(limit: i64) -> Vec<Expense> {
    let conn = match open() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let mut stmt = match conn.prepare("SELECT * FROM expenses ORDER BY id DESC LIMIT ?1") {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let result = stmt.query_map([limit], row_to_expense);
    match result {
        Ok(rows) => rows.flatten().collect(),
        Err(_) => Vec::new(),
    }
}

/// 按日期筛选。
pub fn get_expenses_by_date(date: &str) -> Vec<Expense> {
    let conn = match open() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let mut stmt = match conn.prepare(
        "SELECT * FROM expenses WHERE purchase_date=?1 ORDER BY id DESC",
    ) {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let result = stmt.query_map([date], row_to_expense);
    match result {
        Ok(rows) => rows.flatten().collect(),
        Err(_) => Vec::new(),
    }
}

/// 添加分类。
pub fn add_category(name: &str, ctype: &str, subs: &[String]) -> i64 {
    let conn = match open() {
        Ok(c) => c,
        Err(_) => return -1,
    };
    let subs_json = subs.join(",");
    let r = conn.execute(
        "INSERT OR IGNORE INTO categories (name, type, subs) VALUES (?1, ?2, ?3)",
        rusqlite::params![name, ctype, subs_json],
    );
    match r {
        Ok(_) => {
            // 返回 id（存在则查询）
            conn.query_row(
                "SELECT id FROM categories WHERE name=?1",
                [name],
                |r| r.get(0),
            )
            .unwrap_or(-1)
        }
        Err(e) => {
            tracing::error!("添加分类失败: {e}");
            -1
        }
    }
}

/// 获取所有分类。
pub fn get_all_categories() -> Vec<Category> {
    let conn = match open() {
        Ok(c) => c,
        Err(_) => return Vec::new(),
    };
    let mut stmt = match conn.prepare("SELECT id, name, type, subs FROM categories ORDER BY id") {
        Ok(s) => s,
        Err(_) => return Vec::new(),
    };
    let result = stmt.query_map([], |r| {
        let subs_raw: String = r.get(3)?;
        let subs: Vec<String> = if subs_raw.is_empty() {
            Vec::new()
        } else {
            subs_raw.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect()
        };
        Ok(Category {
            id: r.get(0)?,
            name: r.get(1)?,
            ctype: r.get(2)?,
            subs,
        })
    });
    match result {
        Ok(rows) => rows.flatten().collect(),
        Err(_) => Vec::new(),
    }
}

/// 月度汇总：返回 (总支出, 总收入)。
pub fn monthly_summary(year_month: &str) -> (f64, f64) {
    let conn = match open() {
        Ok(c) => c,
        Err(_) => return (0.0, 0.0),
    };
    let prefix = format!("{year_month}%");
    let expense: f64 = conn
        .query_row(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE type='支出' AND purchase_date LIKE ?1",
            [&prefix],
            |r| r.get(0),
        )
        .unwrap_or(0.0);
    let income: f64 = conn
        .query_row(
            "SELECT COALESCE(SUM(amount), 0) FROM expenses WHERE type='收入' AND purchase_date LIKE ?1",
            [&prefix],
            |r| r.get(0),
        )
        .unwrap_or(0.0);
    (expense, income)
}
