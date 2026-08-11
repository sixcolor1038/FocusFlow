//! 插件系统（Lua 脚本）。
//!
//! 镜像 Python 版 `plugins.py` 的插件协议，但用 Lua 脚本替代 Python：
//! - 插件是 `plugins/*.lua` 文件
//! - 声明元数据：PLUGIN_NAME / PLUGIN_DESC / PLUGIN_VERSION / PLUGIN_AUTHOR
//! - 可选函数：init() / cleanup()
//! - UI：get_view() 返回声明式 UI 描述表（宿主 Rust 渲染为 egui）
//!
//! 宿主向 Lua 环境注册 API 表 `focusflow`，插件通过它访问核心功能
//! （键鼠统计、番茄钟、定时任务、记账等）。

pub mod host;
pub mod manager;

pub use manager::{PluginInfo, PluginManager};

pub use self::Widget as WidgetT;

/// 插件 UI 描述：声明式视图，宿主渲染。
#[derive(Debug, Clone, Default)]
pub struct PluginView {
    /// 标题
    pub title: String,
    /// 控件列表
    pub widgets: Vec<Widget>,
}

/// 声明式控件。
#[derive(Debug, Clone)]
pub enum Widget {    /// 文本标签
    Label(String),
    /// 分组标题
    Heading(String),
    /// 只读文本行（键值对）
    KeyValue(String, String),
    /// 表格（表头 + 行）
    Table {
        headers: Vec<String>,
        rows: Vec<Vec<String>>,
    },
    /// 按钮
    Button {
        id: String,
        text: String,
    },
    /// 分隔线
    Separator,
    /// 滚动文本区
    TextArea(String),
}
