//! 主应用逻辑占位（后续放托盘/热键/生命周期编排）。

use std::sync::Arc;

/// 应用共享状态（P0 阶段仅持配置引用，后续扩展数据库/统计等）。
pub struct AppState {
    /// 全局配置实例引用
    pub config: &'static focusflow_core::config::FocusFlowConfig,
    /// tokio runtime 句柄（后续供后台任务使用）
    pub rt: Arc<tokio::runtime::Runtime>,
}

impl AppState {
    pub fn new() -> anyhow::Result<Self> {
        let rt = Arc::new(
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()?,
        );
        Ok(Self {
            config: focusflow_core::config::instance(),
            rt,
        })
    }
}
