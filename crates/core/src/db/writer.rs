//! 数据库写入器：单写线程 + 有界队列 + 批量事务。
//!
//! 镜像 Python 版 `database._DBWriter`：
//! - 后台线程持唯一写连接，避免并发写
//! - 有界队列（5000 条），满时丢弃最旧（防止内存无限增长）
//! - 批量写入（batch_size 或 flush_interval 触发），单事务原子提交
//! - 跨年自动建表
//! - flush 信号：立即落库 + 等待完成（退出/备份用）
//! - 批量失败自动重试 + 逐条兜底

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use crate::db::connection;
use crate::paths;

/// 队列容量上限（与 Python 版 max_queue=5000 一致）
const MAX_QUEUE: usize = 5000;

/// 写事件：键名 + 时间戳（Unix 秒）
pub type KeyEvent = (String, i64);

/// 对写线程的控制信号
enum Signal {
    /// 立即 flush；`done` 为 Some 时等待完成后通知
    Flush { done: Option<mpsc::Sender<()>> },
    /// 停止线程（退出前 flush 残留）
    Stop,
}

struct WriterState {
    /// 有界事件队列发送端
    tx: mpsc::SyncSender<KeyEvent>,
    /// 信号发送端
    sig_tx: mpsc::Sender<Signal>,
    /// 今日计数（内存缓存）
    today_count: AtomicU64,
    /// 已确认建表的年份
    db_year: Mutex<i32>,
    /// 线程是否存活
    alive: AtomicBool,
}

/// 写入器句柄（Send + Sync，可跨线程持有）。
pub struct DbWriter {
    state: Arc<WriterState>,
    batch_size: usize,
    flush_interval: Duration,
}

impl DbWriter {
    /// 创建并启动写入线程。
    pub fn start(batch_size: usize, flush_interval: Duration) -> Arc<Self> {
        let (tx, rx) = mpsc::sync_channel(MAX_QUEUE);
        let (sig_tx, sig_rx) = mpsc::channel();
        // 今日计数初始值 = DB 中已有的今日记录数（避免重启后今日活跃先降后升）
        let today_base = crate::db::queries::today_start_ts();
        let today_base_count = {
            let path = paths::current_year_db_path();
            connection::open_ro(&path)
                .ok()
                .and_then(|conn| {
                    conn.query_row(
                        "SELECT COUNT(*) FROM key_log WHERE timestamp >= ?1 AND timestamp < ?2",
                        rusqlite::params![today_base, today_base + 86_400],
                        |r| r.get::<_, i64>(0),
                    )
                    .ok()
                })
                .unwrap_or(0)
                .max(0) as u64
        };
        let state = Arc::new(WriterState {
            tx,
            sig_tx,
            today_count: AtomicU64::new(today_base_count),
            db_year: Mutex::new(paths::current_year()),
            alive: AtomicBool::new(true),
        });

        let writer = Arc::new(Self {
            state: Arc::clone(&state),
            batch_size: batch_size.max(1),
            flush_interval,
        });

        let state2 = Arc::clone(&state);
        thread::Builder::new()
            .name("db-writer".into())
            .spawn(move || writer_loop(state2, rx, sig_rx, batch_size, flush_interval))
            .expect("启动 DB 写入线程失败");

        tracing::info!(
            "DB 写入线程已启动 (batch={}, interval={:?})",
            writer.batch_size,
            writer.flush_interval
        );
        writer
    }

    /// 投递一次按键记录（完全非阻塞，永不阻塞监听热路径）。
    ///
    /// 队列满时丢弃当前事件（记录 debug 日志）。由于写线程每 200ms 排空
    /// 一次，正常负载下队列不会满；极端磁盘阻塞时才触发丢弃，避免无限增长。
    pub fn record(&self, key_name: &str, timestamp: i64) {
        let state = &*self.state;
        match state.tx.try_send((key_name.to_string(), timestamp)) {
            Ok(()) => {}
            Err(mpsc::TrySendError::Full(_)) => {
                tracing::debug!("写入队列已满，丢弃事件: {key_name}");
            }
            Err(mpsc::TrySendError::Disconnected(_)) => {
                tracing::debug!("写入线程未运行，丢弃事件");
            }
        }
        state.today_count.fetch_add(1, Ordering::Relaxed);
    }

    /// 今日计数（内存缓存值）。
    pub fn today_count(&self) -> u64 {
        self.state.today_count.load(Ordering::Relaxed)
    }

    /// 重置今日计数缓存（外部清除数据后调用）。
    pub fn reset_today_count(&self) {
        self.state.today_count.store(0, Ordering::Relaxed);
    }

    /// 立即 flush：发信号让写线程排空队列。`wait=true` 时阻塞等待完成。
    pub fn flush(&self, wait: bool) {
        if wait {
            let (tx, rx) = mpsc::channel();
            let _ = self.state.sig_tx.send(Signal::Flush { done: Some(tx) });
            let _ = rx.recv_timeout(Duration::from_secs(3));
        } else {
            let _ = self.state.sig_tx.send(Signal::Flush { done: None });
        }
    }

    /// 停止写线程（退出前 flush 残留）。
    pub fn stop(&self) {
        let _ = self.state.sig_tx.send(Signal::Stop);
        let deadline = Instant::now() + Duration::from_secs(3);
        while self.state.alive.load(Ordering::Relaxed) && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
    }

    /// 线程是否存活。
    pub fn is_alive(&self) -> bool {
        self.state.alive.load(Ordering::Relaxed)
    }
}

