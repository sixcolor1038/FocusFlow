-- FocusFlow 插件：Edge 浏览器历史记录
-- 今日/总记录数 + 30 天趋势
-- 依赖宿主 API：focusflow.edge_*

PLUGIN_NAME = "Edge历史记录"
PLUGIN_DESC = "查看 Edge 浏览器历史记录数量及 30 天趋势"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "FocusFlow"

local cached_today = -1
local cached_total = -1

function init()
    focusflow.log("Edge历史记录插件已初始化")
end

function cleanup()
    focusflow.log("Edge历史记录插件已清理")
end

function on_action(id)
    if id == "refresh" then
        local today, total = focusflow.edge_update_today()
        cached_today = today
        cached_total = total
        focusflow.log("已更新 Edge 历史：今日 " .. tostring(today) .. "，总计 " .. tostring(total))
    end
end

function get_view()
    -- 延迟查询：首次打开不自动查 Edge 库（可能很大/被锁定），
    -- 用户点击"刷新数据"才执行，避免加载卡顿
    local today_display = "—"
    local total_display = "—"
    if cached_today >= 0 then
        today_display = tostring(cached_today)
        total_display = tostring(cached_total)
    end

    -- 30 天趋势（本地缓存库，快）
    local counts = focusflow.edge_counts(30)
    local max_count = 0
    local rows = {}
    for _, c in ipairs(counts) do
        if c["count"] > max_count then max_count = c["count"] end
        rows[#rows + 1] = { c["date"], tostring(c["count"]) }
    end

    return {
        title = "Edge 历史记录",
        widgets = {
            { type = "heading", text = "概览" },
            { type = "keyvalue", key = "今日记录数", value = today_display },
            { type = "keyvalue", key = "总记录数", value = total_display },
            { type = "keyvalue", key = "近30天峰值", value = tostring(max_count) },
            { type = "button", id = "refresh", text = "刷新数据" },
            { type = "separator" },
            { type = "heading", text = "近 30 天趋势" },
            {
                type = "table",
                headers = { "日期", "记录数" },
                rows = rows,
            },
            { type = "separator" },
            { type = "label", text = "点击「刷新数据」从 Edge 浏览器读取最新记录（可能较慢）" },
        },
    }
end
