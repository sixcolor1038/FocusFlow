//! 统计视图：主面板、键鼠排行、分组统计、趋势图、小时分布、星期分布。
//!
//! 镜像 Python 版 `gui.py` 的各个视图，使用 egui 绘制。

use std::collections::HashMap;

use eframe::egui;

use focusflow_core::db;
use focusflow_core::stats::cpm as cpm_calc;

/// 主题配色（对应 Python 版 THEMES，硬编码 light/dark 两套）。
pub struct Theme {
    pub bg: egui::Color32,
    pub card_bg: egui::Color32,
    pub fg: egui::Color32,
    pub accent: egui::Color32,
    pub accent_soft: egui::Color32,
    pub muted: egui::Color32,
    pub border: egui::Color32,
    #[allow(dead_code)]
    pub success: egui::Color32,
    #[allow(dead_code)]
    pub warning: egui::Color32,
    #[allow(dead_code)]
    pub danger: egui::Color32,
    #[allow(dead_code)]
    pub tree_alt: egui::Color32,
    #[allow(dead_code)]
    pub tree_bg: egui::Color32,
}

impl Theme {
    pub fn light() -> Self {
        Self {
            bg: c(0xF5, 0xF7, 0xFA),
            card_bg: c(0xFF, 0xFF, 0xFF),
            fg: c(0x1A, 0x1A, 0x2E),
            accent: c(0x4D, 0x8C, 0xF7),
            accent_soft: c(0xE8, 0xF0, 0xFE),
            muted: c(0x4A, 0x4A, 0x6A),
            border: c(0xE5, 0xEC, 0xF8),
            success: c(0x10, 0xB9, 0x81),
            warning: c(0xF5, 0x9E, 0x0B),
            danger: c(0xE5, 0x48, 0x4D),
            tree_alt: c(0xF3, 0xF7, 0xFD),
            tree_bg: c(0xFF, 0xFF, 0xFF),
        }
    }

    pub fn dark() -> Self {
        Self {
            bg: c(0x14, 0x16, 0x1F),
            card_bg: c(0x1E, 0x23, 0x31),
            fg: c(0xE8, 0xEA, 0xEE),
            accent: c(0x5B, 0x9C, 0xF7),
            accent_soft: c(0x22, 0x30, 0x4A),
            muted: c(0x8A, 0x92, 0xA6),
            border: c(0x2A, 0x30, 0x42),
            success: c(0x34, 0xD3, 0x99),
            warning: c(0xFB, 0xBF, 0x24),
            danger: c(0xF8, 0x71, 0x71),
            tree_alt: c(0x24, 0x2B, 0x3C),
            tree_bg: c(0x1E, 0x23, 0x31),
        }
    }

    /// 应用到 egui 视觉风格。
    pub fn apply(&self, ctx: &egui::Context) {
        let dark = self.bg.r() < 100;
        let mut visuals = if dark {
            egui::Visuals::dark()
        } else {
            egui::Visuals::light()
        };
        visuals.panel_fill = self.bg;
        visuals.window_fill = self.card_bg;
        visuals.override_text_color = Some(self.fg);
        visuals.widgets.inactive.bg_fill = self.accent_soft;
        visuals.widgets.hovered.bg_fill = self.accent_soft;
        visuals.widgets.active.bg_fill = self.accent_soft;
        visuals.selection.bg_fill = self.accent;
        ctx.set_visuals(visuals);
    }
}

fn c(r: u8, g: u8, b: u8) -> egui::Color32 {
    egui::Color32::from_rgb(r, g, b)
}

/// 千分位格式化。
pub fn fmt_thousands(n: i64) -> String {
    let s = n.abs().to_string();
    let mut out = String::new();
    let len = s.len();
    for (i, ch) in s.chars().enumerate() {
        if i > 0 && (len - i).is_multiple_of(3) {
            out.push(',');
        }
        out.push(ch);
    }
    if n < 0 {
        format!("-{out}")
    } else {
        out
    }
}

