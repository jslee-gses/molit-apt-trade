// 지도: 시도 → 시군구 → 읍면동 클릭 드릴다운, 단계구분도(크기=단일 색조 순차, 증감=파랑↔빨강 발산), 순위표, 미니 추이
(async () => {
  const el = (id) => document.getElementById(id);
  const METRICS = {
    median_price: ['중위 거래가', App.fmt.eok, 'seq'],
    median_ppm2: ['㎡당 중위가', App.fmt.ppm2, 'seq'],
    n_trades: ['거래량', (v) => `${App.fmt.int(v)}건`, 'seq'],
    yoy_price: ['중위가 전년 대비', App.fmt.pct, 'div'],
    yoy_n: ['거래량 전년 대비', App.fmt.pct, 'div'],
  };
  const NEXT = { sido: 'sgg', sgg: 'umd' };
  const state = App.readState({ level: 'sido', parent: '', metric: 'median_price', band: 'all', from: '', to: '' });
  const chart = App.chart(el('map'));
  const mini = App.chart(el('mini'));
  const geoCache = {};
  let seq = 0;
  let miniSeq = 0;
  let data = null;

  // 지표/면적 검증
  if (!(state.metric in METRICS)) state.metric = 'median_price';
  if (!el('band').querySelector(`option[value="${state.band}"]`)) state.band = 'all';
  if (!['sido', 'sgg', 'umd'].includes(state.level)) { state.level = 'sido'; state.parent = ''; }

  el('metric').value = state.metric;
  el('band').value = state.band;

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

  function visualMap(kind, nums, f) {
    const common = { type: 'continuous', calculable: true, orient: 'horizontal', left: 'center', bottom: 8, itemHeight: 160, textStyle: { color: App.css('--muted') }, formatter: (v) => f(v) };
    if (!nums.length) return { ...common, show: false, min: 0, max: 1, inRange: { color: [App.css('--track')] } };
    if (kind === 'div') {
      const m = Math.max(...nums.map(Math.abs)) || 1;
      return { ...common, min: -m, max: m, text: ['상승', '하락'], inRange: { color: ['--div-neg-2', '--div-neg-1', '--div-mid', '--div-pos-1', '--div-pos-2'].map(App.css) } };
    }
    return { ...common, min: Math.min(...nums), max: Math.max(...nums), text: ['높음', '낮음'], inRange: { color: ['--seq-100', '--seq-300', '--seq-500', '--seq-700'].map(App.css) } };
  }

  async function render() {
    const my = ++seq;
    state.metric = el('metric').value;
    state.band = el('band').value;
    state.from = App.fromMonthInput(el('from').value) || state.from;
    state.to = App.fromMonthInput(el('to').value) || state.to;
    App.writeState(state);
    App.message(el('msg'), '');
    try {
      const apiData = await App.api('/api/map', { level: state.level, parent: state.parent, band: state.band, from: state.from, to: state.to });
      if (my !== seq) return;
      data = apiData;
      state.from = data.from; state.to = data.to;
      el('from').value = App.toMonthInput(data.from);
      el('to').value = App.toMonthInput(data.to);
      App.writeState(state);
      const fc = await geo(data.version, data.level, data.parent);
      if (my !== seq) return;
      const name = `${data.version}:${data.level}:${data.parent || ''}`;
      echarts.registerMap(name, fc);
      const [title, f, kind] = METRICS[state.metric];
      const vals = data.values.map((v) => ({ name: v.region_cd, value: v[state.metric], raw: v }));
      const nums = vals.map((v) => v.value).filter((v) => v != null);
      chart.setOption({
        tooltip: {
          trigger: 'item', backgroundColor: App.css('--card'), borderColor: App.css('--line'), textStyle: { color: App.css('--text') },
          formatter: (p) => {
            const v = p.data?.raw;
            if (!v) return '';
            return `<b>${App.escapeHtml(v.full_name)}</b><br>${title}: ${f(p.data.value)}<br>거래 ${App.fmt.int(v.n)}건${state.metric.startsWith('yoy') ? '' : `<br>전년 대비 ${App.fmt.pct(v.yoy_price)}`}`;
          },
        },
        visualMap: visualMap(kind, nums, f),
        series: [{
          type: 'map', map: name, nameProperty: 'region_cd', data: vals, roam: true, selectedMode: false,
          top: 16, bottom: 72, left: 8, right: 8,
          itemStyle: { areaColor: App.css('--track'), borderColor: App.css('--card'), borderWidth: 1 },
          emphasis: { label: { show: true, color: App.css('--text'), formatter: (p) => p.data?.raw?.name ?? '' }, itemStyle: { borderColor: App.css('--text'), borderWidth: 2 } },
        }],
      }, true);
      drawCrumbs();
      drawRanking(title, f);
      el('note').textContent = `${App.fmt.ym(data.from)}~${App.fmt.ym(data.to)} · 가격은 월별 중위가의 거래량 가중평균 · 전년 대비는 ${App.fmt.ym(data.prev_from)}~${App.fmt.ym(data.prev_to)}와 비교`
        + (data.coverage == null ? '' : ` · 읍면동 커버리지 ${data.coverage}% (좌표가 있는 단지의 거래 비율)`);
    } catch (e) { if (my !== seq) return; App.message(el('msg'), e.message); }
  }

  function drawCrumbs() {
    const parts = [{ level: 'sido', parent: '', name: '전국' }];
    for (const p of data.parents) parts.push({ level: NEXT[p.level], parent: p.region_cd, name: p.name });
    el('crumbs').innerHTML = parts.map((p, i) => (i === parts.length - 1 ? `<b>${App.escapeHtml(p.name)}</b>`
      : `<a href="#" data-level="${p.level}" data-parent="${p.parent}">${App.escapeHtml(p.name)}</a>`)).join(' › ');
    el('crumbs').querySelectorAll('a').forEach((a) => { a.onclick = (ev) => { ev.preventDefault(); state.level = a.dataset.level; state.parent = a.dataset.parent; render(); }; });
  }

  function drawRanking(title, f) {
    const rows = [...data.values].sort((a, b) => (b[state.metric] ?? -Infinity) - (a[state.metric] ?? -Infinity));
    el('ranking').innerHTML = `<thead><tr><th>#</th><th>지역</th><th class="num">${title}</th><th class="num">거래</th></tr></thead><tbody>${
      rows.map((v, i) => `<tr><td class="muted">${i + 1}</td><td><a href="#" data-cd="${v.region_cd}">${App.escapeHtml(v.name)}</a></td><td class="num">${f(v[state.metric])}</td><td class="num">${App.fmt.int(v.n)}</td></tr>`).join('')}</tbody>`;
    el('ranking').querySelectorAll('a').forEach((a) => { a.onclick = (ev) => { ev.preventDefault(); showMini(data.values.find((v) => v.region_cd === a.dataset.cd)); }; });
  }

  async function showMini(v) {
    if (!v) return;
    const my = ++miniSeq;
    const level = data.level, to = data.to;
    const key = `${level}:${v.region_cd}`;
    el('side').hidden = false;
    mini.resize();
    el('side-name').textContent = `${v.full_name} · 중위 거래가 최근 36개월`;
    el('side-link').href = `/trends?regions=${encodeURIComponent(key)}&band=${state.band}`;
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
    const v = p.data?.raw;
    if (!v) return;
    showMini(v);
    if (NEXT[state.level]) { state.parent = v.region_cd; state.level = NEXT[state.level]; render(); }
  });
  for (const id of ['metric', 'band', 'from', 'to']) el(id).onchange = () => render();
  render();
})();
