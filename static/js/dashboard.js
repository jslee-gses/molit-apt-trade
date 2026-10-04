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
  const delta = (v) => `<div class="d ${v == null ? 'muted' : v >= 0 ? 'err' : 'ok'}">전년 동월 대비 ${App.fmt.pct(v)}</div>`;
  el('kpis').innerHTML = `
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 거래량 (확정)</div><div class="v">${App.fmt.int(kpi.n)}건</div>${delta(kpi.yoy_n)}</div>
    <div class="tile"><div class="k">${App.fmt.ym(kpi.ym)} 전국 중위 거래가</div><div class="v">${App.fmt.eok(kpi.median)}</div>${delta(kpi.yoy_median)}</div>
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

  el('mover-note').textContent = `(${App.fmt.ym(movers.window[0])}~${App.fmt.ym(movers.window[1])} vs 전년 같은 기간, 두 기간 모두 ${movers.min_trades}건 이상) · 중위가는 월별 중위가의 거래량 가중평균`;
  const table = (rows, title) => `<thead><tr><th>${title}</th><th class="num">전년 대비</th><th class="num">중위가</th><th class="num">거래</th></tr></thead><tbody>${
    rows.length ? rows.map((m) => `<tr><td><a href="/trends?regions=sgg:${m.region_cd}">${App.escapeHtml(m.name)}</a></td><td class="num">${App.fmt.pct(m.yoy)}</td><td class="num">${App.fmt.eok(m.median)}</td><td class="num">${App.fmt.int(m.n)}</td></tr>`).join('')
      : '<tr><td colspan="4" class="muted">해당 시군구 없음</td></tr>'}</tbody>`;
  el('up').innerHTML = table(movers.up, '상승 상위');
  el('down').innerHTML = table(movers.down, '하락 상위');
})();
