//! 实时统计：CPM（每分钟操作数）计算。
//!
//! 镜像 Python 版 `stats.py`：
//! - 滑动时间窗口 + deque 上限安全阀
//! - 写入/查询分离，查询时惰性清理过期数据
//! - 结果缓存（TTL 500ms）
//! - 线程安全

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};
use std::time::Instant;

use crate::config::FocusFlowConfig;

/// CPM 计算器。
pub struct CpmCalculator {
    /// 窗口（秒）
    window: f64,
    /// 时间戳队列（单调时钟 Instant）
    timestamps: Mutex<VecDeque<Instant>>,
    /// 缓存结果
    cached: Mutex<(i64, Instant)>,
}

impl CpmCalculator {
    pub fn new(window: f64) -> Self {
        Self {
            window: window.max(1.0),
            timestamps: Mutex::new(VecDeque::with_capacity(4096)),
            cached: Mutex::new((0, Instant::now() - std::time::Duration::from_secs(10))),
        }
    }

    /// 记录一次操作时间戳。
    pub fn record(&self) {
        let now = Instant::now();
        let mut timestamps = self.timestamps.lock().unwrap();
        timestamps.push_back(now);
        // 硬上限安全阀（对应 Python maxlen=100000）
        if timestamps.len() > 100_000 {
            timestamps.pop_front();
        }
        // 顺带清理窗口外旧数据
        let cutoff = now - std::time::Duration::from_secs_f64(self.window);
        while let Some(&front) = timestamps.front() {
            if front < cutoff {
                timestamps.pop_front();
            } else {
                break;
            }
        }
        // 写入时缓存失效
        let mut cached = self.cached.lock().unwrap();
        cached.0 = 0;
        cached.1 = Instant::now() - std::time::Duration::from_secs(10);
    }

    /// 获取当前 CPM（窗口内事件数）。
    pub fn get_cpm(&self) -> i64 {
        {
            let cached = self.cached.lock().unwrap();
            if cached.1.elapsed() < std::time::Duration::from_millis(500) {
                return cached.0;
            }
        }
        let now = Instant::now();
        let cutoff = now - std::time::Duration::from_secs_f64(self.window);
        let mut timestamps = self.timestamps.lock().unwrap();
        while let Some(&front) = timestamps.front() {
            if front < cutoff {
                timestamps.pop_front();
            } else {
                break;
            }
        }
        let count = timestamps.len() as i64;
        let mut cached = self.cached.lock().unwrap();
        cached.0 = count;
        cached.1 = now;
        count
    }

    /// 重置（清除当前时间戳）。
    pub fn reset(&self) {
        self.timestamps.lock().unwrap().clear();
        let mut cached = self.cached.lock().unwrap();
        cached.0 = 0;
        cached.1 = Instant::now() - std::time::Duration::from_secs(10);
    }
}

/// 全局 CPM 单例。
static CPM: std::sync::OnceLock<Arc<CpmCalculator>> = std::sync::OnceLock::new();

/// 获取全局 CPM 计算器。
pub fn cpm(config: &'static FocusFlowConfig) -> Arc<CpmCalculator> {
    Arc::clone(CPM.get_or_init(|| {
        let window = config.get_float("stats", "cpm_window", 60.0);
        Arc::new(CpmCalculator::new(window))
    }))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cpm_window() {
        let calc = CpmCalculator::new(60.0);
        for _ in 0..10 {
            calc.record();
        }
        assert_eq!(calc.get_cpm(), 10);
        calc.reset();
        assert_eq!(calc.get_cpm(), 0);
    }
}
