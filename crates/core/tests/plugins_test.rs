//! 插件系统集成测试：加载/卸载/热重载/宿主 API。

#[cfg(test)]
mod tests {
    use focusflow_core::config::FocusFlowConfig;
    use focusflow_core::db;
    use focusflow_core::paths;
    use focusflow_core::plugins::manager::PluginManager;

    /// 串行锁（app_dir 全局状态），容忍 poison（测试失败后不阻塞其他）
    fn test_lock() -> &'static std::sync::Mutex<()> {
        static LOCK: std::sync::OnceLock<std::sync::Mutex<()>> = std::sync::OnceLock::new();
        LOCK.get_or_init(|| std::sync::Mutex::new(()))
    }

    fn guard() -> std::sync::MutexGuard<'static, ()> {
        test_lock().lock().unwrap_or_else(|e| e.into_inner())
    }

    #[test]
    fn load_unload_plugin() {
        let _guard = guard();
        // 用 core crate 所在目录作为 app_dir（plugins/ 在其中）
        let dir = std::env::current_dir().unwrap();
        paths::set_app_dir(&dir);
        db::queries::invalidate_years_cache();

        let config: &'static FocusFlowConfig = Box::leak(Box::new(FocusFlowConfig::load(dir.join("config.ini")).unwrap()));
        let database = db::Database::init_readonly();
        let mut manager = PluginManager::new(config, database);

        // 发现插件
        let files = manager.discover();
        assert!(
            files.iter().any(|f| f.ends_with("stats_overview.lua")),
            "应发现 stats_overview.lua, got {files:?}"
        );

        // 加载
        let file = files.iter().find(|f| f.ends_with("stats_overview.lua")).unwrap();
        let name = manager.load_plugin(file).expect("加载插件失败");
        assert_eq!(name, "统计速览");

        // 检查元数据
        let info = manager.get_plugin(&name).expect("插件应存在");
        assert_eq!(info.version, "1.0.0");
        assert!(info.view.is_some(), "应有视图");

        // 视图内容
        let view = info.view.as_ref().unwrap();
        assert_eq!(view.title, "今日统计速览");
        assert!(!view.widgets.is_empty());
        // 应包含 table 控件
        let has_table = view
            .widgets
            .iter()
            .any(|w| matches!(w, focusflow_core::plugins::Widget::Table { .. }));
        assert!(has_table, "应包含表格控件");

        // 卸载
        assert!(manager.unload_plugin(&name));
        assert!(manager.get_plugin(&name).is_none());
    }

    #[test]
    fn host_api_stats() {
        let _guard = guard();
        let dir = std::env::current_dir().unwrap();
        paths::set_app_dir(&dir);
        db::queries::invalidate_years_cache();

        // 用 Lua 直接调宿主 API 验证
        let lua = mlua::Lua::new();
        let config: &'static FocusFlowConfig = Box::leak(Box::new(FocusFlowConfig::load(dir.join("config.ini")).unwrap()));
        let database = db::Database::init_readonly();
        focusflow_core::plugins::host::register_host_api(&lua, config, database).unwrap();

        // today_count 应返回数字
        let today: i64 = lua
            .load("return focusflow.today_count()")
            .eval()
            .expect("today_count 调用失败");
        assert!(today >= 0);

        // stats(0) 应返回 total + keys
        let result: mlua::MultiValue = lua
            .load("return focusflow.stats(0)")
            .eval()
            .expect("stats 调用失败");
        assert_eq!(result.len(), 2);

        // app_info 应返回版本
        let info: String = lua.load("return focusflow.app_info()").eval().unwrap();
        assert!(info.contains("FocusFlow"));
    }

    #[test]
    fn hot_reload_request() {
        focusflow_core::logger::init_logging();
        let _guard = guard();
        let dir = std::env::current_dir().unwrap();
        paths::set_app_dir(&dir);
        db::queries::invalidate_years_cache();

        let config: &'static FocusFlowConfig = Box::leak(Box::new(FocusFlowConfig::load(dir.join("config.ini")).unwrap()));
        let database = db::Database::init_readonly();
        let mut manager = PluginManager::new(config, database);

        // 加载
        let file = manager.discover().into_iter().find(|f| f.ends_with("stats_overview.lua")).unwrap();
        manager.load_plugin(&file).unwrap();

        // 启用热重载（检测线程）
        manager.enable_hot_reload();

        // 等待检测线程完成首次基线扫描（2s 周期）
        std::thread::sleep(std::time::Duration::from_millis(2500));

        // 修改文件触发重载请求（追加注释改变 mtime）
        let path = manager.get_plugin("统计速览").unwrap().file_path.clone();
        let content = std::fs::read_to_string(&path).unwrap();
        std::fs::write(&path, format!("{content}\n-- hot reload test\n")).unwrap();

        // 等待检测线程扫描并 poll（最多 8 秒）
        let mut reloaded = Vec::new();
        for _ in 0..8 {
            std::thread::sleep(std::time::Duration::from_millis(1000));
            reloaded = manager.poll_reload_requests();
            if !reloaded.is_empty() {
                break;
            }
        }

        // 恢复内容（避免污染）
        std::fs::write(&path, content).unwrap();

        assert!(
            reloaded.contains(&"stats_overview".to_string())
                || reloaded.contains(&"统计速览".to_string()),
            "应收到重载请求, got {reloaded:?}"
        );
        assert!(manager.get_plugin("统计速览").is_some(), "重载后插件应存在");

        manager.disable_hot_reload();
    }
}