/// 按键分类（对应 Python 版 classify_key）。
pub fn classify_key(key_name: &str) -> &'static str {
    if key_name.starts_with("滚轮") {
        return "滚轮";
    }
    if key_name.starts_with("鼠标") {
        return "鼠标点击";
    }
    if matches!(
        key_name,
        "Shift" | "左Shift" | "右Shift" | "Ctrl" | "左Ctrl" | "右Ctrl"
            | "Alt" | "左Alt" | "右Alt" | "Win" | "左Win" | "右Win"
    ) {
        return "修饰键";
    }
    if key_name.starts_with('F') && key_name.len() > 1 && key_name[1..].chars().all(|c| c.is_ascii_digit()) {
        return "功能键";
    }
    if key_name.len() == 1 && key_name.chars().next().unwrap().is_ascii_digit() {
        return "数字键";
    }
    if key_name.len() == 1 && key_name.chars().next().unwrap().is_ascii_alphabetic() {
        return "字母键";
    }
    if matches!(
        key_name,
        "空格" | "回车" | "退格" | "Tab" | "Esc" | "Delete" | "Insert"
            | "Home" | "End" | "PageUp" | "PageDown" | "↑" | "↓" | "←" | "→"
    ) {
        return "编辑键";
    }
    "其他"
}

const KEY_GROUP_ORDER: [&str; 8] = [
    "字母键", "数字键", "功能键", "修饰键", "编辑键", "鼠标点击", "滚轮", "其他",
];

/// 周期选择。
#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Period {
    Today,
    Days(i64),
    Total,
}

impl Period {
    pub fn label(&self) -> String {
        match self {
            Period::Today => "今日".to_string(),
            Period::Days(d) => format!("{d}天"),
            Period::Total => "总计".to_string(),
        }
    }
}

/// 主面板：今日活跃 / CPM / 周期总数 / 日均 / 最高单日。
pub struct StatsPanel {
    pub today_count: i64,
    pub cpm: i64,
    pub period: Period,
    pub total: i64,
    pub avg: i64,
    pub max_day: i64,
}

impl Default for StatsPanel {
    fn default() -> Self {
        Self {
            today_count: 0,
            cpm: 0,
            period: Period::Today,
            total: 0,
            avg: 0,
            max_day: 0,
        }
    }
}

impl StatsPanel {
    pub fn show(&mut self, ui: &mut egui::Ui, theme: &Theme, config: &'static focusflow_core::config::FocusFlowConfig) {
        // 周期选择栏
        ui.horizontal(|ui| {
            for (label, period) in [
                ("今日", Period::Today),
                ("7天", Period::Days(7)),
                ("15天", Period::Days(15)),
                ("30天", Period::Days(30)),
                ("1年", Period::Days(365)),
                ("总计", Period::Total),
            ] {
                if ui.selectable_label(self.period == period, label).clicked() {
                    self.period = period;
                    self.refresh(config);
                }
            }
        });
        ui.add_space(8.0);
        ui.separator();
        ui.add_space(8.0);

        // 大数字卡片
        egui::Grid::new("hero_grid")
            .num_columns(5)
            .spacing([32.0, 8.0])
            .show(ui, |ui| {
                big_stat(ui, theme, "今日活跃", &fmt_thousands(self.today_count));
                big_stat(ui, theme, "当前速度", &format!("{} 次/分", fmt_thousands(self.cpm)));
                big_stat(ui, theme, &format!("周期总数({})", self.period.label()), &fmt_thousands(self.total));
                big_stat(ui, theme, "日均(7天)", &fmt_thousands(self.avg));
                big_stat(ui, theme, "最高单日", &fmt_thousands(self.max_day));
            });
    }

    /// 刷新数据（从 DB 查询 + CPM）。
    pub fn refresh(&mut self, config: &'static focusflow_core::config::FocusFlowConfig) {
        self.today_count = db::get_today_count(None);
        self.cpm = cpm_calc(config).get_cpm();
        let (total, _) = match self.period {
            Period::Today => db::get_stats_by_date(chrono::Local::now().date_naive()),
            Period::Days(d) => db::get_stats(Some(d), None),
            Period::Total => db::get_stats(None, None),
        };
        self.total = total;
        // 日均与最高单日（近7天）
        let daily = db::get_daily_counts(7, None);
        let counts: Vec<i64> = daily.iter().map(|(_, c)| *c).collect();
        self.avg = if counts.is_empty() {
            0
        } else {
            counts.iter().sum::<i64>() / counts.len() as i64
        };
        self.max_day = counts.iter().copied().max().unwrap_or(0);
    }
}

