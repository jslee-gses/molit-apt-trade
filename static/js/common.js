// 공통 도우미: API 호출, 숫자 형식, URL 상태, 차트 기본값(색은 CSS 변수에서 읽어 밝은·어두운 테마를 따른다)
const App = (() => {
  const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

  async function api(path, params = {}) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== '') qs.set(k, v);
    const resp = await fetch(qs.toString() ? `${path}?${qs}` : path, { headers: { Accept: 'application/json' } });
    const data = await resp.json().catch(() => ({}));
    if (!resp.ok) throw new Error(data.error || `요청 실패 (HTTP ${resp.status})`);
    return data;
  }

  const fmt = {
    int: (v) => (v == null ? '-' : Math.round(v).toLocaleString('ko-KR')),
    eok: (v) => (v == null ? '-' : `${(v / 10000).toLocaleString('ko-KR', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}억`),
    ppm2: (v) => (v == null ? '-' : `${Math.round(v).toLocaleString('ko-KR')}만/㎡`),
    pct: (v) => (v == null ? '-' : `${v > 0 ? '+' : ''}${v.toFixed(1)}%`),
    ym: (s) => `${s.slice(0, 4)}-${s.slice(4, 6)}`,
  };

  function shiftYm(ym, n) {
    let y = Number(ym.slice(0, 4));
    let m = Number(ym.slice(4)) + n;
    y += Math.floor((m - 1) / 12);
    m = (((m - 1) % 12) + 12) % 12 + 1;
    return `${y}${String(m).padStart(2, '0')}`;
  }
  function monthRange(from, to) {
    const out = [];
    for (let m = from; m <= to; m = shiftYm(m, 1)) out.push(m);
    return out;
  }
  const toMonthInput = (ym) => (ym ? `${ym.slice(0, 4)}-${ym.slice(4)}` : '');
  const fromMonthInput = (v) => (v || '').replace('-', '');

  function readState(defaults) {
    const p = new URLSearchParams(location.search);
    const out = { ...defaults };
    for (const k of Object.keys(defaults)) if (p.has(k)) out[k] = p.get(k);
    return out;
  }
  function writeState(state) {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(state)) if (v !== '' && v != null) p.set(k, v);
    history.replaceState(null, '', `${location.pathname}?${p}`);
  }

  // 범주 색은 고정 순서 8개. slot은 지역에 붙는다(순위가 아니라).
  const seriesColor = (slot) => css(`--series-${(slot % 8) + 1}`);

  function chart(el) {
    const c = echarts.init(el);
    window.addEventListener('resize', () => c.resize());   // 테마가 바뀌면 theme.js가 새로고침한다
    return c;
  }

  function baseOption() {
    return {
      backgroundColor: 'transparent',
      textStyle: { color: css('--muted'), fontFamily: getComputedStyle(document.body).fontFamily },
      grid: { left: 72, right: 24, top: 40, bottom: 36 },
      tooltip: {
        trigger: 'axis', backgroundColor: css('--card'), borderColor: css('--line'),
        textStyle: { color: css('--text') }, axisPointer: { type: 'line', lineStyle: { color: css('--axis') } },
      },
      legend: { top: 4, textStyle: { color: css('--text') }, icon: 'roundRect', itemWidth: 14, itemHeight: 4 },
      xAxis: {
        type: 'category', axisLine: { lineStyle: { color: css('--axis') } }, axisTick: { show: false },
        axisLabel: { color: css('--muted') },
      },
      yAxis: { type: 'value', splitLine: { lineStyle: { color: css('--grid') } }, axisLabel: { color: css('--muted') } },
    };
  }

  // 잠정 구간 음영(카테고리 축 라벨 'YYYY-MM' 기준)
  function provisionalArea(fromYm, toYm, rangeFrom) {
    if (!fromYm || !toYm || toYm < fromYm) return undefined;
    const start = rangeFrom && rangeFrom > fromYm ? rangeFrom : fromYm;
    return {
      silent: true, itemStyle: { color: css('--provisional') },
      label: { show: true, position: 'insideTop', color: css('--muted'), formatter: '잠정' },
      data: [[{ xAxis: fmt.ym(start) }, { xAxis: fmt.ym(toYm) }]],
    };
  }

  const escapeHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  // 알약 묶음(button.pill[aria-pressed]): 하나만 선택. onChange(value)는 사용자가 바꿨을 때만 부른다.
  function pills(el, onChange) {
    const buttons = () => [...el.querySelectorAll('.pill')];
    const api = {
      get value() {
        const on = buttons().find((b) => b.getAttribute('aria-pressed') === 'true') || buttons()[0];
        return on ? on.dataset.value : '';
      },
      set value(v) { buttons().forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === v))); },
      has: (v) => buttons().some((b) => b.dataset.value === v),
    };
    el.addEventListener('click', (ev) => {
      const b = ev.target.closest('.pill');
      if (!b || !el.contains(b) || b.getAttribute('aria-pressed') === 'true') return;
      api.value = b.dataset.value;
      if (onChange) onChange(b.dataset.value);
    });
    return api;
  }

  // 막대 셀: 숫자(text)를 항상 함께 보여 색만으로 전하지 않는다. 빈 값은 막대 없이 text('-')만.
  function barCell(v, max, colorVar, text) {
    const w = v == null || !max ? 0 : Math.max(2, Math.round((100 * Math.abs(v)) / max));
    return `<span class="bar-cell"><span class="bar-track">${w ? `<i style="width:${w}%;background:var(${colorVar})"></i>` : ''}</span><span class="bar-num">${text}</span></span>`;
  }
  // 증감 막대: 가운데 기준, 상승 = 오른쪽 빨강, 하락 = 왼쪽 파랑
  function divBarCell(v, maxAbs, text) {
    let bar = '';
    if (v != null && maxAbs && Math.abs(v) >= 0.05) {
      const w = Math.max(2, Math.round((50 * Math.min(Math.abs(v), maxAbs)) / maxAbs));
      bar = v >= 0 ? `<i style="left:50%;width:${w}%;background:var(--div-pos-2)"></i>`
        : `<i style="left:${50 - w}%;width:${w}%;background:var(--div-neg-2)"></i>`;
    }
    return `<span class="bar-cell div"><span class="bar-track">${bar}</span><span class="bar-num">${text}</span></span>`;
  }

  function message(el, text, cls = 'err') {
    el.className = `meta ${text ? cls : ''}`;
    el.textContent = text || '';
  }

  // 경계 파일 주소. 정적 파일은 1년 캐시라 배포 버전(?v=)을 붙여 경계를 다시 만들면 새 파일을 받게 한다
  const assetV = document.querySelector('meta[name="asset-version"]')?.content || '';
  const geoUrl = (version, file) => `/static/geo/${encodeURIComponent(version)}/${file}.json${assetV ? `?v=${encodeURIComponent(assetV)}` : ''}`;

  // 배경지도 타일: 브이월드(밝게 회색 지도, 어둡게 야간 지도). 키가 없으면 OpenStreetMap(어둡게는 색 반전).
  // 브이월드 gray 레이어는 제공되지 않아(2026-10 확인) 흰색 지도(white)를 흑백으로 표시해 회색 지도로 쓴다.
  function basemap(map) {
    const key = document.querySelector('meta[name="vworld-key"]')?.content;
    const dark = document.documentElement.getAttribute('data-theme') === 'dark';
    if (key) {
      L.tileLayer(`https://api.vworld.kr/req/wmts/1.0.0/${encodeURIComponent(key)}/${dark ? 'midnight' : 'white'}/{z}/{y}/{x}.png`, {
        minZoom: 6, maxZoom: 19, className: dark ? '' : 'tiles-gray', attribution: '배경지도 <a href="https://www.vworld.kr" target="_blank" rel="noopener">브이월드(국토교통부)</a>',
      }).addTo(map);
    } else {
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
        maxZoom: 19, className: dark ? 'tiles-dark' : '',
        attribution: '&copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a>',
      }).addTo(map);
    }
    return map;
  }

  // 단지 점: 캔버스로 그리고, 점 주변 DOT_TOLERANCE px 안에서도 반응(작은 점도 고르기 쉽게),
  // 확대 수준에 따라 크기를 바꾸고, 마우스를 올린 점은 키우고 굵은 테두리로 강조한다.
  // items: [{lat, lon, ...}], fill: 채움색, extra: 기본 크기에 더할 px(강조할 단지), label(it): 도움말, onClick(it)
  const DOT_TOLERANCE = 8;
  const dotRadius = (z) => (z == null ? 3.5 : z <= 8 ? 2.5 : z <= 11 ? 3.5 : z <= 13 ? 4.5 : z <= 15 ? 6 : 7);
  function dots(map, items, { fill, extra = 0, label, onClick } = {}) {
    if (!map._dotRenderer) map._dotRenderer = L.canvas({ tolerance: DOT_TOLERANCE });
    const card = css('--card'), ink = css('--ink');
    const radius = () => dotRadius(map._loaded ? map.getZoom() : null) + extra;
    const group = L.featureGroup();
    for (const it of items) {
      const m = L.circleMarker([it.lat, it.lon], { renderer: map._dotRenderer, radius: radius(), weight: 0.8, color: card, fillColor: fill, fillOpacity: 0.9 });
      if (label) m.bindTooltip(escapeHtml(label(it)), { direction: 'top', offset: [0, -4] });
      m.on('mouseover', () => { m.setStyle({ weight: 2, color: ink }); m.setRadius(radius() + 3); m.bringToFront(); });
      m.on('mouseout', () => { m.setStyle({ weight: 0.8, color: card }); m.setRadius(radius()); });
      if (onClick) m.on('click', () => onClick(it));
      m.addTo(group);
    }
    const resize = () => { const r = radius(); group.eachLayer((m) => m.setRadius(r)); };
    map.on('zoomend load', resize);
    return group;
  }

  return { dots, basemap, geoUrl, css, api, fmt, shiftYm, monthRange, toMonthInput, fromMonthInput, readState, writeState, seriesColor, chart, baseOption, provisionalArea, escapeHtml, message, pills, barCell, divBarCell };
})();
