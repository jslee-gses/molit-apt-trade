// 단지 검색: 검색 목록과 같은 조건(단지명·지역)의 좌표 있는 단지를 모두 배경지도 위 점으로(전 기간, 캔버스로 그림).
// 조건이 없으면(전국) 최근 거래 순 3,000개. 점을 누르면 단지 상세.
(async () => {
  const el = (id) => document.getElementById(id);
  const map = App.basemap(L.map(el('cmap'), { preferCanvas: true }).setView([36.4, 127.9], 7));
  try {
    const data = await App.api('/api/complexes/locations', window.SEARCH || {});
    const style = { radius: 3.5, weight: 0.8, color: App.css('--card'), fillColor: App.css('--accent'), fillOpacity: 0.9 };   // 4만여 개라 한 번만 읽는다
    const layer = L.featureGroup();
    for (const [apt_seq, apt_nm, lon, lat] of data.complexes) {
      const c = { apt_seq, apt_nm, lon, lat };
      L.circleMarker([c.lat, c.lon], style)
        .bindTooltip(App.escapeHtml(c.apt_nm || c.apt_seq), { direction: 'top' })
        .on('click', () => { location.href = `/complexes/${encodeURIComponent(c.apt_seq)}`; })
        .addTo(layer);
    }
    layer.addTo(map);
    if (data.complexes.length) map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 16 });
    const s = window.SEARCH || {};
    el('cmap-note').textContent = `지도에 ${App.fmt.int(data.complexes.length)}개 단지 · ${s.q || s.region ? '조건에 맞는 단지 중 좌표가 있는 단지 모두' : '전국은 최근 거래 순 3,000개(지역을 고르면 그 지역 단지 모두)'} · 점을 누르면 단지 상세`;
  } catch (e) { App.message(el('msg'), e.message); }
})();
