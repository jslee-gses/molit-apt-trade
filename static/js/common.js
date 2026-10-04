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
    window.addEventListener('resize', () => c.resize());
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => location.reload());
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

  return { css, api, fmt, shiftYm, monthRange, toMonthInput, fromMonthInput, readState, writeState, seriesColor, chart, baseOption, provisionalArea, escapeHtml, message, pills, barCell, divBarCell };
})();
