// 대시보드: 전국 요약, 24개월 거래량·중위가(차트 2개, 축을 섞지 않음), 상승·하락 시군구
(async () => {
  const el = (id) => document.getElementById(id);
  let data;
  try {
    data = await App.api('/api/summary');
  } catch (e) {
    App.message(el('msg'), e.message);
    return;
  }
  const { kpi, series, movers } = data;
  // 증감 배지: 색(상승 빨강·하락 파랑) + 화살표·부호를 함께 보여 색만으로 전하지 않는다
  const badge = (v, prefix = '') => (v == null ? `<span class="badge">${prefix}-</span>`
    : Math.abs(v) < 0.05 ? `<span class="badge">${prefix}0.0%</span>`
    : `<span class="badge ${v >= 0 ? 'up' : 'down'}">${prefix}${v >= 0 ? '▲' : '▼'} ${App.fmt.pct(v)}</span>`);
  el('kpis').innerHTML = `
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 거래량 (확정)</div><div class="v">${App.fmt.int(kpi.n)}건</div><div class="d">${badge(kpi.yoy_n, '전년 동월 대비 ')}</div></div>
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 전국 중위 거래가</div><div class="v">${App.fmt.eok(kpi.median)}</div><div class="d">${badge(kpi.yoy_median, '전년 동월 대비 ')}</div></div>
    <div class="tile"><div class="k">경계 버전</div><div class="v">${App.escapeHtml(data.version)}</div></div>`;

  const labels = series.map((s) => App.fmt.ym(s.ym));
  const last = series[series.length - 1].ym;
  const prov = App.provisionalArea(data.provisional_from, last, series[0].ym);
  const color = App.seriesColor(0);

  const vol = App.chart(el('vol'));
  vol.setOption({
    ...App.baseOption(),
    legend: { show: false },
    xAxis: { ...App.baseOption().xAxis, data: labels },
    yAxis: { ...App.baseOption().yAxis, axisLabel: { color: App.css('--muted'), formatter: App.fmt.int } },
    tooltip: { ...App.baseOption().tooltip, valueFormatter: (v) => `${App.fmt.int(v)}건` },
    series: [{ name: '거래량', type: 'bar', data: series.map((s) => s.n), itemStyle: { color, borderRadius: [4, 4, 0, 0] }, barMaxWidth: 18, markArea: prov }],
  });

  const price = App.chart(el('price'));
  price.setOption({
    ...App.baseOption(),
    legend: { show: false },
    xAxis: { ...App.baseOption().xAxis, data: labels },
    yAxis: { ...App.baseOption().yAxis, scale: true, axisLabel: { color: App.css('--muted'), formatter: App.fmt.eok } },
    tooltip: { ...App.baseOption().tooltip, valueFormatter: App.fmt.eok },
    series: [{ name: '중위 거래가', type: 'line', data: series.map((s) => s.median), color, symbol: 'none', lineStyle: { width: 2 }, markArea: prov }],
  });

  el('mover-note').textContent = `${App.fmt.ym(movers.window[0])}~${App.fmt.ym(movers.window[1])} vs 전년 같은 기간 · 두 기간 모두 ${movers.min_trades}건 이상 · 중위가는 월별 중위가의 거래량 가중평균`;
  const list = (rows) => (rows.length ? rows.map((m, i) => `<li><a class="rank-item" href="/trends?regions=sgg:${m.region_cd}">`
    + `<span class="rank-no">#${i + 1}</span>`
    + `<span class="rank-name"><b>${App.escapeHtml(m.name)}</b><small>중위 ${App.fmt.eok(m.median)} · 거래 ${App.fmt.int(m.n)}건</small></span>`
    + `${badge(m.yoy)}</a></li>`).join('') : '<li class="rank-empty">해당 시군구 없음</li>');
  el('up').innerHTML = list(movers.up);
  el('down').innerHTML = list(movers.down);
})();