fn big_stat(ui: &mut egui::Ui, theme: &Theme, label: &str, value: &str) {
    egui::Frame::new()
        .fill(theme.card_bg)
        .corner_radius(8.0)
        .inner_margin(egui::Margin::symmetric(16, 10))
        .show(ui, |ui| {
            ui.vertical(|ui| {
                ui.label(egui::RichText::new(label).color(theme.muted).size(11.0));
                ui.label(egui::RichText::new(value).color(theme.accent).size(24.0).strong());
            });
        });
}

/// 键鼠排行表。
#[derive(Default)]
pub struct RankView {
    /// (键名, 次数)
    pub rows: Vec<(String, i64)>,
    pub total: i64,
    /// 右键清除的键
    #[allow(dead_code)]
    pub clear_key: Option<String>,
}


impl RankView {
    pub fn show(&mut self, ui: &mut egui::Ui, theme: &Theme) {
        // 使用 Table
        use egui_extras::{Column, TableBuilder};
        TableBuilder::new(ui)
            .striped(true)
            .resizable(true)
            .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
            .column(Column::exact(60.0))
            .column(Column::remainder())
            .column(Column::exact(90.0))
            .column(Column::exact(90.0))
            .header(20.0, |mut header| {
                header.col(|ui| { ui.strong("排名"); });
                header.col(|ui| { ui.strong("键鼠"); });
                header.col(|ui| { ui.strong("次数"); });
                header.col(|ui| { ui.strong("占比"); });
            })
            .body(|mut body| {
                for (i, (key, count)) in self.rows.iter().enumerate() {
                    body.row(22.0, |mut row| {
                        row.col(|ui| { ui.label((i + 1).to_string()); });
                        row.col(|ui| {
                            ui.label(key.as_str());
                        });
                        row.col(|ui| { ui.label(fmt_thousands(*count)); });
                        let percent = if self.total > 0 {
                            format!("{:.2}%", (*count as f64 / self.total as f64) * 100.0)
                        } else {
                            "0.00%".to_string()
                        };
                        row.col(|ui| { ui.label(percent); });
                    });
                }
                let _ = theme;
            });
    }
}

/// 分组统计视图。
#[derive(Default)]
pub struct GroupView {
    pub rows: Vec<(&'static str, i64)>,
    pub total: i64,
}


impl GroupView {
    pub fn update(&mut self, stats: &HashMap<String, i64>, total: i64) {
        self.total = total;
        let mut groups: HashMap<&'static str, i64> = HashMap::new();
        for (key, count) in stats {
            let g = classify_key(key);
            *groups.entry(g).or_insert(0) += count;
        }
        self.rows = KEY_GROUP_ORDER
            .iter()
            .filter_map(|g| groups.get(*g).map(|c| (*g, *c)))
            .collect();
    }

    pub fn show(&self, ui: &mut egui::Ui) {
        use egui_extras::{Column, TableBuilder};
        TableBuilder::new(ui)
            .striped(true)
            .cell_layout(egui::Layout::left_to_right(egui::Align::Center))
            .column(Column::remainder())
            .column(Column::exact(90.0))
            .column(Column::exact(90.0))
            .header(20.0, |mut header| {
                header.col(|ui| { ui.strong("分组"); });
                header.col(|ui| { ui.strong("次数"); });
                header.col(|ui| { ui.strong("占比"); });
            })
            .body(|mut body| {
                for (g, count) in &self.rows {
                    body.row(22.0, |mut row| {
                        row.col(|ui| { ui.label(*g); });
                        row.col(|ui| { ui.label(fmt_thousands(*count)); });
                        let percent = if self.total > 0 {
                            format!("{:.2}%", (*count as f64 / self.total as f64) * 100.0)
                        } else {
                            "0.00%".to_string()
                        };
                        row.col(|ui| { ui.label(percent); });
                    });
                }
            });
    }
}

/// 趋势图（近7/30天每日活跃）。
pub struct TrendView {
    /// 近 N 天数据
    pub days: i64,
    pub data: Vec<(String, i64)>,
}

impl Default for TrendView {
    fn default() -> Self {
        Self { days: 7, data: Vec::new() }
    }
}

impl TrendView {
    pub fn refresh(&mut self) {
        self.data = db::get_daily_counts(self.days, None);
    }

