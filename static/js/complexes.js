// 단지 검색: 검색 목록과 같은 조건(단지명·지역)의 좌표 있는 단지를 모두 배경지도 위 점으로(전 기간, 캔버스로 그림).
// 조건이 없으면(전국) 최근 거래 순 3,000개. 점을 누르면 단지 상세.
(async () => {
  const el = (id) => document.getElementById(id);
  // 시작 위치를 정하지 않고 단지 위치를 받은 뒤 한 번에 맞춘다(전국을 먼저 그렸다가 확대하는 깜빡임 방지)
  const map = App.basemap(L.map(el('cmap'), { preferCanvas: true, zoomControl: false }));
  L.control.zoom({ position: 'topright' }).addTo(map);   // 왼쪽 위는 행정구역 경로 상자 자리
  const nationView = () => map.setView([36.4, 127.9], 7, { animate: false });
  try {
    const data = await App.api('/api/complexes/locations', window.SEARCH || {});
    const items = data.complexes.map(([apt_seq, apt_nm, lon, lat]) => ({ apt_seq, apt_nm, lon, lat }));
    const layer = App.dots(map, items, {
      fill: App.css('--accent'), label: (c) => c.apt_nm || c.apt_seq,
      onClick: (c) => { location.href = `/complexes/${encodeURIComponent(c.apt_seq)}`; },
    }).addTo(map);
    if (data.complexes.length) map.fitBounds(layer.getBounds(), { padding: [24, 24], maxZoom: 16, animate: false });
    else nationView();
    const s = window.SEARCH || {};
    el('cmap-note').textContent = `지도에 ${App.fmt.int(data.complexes.length)}개 단지 · ${s.q || s.region ? '조건에 맞는 단지 중 좌표가 있는 단지 모두' : '전국은 최근 거래 순 3,000개(지역을 고르면 그 지역 단지 모두)'} · 점을 누르면 단지 상세`;
  } catch (e) { nationView(); App.message(el('msg'), e.message); }
})();
