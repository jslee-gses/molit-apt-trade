// 단지 검색: 검색 목록과 같은 조건(단지명·지역)의 단지를 배경지도 위 점으로(최근 거래 순 최대 3,000개). 점을 누르면 단지 상세.
(async () => {
  const el = (id) => document.getElementById(id);
  const map = App.basemap(L.map(el('cmap'), { preferCanvas: true }).setView([36.4, 127.9], 7));
  try {
    const data = await App.api('/api/complexes/locations', window.SEARCH || {});
    const color = App.css('--accent');
    const layer = L.featureGroup();
    for (const c of data.complexes) {
      L.circleMarker([c.lat, c.lon], { radius: 4, weight: 1, color: App.css('--card'), fillColor: color, fillOpacity: 0.9 })
        .bindTooltip(App.escapeHtml(c.apt_nm || c.apt_seq), { direction: 'top' })
        .on('click', () => { location.href = `/complexes/${encodeURIComponent(c.apt_seq)}`; })
        .addTo(layer);
    }
    layer.addTo(map);
    if (data.complexes.length) map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 16 });
    el('cmap-note').textContent = `지도에 ${App.fmt.int(data.complexes.length)}개 단지 · 좌표가 있는 단지, 최근 거래 순 최대 3,000개 · 점을 누르면 단지 상세`;
  } catch (e) { App.message(el('msg'), e.message); }
})();