    pub fn show(&mut self, ui: &mut egui::Ui, theme: &Theme) {
        ui.horizontal(|ui| {
            for (label, days) in [("近7天", 7i64), ("近30天", 30i64)] {
                if ui.selectable_label(self.days == days, label).clicked() {
                    self.days = days;
                    self.refresh();
                }
            }
            if ui.button("刷新趋势").clicked() {
                self.refresh();
            }
        });
        ui.add_space(8.0);
        draw_line_chart(ui, theme, "每日活跃趋势", &self.data);
    }
}

/// 小时分布（今日每小时）。
pub struct HourlyView {
    pub data: Vec<i64>,
}

impl Default for HourlyView {
    fn default() -> Self {
        Self { data: vec![0; 24] }
    }
}

impl HourlyView {
    pub fn refresh(&mut self) {
        self.data = db::queries::get_hourly_stats(None);
    }

    pub fn show(&mut self, ui: &mut egui::Ui, theme: &Theme) {
        if ui.button("刷新").clicked() {
            self.refresh();
        }
        ui.add_space(8.0);
        draw_bar_chart(ui, theme, "今日每小时活跃", &self.data, 24);
    }
}

/// 星期分布。
#[derive(Default)]
pub struct WeekdayView {
    pub data: HashMap<i64, i64>,
}


impl WeekdayView {
    pub fn refresh(&mut self) {
        self.data = db::queries::get_weekday_stats(30);
    }

