// 手绘 SVG 折线图 / 柱状图（无外部依赖）

const NS = "http://www.w3.org/2000/svg";

function el(tag, attrs, text) {
  const node = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
  if (text != null) node.textContent = text;
  return node;
}

function fmtThousands(n) {
  return Number(n).toLocaleString("zh-CN");
}

// 折线图：data = [{date, value}]，width/height 为 viewBox 逻辑尺寸
function lineChart(container, title, data) {
  container.innerHTML = "";
  const W = 900, H = 260;
  const padL = 50, padR = 16, padT = 30, padB = 30;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: "100%" });
  const color = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
  const grid = getComputedStyle(document.documentElement).getPropertyValue("--grid-line").trim();
  const fg = getComputedStyle(document.documentElement).getPropertyValue("--fg").trim();

  svg.appendChild(el("text", { x: W / 2, y: 16, "text-anchor": "middle", "font-size": 15, fill: fg }, title));

  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(1, ...data.map((d) => d.value));
  const n = data.length;

  for (let i = 0; i <= 4; i++) {
    const y = padT + plotH - (plotH * i) / 4;
    svg.appendChild(el("line", { x1: padL, y1: y, x2: W - padR, y2: y, stroke: grid, "stroke-width": 1 }));
    svg.appendChild(el("text", { x: padL - 6, y: y + 4, "text-anchor": "end", "font-size": 10, fill: muted }, String(Math.round((max * i) / 4))));
  }

  if (n < 2) {
    svg.appendChild(el("text", { x: W / 2, y: H / 2, "text-anchor": "middle", "font-size": 14, fill: muted }, "暂无数据"));
    container.appendChild(svg);
    return;
  }

  const stepX = plotW / (n - 1);
  const pts = data.map((d, i) => {
    const x = padL + stepX * i;
    const y = padT + plotH - (plotH * d.value) / max;
    return { x, y };
  });

  const fillPts = `${padL},${padT + plotH} ${pts.map((p) => `${p.x},${p.y}`).join(" ")} ${padL + plotW},${padT + plotH}`;
  svg.appendChild(el("polygon", { points: fillPts, fill: color, "fill-opacity": 0.25 }));
  svg.appendChild(el("polyline", { points: pts.map((p) => `${p.x},${p.y}`).join(" "), fill: "none", stroke: color, "stroke-width": 2.5, "stroke-linejoin": "round" }));
  for (const p of pts) svg.appendChild(el("circle", { cx: p.x, cy: p.y, r: 3.5, fill: color }));

  const labelStep = Math.max(1, Math.floor(n / 7));
  data.forEach((d, i) => {
    if (i % labelStep === 0 || i === n - 1) {
      const x = padL + stepX * i;
      const short = d.date.length >= 10 ? d.date.slice(5) : d.date;
      svg.appendChild(el("text", { x, y: padT + plotH + 16, "text-anchor": "middle", "font-size": 10, fill: muted }, short));
    }
  });

  container.appendChild(svg);
}

// 柱状图：values = [v0, v1, ...]，labels 可选
function barChart(container, title, values, labels) {
  container.innerHTML = "";
  const W = 900, H = 260;
  const padL = 50, padR = 16, padT = 30, padB = 30;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, width: "100%", height: "100%" });
  const color = getComputedStyle(document.documentElement).getPropertyValue("--accent").trim();
  const muted = getComputedStyle(document.documentElement).getPropertyValue("--muted").trim();
  const grid = getComputedStyle(document.documentElement).getPropertyValue("--grid-line").trim();
  const fg = getComputedStyle(document.documentElement).getPropertyValue("--fg").trim();

  svg.appendChild(el("text", { x: W / 2, y: 16, "text-anchor": "middle", "font-size": 15, fill: fg }, title));

  const plotW = W - padL - padR, plotH = H - padT - padB;
  const max = Math.max(1, ...values);
  const count = values.length;
  const slot = plotW / count;
  const barW = slot * 0.6;
  const gap = slot * 0.4;

  for (let i = 0; i <= 4; i++) {
    const y = padT + plotH - (plotH * i) / 4;
    svg.appendChild(el("line", { x1: padL, y1: y, x2: W - padR, y2: y, stroke: grid, "stroke-width": 1 }));
    svg.appendChild(el("text", { x: padL - 6, y: y + 4, "text-anchor": "end", "font-size": 10, fill: muted }, String(Math.round((max * i) / 4))));
  }

  values.forEach((v, i) => {
    const x = padL + i * slot + gap / 2;
    const barH = (plotH * v) / max;
    const y = padT + plotH - barH;
    svg.appendChild(el("rect", { x, y, width: barW, height: barH, rx: 2, fill: color, "fill-opacity": 0.85 }));
    if (v > 0) {
      svg.appendChild(el("text", { x: x + barW / 2, y: y - 5, "text-anchor": "middle", "font-size": 9, fill: fg }, fmtThousands(v)));
    }
  });

  if (labels && labels.length === count) {
    const labelStep = Math.max(1, Math.floor(count / 12));
    labels.forEach((lab, i) => {
      if (i % labelStep === 0) {
        const x = padL + i * slot + slot / 2;
        svg.appendChild(el("text", { x, y: padT + plotH + 16, "text-anchor": "middle", "font-size": 10, fill: muted }, lab));
      }
    });
  }

  container.appendChild(svg);
}
