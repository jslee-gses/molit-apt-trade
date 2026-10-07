// 지도: 시도 → 시군구 → 읍면동 드릴다운 단계구분도 + 오른쪽 순위 목록 카드 + 아래 지표 비교표
// 색: 크기 지표 = 남색 7단계(--seq-*), 증감 지표 = 파랑↔회색↔빨강 5단계(--div-*, 상승 = 빨강). 지도와 순위 점이 같은 단계를 쓴다.
// 읍면동 단계에서는 단지 위치 점(scatter, geo 좌표계)을 겹친다. 크기 = 거래량, 색 = 지표(전년 대비 지표는 단지 값이 없어 중립색).
(async () => {
  const el = (id) => document.getElementById(id);
  const esc = App.escapeHtml;
  const cnt = (v) => `${App.fmt.int(v)}건`;
  // 지표 키(URL metric) → [이름, /api/map values의 필드, 형식, 색 종류]
  const METRICS = {
    median_price: ['중위 거래가', 'median_price', App.fmt.eok, 'seq'],
    median_ppm2: ['㎡당 중위가', 'median_ppm2', App.fmt.ppm2, 'seq'],
    n_trades: ['거래량', 'n', cnt, 'seq'],
    yoy_price: ['중위가 전년 대비', 'yoy_price', App.fmt.pct, 'div'],
    yoy_n: ['거래량 전년 대비', 'yoy_n', App.fmt.pct, 'div'],
  };
  const COLS = [
    { field: 'median_price', label: '중위가', f: App.fmt.eok, color: '--m-price' },
    { field: 'median_ppm2', label: '㎡당가', f: App.fmt.ppm2, color: '--m-ppm2' },
    { field: 'n', label: '거래량', f: cnt, color: '--m-count' },
    { field: 'yoy_price', label: '중위가 전년 대비', f: App.fmt.pct, div: true },
    { field: 'yoy_n', label: '거래량 전년 대비', f: App.fmt.pct, div: true },
  ];
  const RAMP = {
    seq: ['--seq-100', '--seq-200', '--seq-300', '--seq-400', '--seq-500', '--seq-600', '--seq-700'],
    div: ['--div-neg-2', '--div-neg-1', '--div-mid', '--div-pos-1', '--div-pos-2'],
  };
  const NEXT = { sido: 'sgg', sgg: 'umd' };
  const state = App.readState({ level: 'sido', parent: '', metric: 'median_price', band: 'all', from: '', to: '', pts: '1' });
  const chart = App.chart(el('map'));
  const mini = App.chart(el('mini'));
  const geoCache = {};
  let seq = 0;
  let miniSeq = 0;
  let data = null;
  let sideCode = null;
  let selected = null;   // 순위에서 고른 지역 코드
  let sort = null;       // 비교표 정렬 { field, dir }. null이면 현재 지표 내림차순

  const metricPills = App.pills(el('metric'), () => { sort = null; render(); });
  const bandPills = App.pills(el('band'), () => render());
  const ptsPills = App.pills(el('pts'), () => render());
  if (!ptsPills.has(state.pts)) state.pts = '1';
  ptsPills.value = state.pts;
  // 지표 → 단지 점 색에 쓸 /api/map/complexes 필드(전년 대비는 없음)
  const POINT_FIELD = { median_price: 'median_price', median_ppm2: 'median_ppm2', n_trades: 'n' };
  let points = [];
  if (!Object.hasOwn(METRICS, state.metric)) state.metric = 'median_price';
  if (!bandPills.has(state.band)) state.band = 'all';
  if (!['sido', 'sgg', 'umd'].includes(state.level)) { state.level = 'sido'; state.parent = ''; }
  // 주소의 수준·상위 코드 자릿수가 맞지 않으면(이전 오류로 남은 주소 등) 전국으로
  if ((state.level === 'sgg' && !/^\d{2}$/.test(state.parent)) || (state.level === 'umd' && !/^\d{5}$/.test(state.parent))) {
    state.level = 'sido'; state.parent = '';
  }
  if (state.level !== 'sido' && !state.parent) state.level = 'sido';
  metricPills.value = state.metric;
  bandPills.value = state.band;

  async function geo(version, level, parent) {
    const file = level === 'sido' ? 'sido' : level === 'sgg' ? 'sgg' : `umd_${parent.slice(0, 2)}`;
    const url = `/static/geo/${encodeURIComponent(version)}/${file}.json`;
    if (!geoCache[url]) {
      const resp = await fetch(url);
      if (!resp.ok) throw new Error('경계 파일을 불러오지 못했습니다.');
      geoCache[url] = await resp.json();
    }
    const fc = geoCache[url];
    if (level === 'sido') return fc;
    const key = level === 'sgg' ? 'sido_cd' : 'sgg_cd';
    return { type: 'FeatureCollection', features: fc.features.filter((f) => f.properties[key] === parent) };
  }

  function scaleOf(kind, field) {
    const nums = data.values.map((v) => v[field]).filter((v) => v != null);
    if (!nums.length) return null;
    if (kind === 'div') { const m = Math.max(...nums.map(Math.abs)) || 1; return { min: -m, max: m }; }
    return { min: Math.min(...nums), max: Math.max(...nums) };
  }
  function colorFor(v, kind, sc) {
    if (v == null || !sc) return App.css('--nodata');
    const stops = RAMP[kind];
    const t = sc.max === sc.min ? 1 : (v - sc.min) / (sc.max - sc.min);
    return App.css(stops[Math.round(Math.max(0, Math.min(1, t)) * (stops.length - 1))]);
  }
  // 빈 값은 방향과 관계없이 맨 아래
  function sortRows(rows, field, dir) {
    return [...rows].sort((a, b) => {
      if (field === 'name') return dir * a.name.localeCompare(b.name, 'ko');
      const x = a[field], y = b[field];
      if (x == null || y == null) return x == null && y == null ? a.name.localeCompare(b.name, 'ko') : x == null ? 1 : -1;
      return dir * (x - y) || a.name.localeCompare(b.name, 'ko');
    });
  }

  async function render() {
    const my = ++seq;
    state.metric = metricPills.value;
    state.band = bandPills.value;
    state.pts = ptsPills.value;
    state.from = App.fromMonthInput(el('from').value) || state.from;
    state.to = App.fromMonthInput(el('to').value) || state.to;
    App.writeState(state);
    App.message(el('msg'), '');
    try {
      const apiData = await App.api('/api/map', { level: state.level, parent: state.parent, band: state.band, from: state.from, to: state.to });
      if (my !== seq) return;
      const fc = await geo(apiData.version, apiData.level, apiData.parent);
      if (my !== seq) return;
      let pts = [];
      let ptsFailed = false;
      if (apiData.level === 'umd' && state.pts === '1') {
        try {
          pts = (await App.api('/api/map/complexes', { parent: apiData.parent, band: state.band, from: apiData.from, to: apiData.to })).complexes;
        } catch (e) { pts = []; ptsFailed = true; }   // 점 요청 실패가 지역 지도·순위·비교표를 막지 않게 한다
      }
      if (my !== seq) return;
      if (ptsFailed) App.message(el('msg'), '단지 점을 불러오지 못했습니다. 지역 지도만 표시합니다.');
      points = pts;
      data = apiData;
      if (el('side').hidden === false && !data.values.some((v) => v.region_cd === sideCode)) el('side-drill').hidden = true;
      if (selected && !data.values.some((v) => v.region_cd === selected)) { selected = null; el('side').hidden = true; }
      state.from = data.from; state.to = data.to;
      el('from').value = App.toMonthInput(data.from);
      el('to').value = App.toMonthInput(data.to);
      App.writeState(state);
      const name = `${data.version}:${data.level}:${data.parent || ''}`;
      echarts.registerMap(name, fc);
      drawMap(name);
      drawCrumbs();
      drawLegend();
      drawRanking();
      drawCompare();
      drawNote();
      if (selected) showMini(data.values.find((v) => v.region_cd === selected));
    } catch (e) { if (my !== seq) return; App.message(el('msg'), e.message); }
  }

  function pointSize(n) { return n ? Math.max(4, Math.min(14, 3 + Math.sqrt(n) * 1.5)) : 3; }

  function pointSeries() {
    const pf = POINT_FIELD[state.metric];
    const vals = pf ? points.map((c) => c[pf]).filter((v) => v != null).sort((a, b) => a - b) : [];
    // 단지 값의 분위수로 7단계(지도와 같은 남색 단계)
    const color = (v) => {
      if (!pf || v == null || !vals.length) return App.css('--muted');
      let lo = 0, hi = vals.length;
      while (lo < hi) { const mid = (lo + hi) >> 1; if (vals[mid] < v) lo = mid + 1; else hi = mid; }
      return App.css(RAMP.seq[Math.min(RAMP.seq.length - 1, Math.floor((lo / vals.length) * RAMP.seq.length))]);
    };
    return {
      type: 'scatter', coordinateSystem: 'geo', geoIndex: 0, z: 3,
      data: points.map((c) => ({
        value: [c.lon, c.lat], c, symbolSize: pointSize(c.n),
        itemStyle: { color: c.n ? color(pf ? c[pf] : null) : App.css('--nodata'), borderColor: App.css('--card'), borderWidth: 1 },
      })),
      emphasis: { scale: 1.5, itemStyle: { borderColor: App.css('--ink'), borderWidth: 1.5 } },
    };
  }

  function drawMap(name) {
    const [title, field, f, kind] = METRICS[state.metric];
    const sc = scaleOf(kind, field);
    const byCd = Object.fromEntries(data.values.map((v) => [v.region_cd, v]));
    const regionTip = (v) => (v ? `<b>${esc(v.full_name)}</b><br>${title}: ${f(v[field])}<br>거래 ${cnt(v.n)}`
      + `${state.metric.startsWith('yoy') ? '' : `<br>중위가 전년 대비 ${App.fmt.pct(v.yoy_price)}`}` : '');
    const pointTip = (c) => (c ? `<b>${esc(c.apt_nm || c.apt_seq)}</b><br>거래 ${cnt(c.n)}<br>중위가 ${App.fmt.eok(c.median_price)}`
      + `<br>㎡당 ${App.fmt.ppm2(c.median_ppm2)}<br><small>누르면 단지 상세</small>` : '');
    const nameLabel = (p) => byCd[p.name]?.name ?? '';
    chart.setOption({
      tooltip: {
        trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
        formatter: (p) => (p.componentType === 'geo' ? regionTip(byCd[p.name]) : pointTip(p.data?.c)),
      },
      geo: {
        map: name, nameProperty: 'region_cd', roam: true, selectedMode: 'single',
        top: 12, bottom: 12, left: 8, right: 8, tooltip: { show: true },
        regions: data.values.map((v) => {
          const c = colorFor(v[field], kind, sc);
          return { name: v.region_cd, itemStyle: { areaColor: c }, emphasis: { itemStyle: { areaColor: c } }, select: { itemStyle: { areaColor: c } } };
        }),
        itemStyle: { areaColor: App.css('--nodata'), borderColor: App.css('--card'), borderWidth: 1 },
        emphasis: { label: { show: true, color: App.css('--text'), formatter: nameLabel },
          itemStyle: { borderColor: App.css('--ink'), borderWidth: 2 } },
        select: { label: { show: true, color: App.css('--text'), formatter: nameLabel },
          itemStyle: { borderColor: App.css('--ink'), borderWidth: 2.5 } },
      },
      series: points.length ? [pointSeries()] : [],
    }, true);
    if (selected) chart.dispatchAction({ type: 'geoSelect', geoIndex: 0, name: selected });
  }

  function drawCrumbs() {
    const parts = [{ level: 'sido', parent: '', name: '전국' }];
    for (const p of data.parents) parts.push({ level: NEXT[p.level], parent: p.region_cd, name: p.name });
    el('crumbs').innerHTML = parts.map((p, i) => (i === parts.length - 1 ? `<b>${esc(p.name)}</b>`
      : `<a href="#" data-level="${esc(p.level)}" data-parent="${esc(p.parent)}">${esc(p.name)}</a>`)).join(' › ');
    el('crumbs').querySelectorAll('a').forEach((a) => {
      a.onclick = (ev) => {
        ev.preventDefault();
        state.level = a.dataset.level; state.parent = a.dataset.parent;
        selected = null; el('side').hidden = true; el('rank-q').value = '';
        render();
      };
    });
  }

  function drawLegend() {
    const [, field, f, kind] = METRICS[state.metric];
    const sc = scaleOf(kind, field);
    el('legend-ramp').innerHTML = RAMP[kind].map((s) => `<i style="background:var(${s})"></i>`).join('');
    if (!sc) { el('legend-lo').textContent = '값 없음'; el('legend-hi').textContent = ''; return; }
    el('legend-lo').textContent = `${kind === 'div' ? '하락' : '낮음'} ${f(sc.min)}`;
    el('legend-hi').textContent = `${kind === 'div' ? '상승' : '높음'} ${f(sc.max)}`;
  }

  function drawRanking() {
    const [title, field, f, kind] = METRICS[state.metric];
    const sc = scaleOf(kind, field);
    el('rank-title').textContent = `${title} 순위`;
    const q = el('rank-q').value.trim();
    const items = sortRows(data.values, field, -1).map((v, i) => ({ v, i }))
      .filter(({ v }) => !q || v.name.includes(q) || v.full_name.includes(q));
    el('ranking').innerHTML = items.map(({ v, i }) => `<li><button type="button" class="rank-item${v.region_cd === selected ? ' on' : ''}" data-cd="${esc(v.region_cd)}" aria-pressed="${v.region_cd === selected}">`
      + `<span class="rank-no">${v[field] == null ? '-' : `#${i + 1}`}</span>`
      + `<span class="rank-name"><b>${esc(v.name)}</b><small>거래 ${cnt(v.n)}</small></span>`
      + `<span class="rank-val">${f(v[field])}</span>`
      + `<span class="rank-dot" style="background:${colorFor(v[field], kind, sc)}"></span></button></li>`).join('')
      || '<li class="rank-empty">찾는 지역이 없습니다.</li>';
    el('ranking').querySelectorAll('.rank-item').forEach((b) => { b.onclick = () => select(b.dataset.cd); });
  }

  function drawCompare() {
    const s = sort || { field: METRICS[state.metric][1], dir: -1 };
    const maxes = Object.fromEntries(COLS.map((c) => [c.field, Math.max(0, ...data.values.map((v) => Math.abs(v[c.field] ?? 0)))]));
    const head = [{ field: 'name', label: '지역' }, ...COLS].map((c) => {
      const on = c.field === s.field;
      const ariaSort = on ? (s.dir > 0 ? 'ascending' : 'descending') : 'none';
      return `<th scope="col" aria-sort="${ariaSort}"${c.field === 'name' ? '' : ' class="num"'}><button type="button" class="sort" data-field="${c.field}">${c.label}`
        + `<span class="sort-ind" aria-hidden="true">${on ? (s.dir > 0 ? '▲' : '▼') : '▲▼'}</span></button></th>`;
    }).join('');
    const rows = sortRows(data.values, s.field, s.dir);
    const body = rows.map((v) => `<tr><td><a href="/trends?regions=${encodeURIComponent(`${data.level}:${v.region_cd}`)}&band=${encodeURIComponent(state.band)}"><b>${esc(v.name)}</b></a></td>`
      + COLS.map((c) => `<td class="num">${c.div ? App.divBarCell(v[c.field], maxes[c.field], c.f(v[c.field]))
        : App.barCell(v[c.field], maxes[c.field], c.color, c.f(v[c.field]))}</td>`).join('') + '</tr>').join('');
    el('compare').innerHTML = `<thead><tr>${head}</tr></thead><tbody>${body || `<tr><td colspan="${COLS.length + 1}" class="muted">지역이 없습니다.</td></tr>`}</tbody>`;
    el('compare').querySelectorAll('button.sort').forEach((b) => {
      b.onclick = () => {
        const fld = b.dataset.field;
        const cur = sort || { field: METRICS[state.metric][1], dir: -1 };
        sort = { field: fld, dir: cur.field === fld ? -cur.dir : (fld === 'name' ? 1 : -1) };
        drawCompare();
      };
    });
  }

  function drawNote() {
    el('note').textContent = `${data.values.length}개 지역 · ${App.fmt.ym(data.from)}~${App.fmt.ym(data.to)} · 가격은 월별 중위가의 거래량 가중평균 · 전년 대비는 ${App.fmt.ym(data.prev_from)}~${App.fmt.ym(data.prev_to)}와 비교`
      + (data.coverage == null ? '' : ` · 읍면동 커버리지 ${data.coverage}% (읍면동이 판정된 단지의 거래 비율)`)
      + (data.level === 'umd' && state.pts === '1' ? ` · 단지 점 ${points.length}개(좌표가 있는 단지)` : '');
  }

  function select(code) {
    const v = data.values.find((x) => x.region_cd === code);
    if (!v) return;
    selected = code;
    chart.dispatchAction({ type: 'geoSelect', geoIndex: 0, name: code });
    el('ranking').querySelectorAll('.rank-item').forEach((b) => { b.classList.toggle('on', b.dataset.cd === code); b.setAttribute('aria-pressed', String(b.dataset.cd === code)); });
    showMini(v);
  }

  async function showMini(v) {
    if (!v) return;
    const my = ++miniSeq;
    const level = data.level, to = data.to;
    const key = `${level}:${v.region_cd}`;
    el('side').hidden = false;
    sideCode = v.region_cd;
    mini.resize();
    el('side-name').textContent = `${v.full_name} · 중위 거래가 최근 36개월`;
    el('side-link').href = `/trends?regions=${encodeURIComponent(key)}&band=${encodeURIComponent(state.band)}`;
    el('side-drill').hidden = !NEXT[level];
    el('side-drill').onclick = () => {
      state.parent = v.region_cd; state.level = NEXT[level];
      selected = null; el('side').hidden = true; el('rank-q').value = '';
      render();
    };
    try {
      const agg = await App.api('/api/agg', { regions: key, band: state.band, from: App.shiftYm(to, -35), to });
      if (my !== miniSeq) return;
      const months = App.monthRange(agg.from, agg.to);
      const by = Object.fromEntries(agg.series[0].points.map((p) => [p.ym, p]));
      const base = App.baseOption();
      mini.setOption({
        ...base, legend: { show: false }, grid: { left: 56, right: 12, top: 12, bottom: 24 },
        xAxis: { ...base.xAxis, data: months.map(App.fmt.ym) },
        yAxis: { ...base.yAxis, scale: true, axisLabel: { color: App.css('--muted'), formatter: App.fmt.eok } },
        tooltip: { ...base.tooltip, valueFormatter: App.fmt.eok },
        series: [{ name: '중위 거래가', type: 'line', data: months.map((m) => by[m]?.median_price ?? null), color: App.seriesColor(0), symbol: 'none', lineStyle: { width: 2 } }],
      }, true);
    } catch (e) { if (my !== miniSeq) return; App.message(el('msg'), e.message); }
  }

  chart.on('click', (p) => {
    if (p.componentType === 'series' && p.seriesType === 'scatter') {
      const c = p.data?.c;
      if (c) location.href = `/complexes/${encodeURIComponent(c.apt_seq)}`;
      return;
    }
    if (p.componentType !== 'geo') return;
    const v = data.values.find((x) => x.region_cd === p.name);
    if (!v) return;
    // 지금 그려진 지도(data.level) 기준으로 내려간다. 새 지도가 그려지기 전 두 번째 클릭(더블클릭)이
    // state.level(이미 다음 단계)로 계산되면 시도 코드로 읍면동을 요청해 400이 난다.
    if (NEXT[data.level]) {
      showMini(v);
      state.parent = v.region_cd; state.level = NEXT[data.level];
      selected = null; el('rank-q').value = '';
      render();
    } else {
      select(v.region_cd);
    }
  });
  el('rank-q').oninput = () => { if (data) drawRanking(); };
  for (const id of ['from', 'to']) el(id).onchange = () => render();
  render();
})();
