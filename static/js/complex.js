// 단지 상세: 계약일 x 가격 산점도(면적 구간 3색), 면적 구간별·층별 거래 수
(async () => {
  const el = (id) => document.getElementById(id);
  const BANDS = [['le60', '60㎡ 이하', (a) => a <= 60], ['60_85', '60~85㎡', (a) => a > 60 && a <= 85], ['gt85', '85㎡ 초과', (a) => a > 85]];
  let data;
  try {
    data = await App.api(`/api/complexes/${encodeURIComponent(window.APT_SEQ)}`);
  } catch (e) { App.message(el('msg'), e.message); return; }
  const trades = data.trades.filter((t) => !t.is_cancelled && t.deal_date && t.deal_amount != null);
  const base = App.baseOption();

  const scatter = App.chart(el('scatter'));
  scatter.setOption({
    ...base,
    tooltip: {
      trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
      formatter: (p) => { const t = p.data.t; return `${t.deal_date}<br><b>${App.fmt.eok(t.deal_amount)}</b> (${App.fmt.int(t.deal_amount)}만원)<br>전용 ${t.area ?? '-'}㎡ · ${t.floor ?? '-'}층${t.apt_dong ? ` · ${App.escapeHtml(t.apt_dong)}동` : ''}`; },
    },
    xAxis: { ...base.xAxis, type: 'time' },
    yAxis: { ...base.yAxis, scale: true, axisLabel: { color: App.css('--muted'), formatter: App.fmt.eok } },
    series: BANDS.map(([, label, test], i) => ({
      name: label, type: 'scatter', symbolSize: 8, color: App.seriesColor(i),
      itemStyle: { borderColor: App.css('--card'), borderWidth: 1 },   // 겹치는 점 구분용 테두리
      data: trades.filter((t) => t.area != null && test(t.area)).map((t) => ({ value: [t.deal_date, t.deal_amount], t })),
    })),
  });

  const bar = (node, labels, counts, unit) => {
    const c = App.chart(node);
    c.setOption({
      ...base, legend: { show: false }, grid: { left: 48, right: 12, top: 16, bottom: 28 },
      xAxis: { ...base.xAxis, data: labels },
      tooltip: { ...base.tooltip, valueFormatter: (v) => `${App.fmt.int(v)}${unit}` },
      series: [{ name: '거래 수', type: 'bar', data: counts, color: App.seriesColor(0), barMaxWidth: 28, itemStyle: { borderRadius: [4, 4, 0, 0] } }],
    });
  };
  bar(el('areas'), BANDS.map((b) => b[1]), BANDS.map(([, , test]) => trades.filter((t) => t.area != null && test(t.area)).length), '건');
  const FLOORS = [['지하', (f) => f < 1], ['1~5', (f) => f >= 1 && f <= 5], ['6~10', (f) => f >= 6 && f <= 10], ['11~15', (f) => f >= 11 && f <= 15], ['16~20', (f) => f >= 16 && f <= 20], ['21+', (f) => f >= 21]];
  bar(el('floors'), FLOORS.map((f) => f[0]), FLOORS.map(([, test]) => trades.filter((t) => t.floor != null && test(t.floor)).length), '건');
})();