    pub fn show(&mut self, ui: &mut egui::Ui, theme: &Theme) {
        if ui.button("刷新").clicked() {
            self.refresh();
        }
        ui.add_space(8.0);
        let labels = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"];
        let values: Vec<i64> = (0..7).map(|i| self.data.get(&i).copied().unwrap_or(0)).collect();
        draw_bar_chart(ui, theme, "近30天星期活跃", &values, 7);
        let _ = labels;
    }
}

/// 手绘折线图。
fn draw_line_chart(ui: &mut egui::Ui, theme: &Theme, title: &str, data: &[(String, i64)]) {
    let (rect, _) = ui.allocate_exact_size(
        egui::vec2(ui.available_width(), 260.0),
        egui::Sense::hover(),
    );
    if rect.width() < 50.0 || rect.height() < 50.0 {
        return;
    }
    let painter = ui.painter_at(rect);
    let margin = egui::Margin::symmetric(40, 20);
    let plot_rect = rect.shrink2(egui::vec2(margin.left as f32, margin.top as f32));
    // 标题
    painter.text(
        rect.center_top() + egui::vec2(0.0, 8.0),
        egui::Align2::CENTER_TOP,
        title,
        egui::FontId::proportional(14.0),
        theme.fg,
    );
    if data.is_empty() {
        painter.text(rect.center(), egui::Align2::CENTER_CENTER, "暂无数据", egui::FontId::proportional(14.0), theme.muted);
        return;
    }
    let max_count = data.iter().map(|(_, c)| *c).max().unwrap_or(1).max(1);
    let n = data.len();
    let step_x = if n > 1 { plot_rect.width() / (n - 1) as f32 } else { plot_rect.width() };
    let points: Vec<egui::Pos2> = data
        .iter()
        .enumerate()
        .map(|(i, (_, c))| {
            let x = plot_rect.left() + step_x * i as f32;
            let y = plot_rect.bottom() - (plot_rect.height() * (*c as f32 / max_count as f32));
            egui::pos2(x, y)
        })
        .collect();
    // 网格线
    for i in 0..5 {
        let y = plot_rect.bottom() - plot_rect.height() * (i as f32 / 4.0);
        painter.hline(plot_rect.x_range(), y, egui::Stroke::new(1.0, theme.border));
        painter.text(egui::pos2(plot_rect.left() - 6.0, y), egui::Align2::RIGHT_CENTER, (max_count * i / 4).to_string(), egui::FontId::proportional(9.0), theme.muted);
    }
    // 填充 + 折线
    if points.len() >= 2 {
        let fill_points: Vec<egui::Pos2> = std::iter::once(egui::pos2(plot_rect.left(), plot_rect.bottom()))
            .chain(points.iter().copied())
            .chain(std::iter::once(egui::pos2(plot_rect.right(), plot_rect.bottom())))
            .collect();
        painter.add(egui::Shape::convex_polygon(fill_points, theme.accent.gamma_multiply(0.3), egui::Stroke::NONE));
        painter.add(egui::Shape::line(points.clone(), egui::Stroke::new(2.0, theme.accent)));
    }
    // 数据点
    for p in &points {
        painter.circle_filled(*p, 3.0, theme.accent);
    }
    // X 轴标签（每隔几个显示）
    let label_step = (n / 7).max(1);
    for (i, (date, _)) in data.iter().enumerate() {
        if i % label_step == 0 || i == n - 1 {
            let x = plot_rect.left() + step_x * i as f32;
            let short = if date.len() >= 10 { &date[5..] } else { date.as_str() };
            painter.text(egui::pos2(x, plot_rect.bottom() + 14.0), egui::Align2::CENTER_CENTER, short, egui::FontId::proportional(9.0), theme.muted);
        }
    }
}

/// 手绘柱状图。
fn draw_bar_chart(ui: &mut egui::Ui, theme: &Theme, title: &str, values: &[i64], count: usize) {
    let (rect, _) = ui.allocate_exact_size(
        egui::vec2(ui.available_width(), 260.0),
        egui::Sense::hover(),
    );
    if rect.width() < 50.0 || rect.height() < 50.0 {
        return;
    }
    let painter = ui.painter_at(rect);
    let margin = egui::Margin::symmetric(40, 20);
    let plot_rect = rect.shrink2(egui::vec2(margin.left as f32, margin.top as f32));
    painter.text(
        rect.center_top() + egui::vec2(0.0, 8.0),
        egui::Align2::CENTER_TOP,
        title,
        egui::FontId::proportional(14.0),
        theme.fg,
    );
    let max_val = values.iter().copied().max().unwrap_or(0).max(1);
    if max_val == 0 {
        painter.text(rect.center(), egui::Align2::CENTER_CENTER, "暂无数据", egui::FontId::proportional(14.0), theme.muted);
        return;
    }
    let bar_w = plot_rect.width() / count as f32 * 0.6;
    let gap = plot_rect.width() / count as f32 * 0.4;
    // 网格
    for i in 0..5 {
        let y = plot_rect.bottom() - plot_rect.height() * (i as f32 / 4.0);
        painter.hline(plot_rect.x_range(), y, egui::Stroke::new(1.0, theme.border));
        painter.text(egui::pos2(plot_rect.left() - 6.0, y), egui::Align2::RIGHT_CENTER, (max_val * i / 4).to_string(), egui::FontId::proportional(9.0), theme.muted);
    }
    for (i, &v) in values.iter().enumerate().take(count) {
        let x = plot_rect.left() + i as f32 * (bar_w + gap) + gap / 2.0;
        let bar_h = plot_rect.height() * (v as f32 / max_val as f32);
        let y = plot_rect.bottom() - bar_h;
        painter.rect_filled(
            egui::Rect::from_min_max(egui::pos2(x, y), egui::pos2(x + bar_w, plot_rect.bottom())),
            2.0,
            theme.accent.gamma_multiply(0.85),
        );
        if v > 0 {
            painter.text(
                egui::pos2(x + bar_w / 2.0, y - 8.0),
                egui::Align2::CENTER_CENTER,
                fmt_thousands(v),
                egui::FontId::proportional(8.0),
                theme.fg,
            );
        }
    }
    // 底部标签（每 3 个显示小时）
    if count == 24 {
        for hour in (0..24).step_by(3) {
            let x = plot_rect.left() + hour as f32 * (bar_w + gap) + gap / 2.0 + bar_w / 2.0;
            painter.text(egui::pos2(x, plot_rect.bottom() + 14.0), egui::Align2::CENTER_CENTER, format!("{hour}时"), egui::FontId::proportional(9.0), theme.muted);
        }
    }
}
