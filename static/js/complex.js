// 단지 상세: 계약일 x 가격 산점도(면적 구간 3색), 요약 카드(면적 구간별 거래 수·최고가·최저가)
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

  // 요약 카드: 면적 구간별 거래 수, 최고가·최저가(같은 값이면 최근 거래)
  el('stat-areas').innerHTML = BANDS.map(([, label, test]) =>
    `<dt>${label}</dt><dd>${App.fmt.int(trades.filter((t) => t.area != null && test(t.area)).length)}건</dd>`).join('');
  const detail = (t) => `${t.deal_date} · 전용 ${t.area ?? '-'}㎡ · ${t.floor ?? '-'}층`;
  if (trades.length) {
    const byRecent = [...trades].sort((a, b) => b.deal_date.localeCompare(a.deal_date));
    const max = byRecent.reduce((m, t) => (t.deal_amount > m.deal_amount ? t : m));
    const min = byRecent.reduce((m, t) => (t.deal_amount < m.deal_amount ? t : m));
    el('stat-max').textContent = App.fmt.eok(max.deal_amount);
    el('stat-max-d').textContent = detail(max);
    el('stat-min').textContent = App.fmt.eok(min.deal_amount);
    el('stat-min-d').textContent = detail(min);
  }

  // 위치: 배경지도 위에 그 읍면동 경계, 주변 단지(회색)와 이 단지(강조)
  if (el('loc')) {
    try {
      const nb = await App.api(`/api/complexes/${encodeURIComponent(window.APT_SEQ)}/nearby`);
      const map = App.basemap(L.map(el('loc'), { preferCanvas: true, zoomControl: false }).setView([36.4, 127.9], 7));
      L.control.zoom({ position: 'topright' }).addTo(map);   // 왼쪽 위는 행정구역 경로 상자 자리
      if (!nb.umd_cd || !nb.complexes.length) {
        App.message(el('msg'), '읍면동이 판정되지 않아 위치 지도에 경계를 그리지 않습니다.', 'meta');
      } else {
        const resp = await fetch(App.geoUrl(nb.version, `umd_${nb.umd_cd.slice(0, 5)}`));
        if (!resp.ok) throw new Error('경계 파일을 불러오지 못했습니다.');
        const fc = await resp.json();
        const area = L.geoJSON({ type: 'FeatureCollection', features: fc.features.filter((f) => f.properties.region_cd === nb.umd_cd) }, {
          style: { color: App.css('--ink'), weight: 1.5, fillColor: App.css('--accent'), fillOpacity: 0.06 }, interactive: false,
        }).addTo(map);
        const label = (c) => c.apt_nm || c.apt_seq;
        App.dots(map, nb.complexes.filter((x) => !x.is_self), {
          fill: App.css('--muted'), label, onClick: (c) => { location.href = `/complexes/${encodeURIComponent(c.apt_seq)}`; },
        }).addTo(map);
        App.dots(map, nb.complexes.filter((x) => x.is_self), { fill: App.css('--series-2'), extra: 4, label }).addTo(map);   // 이 단지는 크게
        map.fitBounds(area.getBounds(), { padding: [16, 16] });
      }
    } catch (e) { App.message(el('msg'), e.message); }
  }
})();
