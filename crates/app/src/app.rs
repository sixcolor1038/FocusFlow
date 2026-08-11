//! 主应用逻辑占位（后续放托盘/热键/生命周期编排）。

/// 应用共享状态（P0/P1 阶段仅持配置引用，后续扩展数据库/统计/运行时等）。
pub struct AppState {
    /// 全局配置实例引用
    pub config: &'static focusflow_core::config::FocusFlowConfig,
}

impl AppState {
    pub fn new() -> Self {
        Self {
            config: focusflow_core::config::instance(),
        }
    }
}
