//! 导出报告（CSV / HTML），逻辑与 CLI 版一致。

use std::collections::HashMap;
use std::io::Write;

use chrono::Local;

fn fmt_thousands(n: i64) -> String {
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

/// 导出 CSV（带 BOM，Excel 打开中文不乱码）。
pub fn export_csv(path: &std::path::Path, total: i64, stats: &HashMap<String, i64>) -> bool {
    let mut sorted: Vec<(&String, &i64)> = stats.iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(a.1));
    let now_str = Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let mut out = String::new();
    out.push_str("# FocusFlow 键鼠活跃统计导出\n");
    out.push_str("# 统计周期 总计\n");
    out.push_str(&format!("# 总活跃次数 {total}\n"));
    out.push_str(&format!("# 导出时间 {now_str}\n\n"));
    out.push_str("排名,键鼠,次数,占比(%)\n");
    let mut rank = 0;
    for (key, count) in sorted {
        rank += 1;
        let percent = if total > 0 {
            format!("{:.2}", (*count as f64 / total as f64) * 100.0)
        } else {
            "0.00".to_string()
        };
        out.push_str(&format!("{rank},{key},{count},{percent}\n"));
    }
    std::fs::File::create(path)
        .and_then(|mut f| {
            f.write_all(b"\xef\xbb\xbf")?;
            f.write_all(out.as_bytes())
        })
        .is_ok()
}

/// 导出 HTML 统计报告。
pub fn export_html(path: &std::path::Path, total: i64, stats: &HashMap<String, i64>) -> bool {
    let mut sorted: Vec<(&String, &i64)> = stats.iter().collect();
    sorted.sort_by(|a, b| b.1.cmp(a.1));
    let now_str = Local::now().format("%Y-%m-%d %H:%M:%S").to_string();
    let mut rows = String::new();
    let mut rank = 0;
    for (key, count) in sorted {
        rank += 1;
        let percent = if total > 0 {
            format!("{:.2}%", (*count as f64 / total as f64) * 100.0)
        } else {
            "0.00%".to_string()
        };
        let bar_width = if total > 0 {
            (*count as f64 / total as f64) * 100.0
        } else {
            0.0
        };
        rows.push_str(&format!(
            r#"<tr><td class="rank">{rank}</td><td class="key">{key}</td><td class="count">{}</td><td class="percent"><div class="bar-container"><div class="bar" style="width:{bar_width:.1}%"></div><span>{percent}</span></div></td></tr>"#,
            fmt_thousands(*count)
        ));
    }
    let html = format!(
        r#"<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>FocusFlow 活跃统计报告</title>
<style>
    body {{ font-family: "Segoe UI", "Microsoft YaHei", sans-serif; margin: 40px; background: #f5f5f5; }}
    .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }}
    h1 {{ color: #0078d4; }}
    .meta {{ color: #666; margin-bottom: 20px; }}
    .total {{ font-size: 28px; font-weight: bold; color: #0078d4; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    th {{ background: #0078d4; color: white; padding: 12px; }}
    td {{ padding: 10px; border-bottom: 1px solid #eee; }}
    .bar-container {{ position: relative; min-width: 200px; }}
    .bar {{ background: #0078d4; height: 20px; border-radius: 3px; opacity: 0.3; }}
    .bar-container span {{ position: absolute; left: 8px; top: 2px; }}
</style>
</head>
<body>
<div class="container">
    <h1>FocusFlow 活跃统计报告</h1>
    <div class="meta"><div>统计周期：总计</div><div>导出时间：{now_str}</div></div>
    <div class="total">总活跃次数：{}</div>
    <table>
        <thead><tr><th>排名</th><th>键鼠</th><th>次数</th><th>占比</th></tr></thead>
        <tbody>{rows}</tbody>
    </table>
</div>
</body>
</html>"#,
        fmt_thousands(total)
    );
    std::fs::write(path, html).is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    fn sample_stats() -> HashMap<String, i64> {
        let mut m = HashMap::new();
        m.insert("A".to_string(), 40);
        m.insert("B".to_string(), 10);
        m.insert("鼠标左键".to_string(), 30);
        m
    }

    #[test]
    fn export_csv_has_bom_header_and_rows() {
        let dir = std::env::temp_dir().join("ff_export_test");
        std::fs::create_dir_all(&dir).ok();
        let path = dir.join("out.csv");
        let _ = std::fs::remove_file(&path);

        let ok = export_csv(&path, 80, &sample_stats());
        assert!(ok, "export_csv should succeed");
        let data = std::fs::read(&path).unwrap();
        // 带 UTF-8 BOM
        assert_eq!(&data[0..3], b"\xef\xbb\xbf", "CSV should have BOM");
        let text = String::from_utf8_lossy(&data[3..]);
        assert!(text.contains("总活跃次数 80"), "should contain total: {text}");
        assert!(text.contains("排名,键鼠,次数,占比(%)"), "should contain header");
        // 排行按次数降序：A(40) 应排第一
        let a_pos = text.find("1,A,40").expect("rank1 A 40");
        assert!(a_pos > 0);
        assert!(text.contains("鼠标左键"), "should contain中文键名");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn export_html_contains_stats() {
        let dir = std::env::temp_dir().join("ff_export_test2");
        std::fs::create_dir_all(&dir).ok();
        let path = dir.join("out.html");
        let _ = std::fs::remove_file(&path);

        let ok = export_html(&path, 80, &sample_stats());
        assert!(ok, "export_html should succeed");
        let html = std::fs::read_to_string(&path).unwrap();
        assert!(html.contains("总活跃次数：80"), "should contain total");
        assert!(html.contains("<html"), "should be html");
        assert!(html.contains("50.00%"), "A 占比 40/80 = 50.00%");
        assert!(html.contains("12.50%"), "B 占比 10/80 = 12.50%");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn thousands_formatter() {
        assert_eq!(fmt_thousands(0), "0");
        assert_eq!(fmt_thousands(1000), "1,000");
        assert_eq!(fmt_thousands(1234567), "1,234,567");
        assert_eq!(fmt_thousands(-500), "-500");
    }
}

