(function() {
  var style = getComputedStyle(document.documentElement);
  var accent = style.getPropertyValue('--accent').trim();
  var accent2 = style.getPropertyValue('--accent2').trim();
  var ink = style.getPropertyValue('--ink').trim();
  var muted = style.getPropertyValue('--muted').trim();
  var rule = style.getPropertyValue('--rule').trim();
  var bg2 = style.getPropertyValue('--bg2').trim();

  // --- 图 1: 各模块用例通过分布 ---
  var modules = ['server_http','crypto','manifest','health','sync','backup','logcfg','cli','e2e'];
  var counts = [8, 7, 7, 6, 4, 4, 4, 4, 2];

  var chart1 = echarts.init(document.getElementById('chart-modules'), null, { renderer: 'svg' });
  chart1.setOption({
    animation: false,
    grid: { left: 120, right: 40, top: 20, bottom: 20, containLabel: false },
    xAxis: {
      type: 'value',
      axisLine: { lineStyle: { color: rule } },
      splitLine: { lineStyle: { color: rule, type: 'dashed' } },
      axisLabel: { color: muted },
      min: 0, max: 10
    },
    yAxis: {
      type: 'category',
      data: modules,
      inverse: true,
      axisLine: { lineStyle: { color: rule } },
      axisLabel: { color: ink, fontFamily: 'Consolas, monospace' }
    },
    tooltip: {
      trigger: 'axis', appendToBody: true,
      backgroundColor: bg2, borderColor: rule,
      textStyle: { color: ink },
      axisPointer: { type: 'shadow' }
    },
    series: [{
      name: '用例数',
      type: 'bar',
      data: counts,
      barWidth: 18,
      itemStyle: { color: accent, borderRadius: [0, 4, 4, 0] },
      label: { show: true, position: 'right', color: ink, fontWeight: 700, fontSize: 13 }
    }]
  });
  window.addEventListener('resize', function() { chart1.resize(); });
})();
