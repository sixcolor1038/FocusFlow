//! 构建脚本：把 focusflow.ico 嵌入 exe 资源（窗口/任务栏图标）。

fn main() {
    // 仅在 Windows 且存在 .ico 时嵌入
    #[cfg(target_os = "windows")]
    {
        let ico = "assets/focusflow.ico";
        if std::path::Path::new(ico).exists() {
            let mut res = winres::WindowsResource::new();
            res.set_icon(ico);
            // 产品/文件信息（与 Python 版一致）
            res.set("ProductName", "FocusFlow");
            res.set("FileDescription", "FocusFlow - 效率追踪器");
            res.set("LegalCopyright", "FocusFlow");
            res.set("ProductVersion", env!("CARGO_PKG_VERSION"));
            res.set("FileVersion", env!("CARGO_PKG_VERSION"));
            if let Err(e) = res.compile() {
                eprintln!("嵌入窗口图标失败: {e}");
            }
        }
    }
}