fn writer_loop(
    state: Arc<WriterState>,
    rx: mpsc::Receiver<KeyEvent>,
    sig_rx: mpsc::Receiver<Signal>,
    batch_size: usize,
    flush_interval: Duration,
) {
    let mut batch: Vec<KeyEvent> = Vec::with_capacity(batch_size * 2);
    let mut last_flush = Instant::now();

    loop {
        // 处理所有待处理的信号（一次循环内尽量处理完）
        let mut flush_requested = false;
        let mut pending_done: Vec<mpsc::Sender<()>> = Vec::new();
        loop {
            match sig_rx.try_recv() {
                Ok(Signal::Flush { done }) => {
                    flush_requested = true;
                    if let Some(d) = done {
                        pending_done.push(d);
                    }
                }
                Ok(Signal::Stop) => {
                    // 退出前 flush 残留
                    if !batch.is_empty() {
                        write_batch(&state, &batch);
                        batch.clear();
                    }
                    for d in pending_done {
                        let _ = d.send(());
                    }
                    state.alive.store(false, Ordering::Relaxed);
                    tracing::info!("DB 写入线程已停止");
                    return;
                }
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => break,
            }
        }

        // 排空事件队列到批量缓存
        let mut disconnected = false;
        loop {
            match rx.try_recv() {
                Ok(ev) => batch.push(ev),
                Err(mpsc::TryRecvError::Empty) => break,
                Err(mpsc::TryRecvError::Disconnected) => {
                    disconnected = true;
                    break;
                }
            }
        }

        // flush：立即写空
        if flush_requested {
            if !batch.is_empty() {
                write_batch(&state, &batch);
                batch.clear();
            }
            last_flush = Instant::now();
            for d in pending_done {
                let _ = d.send(());
            }
        }

        // 批量阈值或时间阈值触发
        if batch.len() >= batch_size
            || (!batch.is_empty() && last_flush.elapsed() >= flush_interval)
        {
            tracing::debug!("写批触发: batch={} elapsed={:?}", batch.len(), last_flush.elapsed());
            write_batch(&state, &batch);
            batch.clear();
            last_flush = Instant::now();
        }

        if disconnected {
            // 发送端已关闭且无 Stop 信号（异常路径），退出
            if !batch.is_empty() {
                write_batch(&state, &batch);
            }
            state.alive.store(false, Ordering::Relaxed);
            return;
        }

        if batch.is_empty() && !flush_requested {
            thread::sleep(Duration::from_millis(200));
        }
    }
}

/// 批量写入（单事务 + 重试 + 逐条兜底），镜像 `_DBWriter._write_batch`。
fn write_batch(state: &WriterState, batch: &[KeyEvent]) {
    if batch.is_empty() {
        return;
    }
    let max_retries = 3;
    let mut last_err: Option<anyhow::Error> = None;

    for attempt in 0..max_retries {
        let now_year = paths::current_year();
        {
            let mut db_year = state.db_year.lock().unwrap();
            if *db_year != now_year {
                let path = paths::year_db_path(now_year);
                if let Ok(conn) = connection::open_rw(&path) {
                    if connection::ensure_schema(&conn, now_year).is_ok() {
                        *db_year = now_year;
                    }
                }
            }
        }

        let path = paths::current_year_db_path();
        let result = (|| -> anyhow::Result<()> {
            let conn = connection::open_rw(&path)?;
            conn.execute("BEGIN IMMEDIATE;", [])?;
            let insert = || -> anyhow::Result<()> {
                let mut stmt = conn.prepare("INSERT INTO key_log (key_name, timestamp) VALUES (?1, ?2)")?;
                for (key, ts) in batch {
                    stmt.execute(rusqlite::params![key, ts])?;
                }
                Ok(())
            };            match insert() {
                Ok(()) => {
                    conn.execute("COMMIT;", [])?;
                    Ok(())
                }
                Err(e) => {
                    let _ = conn.execute("ROLLBACK;", []);
                    Err(e)
                }
            }
        })();

        match result {
            Ok(()) => {
                tracing::debug!("批量写入成功: {} 条", batch.len());
                return;
            }
            Err(e) => {
                last_err = Some(e);
                if attempt < max_retries - 1 {
                    tracing::warn!(
                        "批量写入失败 (第{}次, {}条), 重试: {}",
                        attempt + 1,
                        batch.len(),
                        last_err.as_ref().unwrap()
                    );
                    thread::sleep(Duration::from_millis(500 * (attempt as u64 + 1)));
                }
            }
        }
    }

    tracing::error!(
        "批量写入最终失败 ({}条, 已重试{}次): {}",
        batch.len(),
        max_retries,
        last_err.as_ref().map(|e| e.to_string()).unwrap_or_default()
    );
    write_one_by_one(batch);
}

/// 逐条写入兜底（单条失败不影响其他）。
fn write_one_by_one(batch: &[KeyEvent]) {
    let path = paths::current_year_db_path();
    let conn = match connection::open_rw(&path) {
        Ok(c) => c,
        Err(e) => {
            tracing::warn!("逐条写入连接失败: {e}");
            return;
        }
    };
    let mut ok = 0usize;
    for (key, ts) in batch {
        let r = conn.execute(
            "INSERT INTO key_log (key_name, timestamp) VALUES (?1, ?2)",
            rusqlite::params![key, ts],
        );
        if r.is_ok() {
            ok += 1;
        }
    }
    tracing::warn!("逐条写入完成: {}/{} 成功", ok, batch.len());
}
