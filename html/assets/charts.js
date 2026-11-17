/* Мінімальні SVG-графіки без бібліотек (працюють офлайн). Charts.line / Charts.bars */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var PAL = ['--c1', '--c2', '--c3', '--c4', '--c5', '--c6'];

  function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888'; }
  function color(s, i) { return s.color ? (s.color.charAt(0) === '-' ? css(s.color) : s.color) : css(PAL[i % PAL.length]); }
  function svgEl(tag, attrs, parent) {
    var e = document.createElementNS(NS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function niceMax(v) {
    if (v <= 0) return 1;
    var p = Math.pow(10, Math.floor(Math.log10(v))), f = v / p;
    var n = f <= 1 ? 1 : f <= 2 ? 2 : f <= 2.5 ? 2.5 : f <= 5 ? 5 : 10;
    return n * p;
  }
  function numFmt(v, o) {
    if (o.yFormat) return o.yFormat(v);
    return new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 2 }).format(v);
  }

  function frame(el, o) {
    el.innerHTML = '';
    el.classList.add('chart');
    var W = 820, H = o.height || 320, m = { l: 58, r: 14, t: o.yTitle ? 28 : 14, b: o.xRotate ? 56 : 34 };
    var legend = document.createElement('div'); legend.className = 'legend';
    o.series.forEach(function (s, i) {
      var sp = document.createElement('span');
      sp.innerHTML = '<i style="background:' + color(s, i) + (s.dash ? ';opacity:.6' : '') + '"></i>' + s.name;
      legend.appendChild(sp);
    });
    el.appendChild(legend);
    var svg = svgEl('svg', { viewBox: '0 0 ' + W + ' ' + H, role: 'img', 'aria-label': o.title || 'графік' }, null);
    el.appendChild(svg);
    var tip = document.createElement('div'); tip.className = 'tip'; el.appendChild(tip);
    return { svg: svg, tip: tip, W: W, H: H, m: m };
  }

  function yAxis(f, ymax, o, ymin) {
    var ticks = 5, step = (ymax - ymin) / ticks;
    for (var i = 0; i <= ticks; i++) {
      var v = ymin + step * i, y = f.m.t + (f.H - f.m.t - f.m.b) * (1 - i / ticks);
      svgEl('line', { x1: f.m.l, x2: f.W - f.m.r, y1: y, y2: y, class: i === 0 ? 'ax' : 'gr' }, f.svg);
      var t = svgEl('text', { x: f.m.l - 6, y: y + 4, 'text-anchor': 'end' }, f.svg);
      t.textContent = numFmt(v, o);
    }
    if (o.yTitle) {
      var yt = svgEl('text', { x: 4, y: 12, 'text-anchor': 'start' }, f.svg);
      yt.textContent = o.yTitle;
    }
  }

  function xLabels(f, labels, xs, o) {
    var every = Math.max(1, Math.ceil(labels.length / (o.maxXLabels || 14)));
    labels.forEach(function (lab, i) {
      if (i % every) return;
      var t = svgEl('text', { x: xs[i], y: f.H - f.m.b + 16, 'text-anchor': o.xRotate ? 'end' : 'middle' }, f.svg);
      if (o.xRotate) t.setAttribute('transform', 'rotate(-40 ' + xs[i] + ' ' + (f.H - f.m.b + 16) + ')');
      t.textContent = lab;
    });
  }

  function hover(f, el, o, xs, tipHtml, bandW) {
    var cur = svgEl('line', { y1: f.m.t, y2: f.H - f.m.b, stroke: css('--muted'), 'stroke-width': 1, opacity: 0 }, f.svg);
    var rect = svgEl('rect', { x: f.m.l, y: f.m.t, width: f.W - f.m.l - f.m.r, height: f.H - f.m.t - f.m.b, fill: 'transparent' }, f.svg);
    function move(ev) {
      var box = f.svg.getBoundingClientRect(), scale = f.W / box.width;
      var px = (ev.clientX - box.left) * scale;
      var best = 0, d = 1e9;
      xs.forEach(function (x, i) { var dd = Math.abs(x - px); if (dd < d) { d = dd; best = i; } });
      cur.setAttribute('x1', xs[best]); cur.setAttribute('x2', xs[best]); cur.setAttribute('opacity', 1);
      f.tip.innerHTML = tipHtml(best); f.tip.style.display = 'block';
      var left = xs[best] / scale + 12; if (left > box.width - 170) left = xs[best] / scale - 170;
      f.tip.style.left = Math.max(0, left) + 'px'; f.tip.style.top = (f.m.t + 24) + 'px';
    }
    rect.addEventListener('mousemove', move);
    rect.addEventListener('touchstart', function (e) { move(e.touches[0]); }, { passive: true });
    rect.addEventListener('touchmove', function (e) { move(e.touches[0]); }, { passive: true });
    rect.addEventListener('mouseleave', function () { cur.setAttribute('opacity', 0); f.tip.style.display = 'none'; });
  }

  function tipRows(o, i) {
    var h = '<b>' + o.labels[i] + '</b>';
    o.series.forEach(function (s, k) {
      var v = s.values[i];
      h += '<div><span style="color:' + color(s, k) + '">●</span> ' + s.name + ': <b>' + (v == null ? '—' : numFmt(v, o)) + '</b></div>';
    });
    return h;
  }

  var Charts = {
    /* o: {labels, series:[{name, values, color, dash, width}], yFormat, yTitle, height, yMin, shade:[[i0,i1,label]]} */
    line: function (el, o) {
      var f = frame(el, o);
      var all = []; o.series.forEach(function (s) { s.values.forEach(function (v) { if (v != null) all.push(v); }); });
      var ymin = o.yMin != null ? o.yMin : 0, ymax = o.yMax != null ? o.yMax : niceMax(Math.max.apply(null, all));
      var n = o.labels.length, pw = f.W - f.m.l - f.m.r, ph = f.H - f.m.t - f.m.b;
      var xs = o.labels.map(function (_, i) { return f.m.l + (n === 1 ? pw / 2 : pw * i / (n - 1)); });
      yAxis(f, ymax, o, ymin);
      (o.shade || []).forEach(function (sh) {
        var x0 = xs[sh[0]] - (pw / (n - 1)) / 2, x1 = xs[sh[1]] + (pw / (n - 1)) / 2;
        svgEl('rect', { x: Math.max(f.m.l, x0), y: f.m.t, width: Math.min(f.W - f.m.r, x1) - Math.max(f.m.l, x0), height: ph, fill: css('--assume'), opacity: .13 }, f.svg);
      });
      if (o.refY != null) {
        var ry = f.m.t + ph * (1 - (o.refY - ymin) / (ymax - ymin));
        svgEl('line', { x1: f.m.l, x2: f.W - f.m.r, y1: ry, y2: ry, stroke: css('--muted'), 'stroke-width': 1.2, 'stroke-dasharray': '6 4' }, f.svg);
      }
      xLabels(f, o.labels, xs, o);
      o.series.forEach(function (s, k) {
        var d = '', pen = false;
        s.values.forEach(function (v, i) {
          if (v == null) { pen = false; return; }
          var y = f.m.t + ph * (1 - (v - ymin) / (ymax - ymin));
          d += (pen ? 'L' : 'M') + xs[i].toFixed(1) + ' ' + y.toFixed(1); pen = true;
        });
        svgEl('path', { d: d, fill: 'none', stroke: color(s, k), 'stroke-width': s.width || 2.2, 'stroke-dasharray': s.dash ? '6 4' : '', 'stroke-linejoin': 'round' }, f.svg);
        if (n <= 14) s.values.forEach(function (v, i) {
          if (v == null) return;
          svgEl('circle', { cx: xs[i], cy: f.m.t + ph * (1 - (v - ymin) / (ymax - ymin)), r: 2.8, fill: color(s, k) }, f.svg);
        });
      });
      hover(f, el, o, xs, function (i) { return tipRows(o, i); });
    },

    /* o: {labels, series:[{name, values, color}], yFormat, height, stacked:false, refY} */
    bars: function (el, o) {
      var f = frame(el, o);
      var all = [];
      o.series.forEach(function (s) { s.values.forEach(function (v) { if (v != null) all.push(v); }); });
      var ymin = 0, ymax = o.yMax != null ? o.yMax : niceMax(Math.max.apply(null, all.concat([o.refY || 0])));
      var n = o.labels.length, pw = f.W - f.m.l - f.m.r, ph = f.H - f.m.t - f.m.b;
      var band = pw / n, k = o.series.length, bw = Math.min(46, band * 0.78 / k);
      var xs = o.labels.map(function (_, i) { return f.m.l + band * (i + .5); });
      yAxis(f, ymax, o, ymin);
      xLabels(f, o.labels, xs, o);
      o.series.forEach(function (s, si) {
        s.values.forEach(function (v, i) {
          if (v == null) return;
          var h = ph * (v - ymin) / (ymax - ymin);
          svgEl('rect', { x: xs[i] - (k * bw) / 2 + si * bw, y: f.m.t + ph - h, width: bw - 1, height: Math.max(0, h), fill: color(s, si), rx: 2 }, f.svg);
        });
      });
      if (o.refY != null) {
        var ry = f.m.t + ph * (1 - o.refY / ymax);
        svgEl('line', { x1: f.m.l, x2: f.W - f.m.r, y1: ry, y2: ry, stroke: css('--warn'), 'stroke-width': 1.5, 'stroke-dasharray': '6 4' }, f.svg);
        if (o.refLabel) { var t = svgEl('text', { x: f.W - f.m.r - 2, y: ry - 5, 'text-anchor': 'end', fill: css('--warn') }, f.svg); t.textContent = o.refLabel; t.style.fill = css('--warn'); }
      }
      hover(f, el, o, xs, function (i) { return tipRows(o, i); });
    }
  };
  window.Charts = Charts;
})();
