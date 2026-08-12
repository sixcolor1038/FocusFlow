-- FocusFlow 插件：记账本
-- 收入/支出记录 CRUD
-- 依赖宿主 API：focusflow.accounting_*

PLUGIN_NAME = "记账本"
PLUGIN_DESC = "收入/支出记录与分类管理"
PLUGIN_VERSION = "1.0.0"
PLUGIN_AUTHOR = "FocusFlow"

local draft_type = "支出"
local draft_item = ""
local draft_amount = ""
local draft_category = ""
local draft_date = ""

function init()
    focusflow.log("记账本插件已初始化")
end

function cleanup()
    focusflow.log("记账本插件已清理")
end

function _today()
    return os.date("%Y-%m-%d")
end

function on_action(id)
    if id == "set_type_expense" then
        draft_type = "支出"
    elseif id == "set_type_income" then
        draft_type = "收入"
    elseif id == "add_record" then
        local amount = tonumber(draft_amount) or 0
        if draft_item == "" or amount <= 0 then
            focusflow.log("请填写物品名称和有效金额")
            return
        end
        local new_id = focusflow.accounting_add(
            draft_type, draft_item, "", draft_date, amount, draft_category, ""
        )
        if new_id > 0 then
            focusflow.log("已添加记录 #" .. tostring(new_id))
            draft_item = ""
            draft_amount = ""
        end
    elseif id:match("^del_") then
        local rid = tonumber(id:sub(5))
        focusflow.accounting_delete(rid)
    elseif id == "set_item" then
        -- 文本输入由宿主特殊处理（见宿主交互说明）
    end
end

-- 文本输入更新（宿主调用）
function set_field(field, value)
    if field == "item" then draft_item = value
    elseif field == "amount" then draft_amount = value
    elseif field == "category" then draft_category = value
    elseif field == "date" then draft_date = value
    end
end

function get_view()
    local ym = os.date("%Y-%m")
    local expense, income = focusflow.accounting_summary(ym)
    local records = focusflow.accounting_list(50)

    local rows = {}
    for _, r in ipairs(records) do
        rows[#rows + 1] = {
            r["date"],
            r["type"],
            r["item"],
            string.format("%.2f", r["amount"]),
            r["category"],
            "del_" .. tostring(r["id"]),
        }
    end

    return {
        title = "记账本",
        widgets = {
            { type = "heading", text = "本月汇总" },
            { type = "keyvalue", key = "支出", value = string.format("%.2f", expense) },
            { type = "keyvalue", key = "收入", value = string.format("%.2f", income) },
            { type = "keyvalue", key = "结余", value = string.format("%.2f", income - expense) },
            { type = "separator" },
            { type = "heading", text = "新增记录" },
            { type = "button", id = "set_type_expense", text = "支出" },
            { type = "button", id = "set_type_income", text = "收入" },
            { type = "keyvalue", key = "当前类型", value = draft_type },
            { type = "textinput", field = "item", label = "物品名称" },
            { type = "textinput", field = "amount", label = "金额" },
            { type = "textinput", field = "category", label = "分类" },
            { type = "textinput", field = "date", label = "日期(YYYY-MM-DD)" },
            { type = "button", id = "add_record", text = "添加记录" },
            { type = "separator" },
            { type = "heading", text = "最近记录" },
            {
                type = "table",
                headers = { "日期", "类型", "物品", "金额", "分类", "删除" },
                rows = rows,
            },
        },
    }
end
