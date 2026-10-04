// 추이: 지역 최대 8개(수준 혼합), 지표·면적·기간, 3개월 이동평균, 시작월=100 지수, 표 보기, PNG 저장
(async () => {
  const MAX = 8;
  const el = (id) => document.getElementById(id);
  const METRICS = {
    median_price: ['중위 거래가', App.fmt.eok],
    median_ppm2: ['㎡당 중위가', App.fmt.ppm2],
    n_trades: ['거래량', (v) => `${App.fmt.int(v)}건`],
    range: ['25~75% 범위', App.fmt.eok],
  };
  const state = App.readState({ regions: 'nation:00', metric: 'median_price', band: 'all', from: '', to: '', ma: '', idx: '' });
  const chart = App.chart(el('chart'));
  let seq = 0;
  let picked = state.regions.split(',').filter(Boolean).slice(0, MAX)
    .map((key, slot) => ({ key, name: key, slot }));
  let last = null;

  // 지표·면적: 알약. 사용자가 바꾸면 다시 그린다.
  const metricPills = App.pills(el('metric'), () => render());
  const bandPills = App.pills(el('band'), () => render());
  if (!Object.hasOwn(METRICS, state.metric)) state.metric = 'median_price';
  if (!bandPills.has(state.band)) state.band = 'all';
  metricPills.value = state.metric;
  bandPills.value = state.band;
  el('ma').checked = state.ma === '1';
  el('idx').checked = state.idx === '1';

  async function fill(sel, level, parent, placeholder) {
    sel.innerHTML = '';
    sel.add(new Option(placeholder, ''));
    if (level !== 'sido' && !parent) return;
    try {
      const data = await App.api('/api/regions', { level, parent });
      for (const r of data.regions) sel.add(new Option(r.name, r.region_cd));
    } catch (e) { App.message(el('msg'), e.message); }
  }
  await fill(el('sel-sido'), 'sido', null, '시도 선택');
  await fill(el('sel-sgg'), 'sgg', null, '시군구 전체');
  await fill(el('sel-umd'), 'umd', null, '읍면동 전체');
  el('sel-sido').onchange = () => { fill(el('sel-sgg'), 'sgg', el('sel-sido').value, '시군구 전체'); fill(el('sel-umd'), 'umd', null, '읍면동 전체'); };
  el('sel-sgg').onchange = () => fill(el('sel-umd'), 'umd', el('sel-sgg').value, '읍면동 전체');

  el('add').onclick = () => {
    const umd = el('sel-umd').value, sgg = el('sel-sgg').value, sido = el('sel-sido').value;
    const key = umd ? `umd:${umd}` : sgg ? `sgg:${sgg}` : sido ? `sido:${sido}` : 'nation:00';
    if (picked.some((p) => p.key === key)) return;
    if (picked.length >= MAX) { App.message(el('msg'), `지역은 최대 ${MAX}개까지 비교할 수 있습니다.`); return; }
    const used = new Set(picked.map((p) => p.slot));
    const slot = [...Array(MAX).keys()].find((i) => !used.has(i));   // 남은 지역의 색은 그대로
    picked.push({ key, name: key, slot });
    render();
  };

  function drawChips() {
    el('chips').innerHTML = picked.map((p) => `<span class="chip" style="--c:${App.seriesColor(p.slot)}">${App.escapeHtml(p.name)}<button data-key="${App.escapeHtml(p.key)}" aria-label="빼기">×</button></span>`).join('');
    el('chips').querySelectorAll('button').forEach((b) => { b.onclick = () => { picked = picked.filter((p) => p.key !== b.dataset.key); render(); }; });
  }

  for (const id of ['ma', 'idx', 'from', 'to']) el(id).onchange = () => render();

  function movingAvg(vals, n) {
    return vals.map((_, i) => {
      if (i < n - 1) return null;
      const w = vals.slice(i - n + 1, i + 1);
      return w.some((v) => v == null) ? null : w.reduce((a, b) => a + b, 0) / n;
    });
  }

  function readControls() {
    state.metric = metricPills.value;
    state.band = bandPills.value;
    state.ma = el('ma').checked ? '1' : '';
    state.idx = el('idx').checked ? '1' : '';
    state.from = App.fromMonthInput(el('from').value) || state.from;
    state.to = App.fromMonthInput(el('to').value) || state.to;
    state.regions = picked.map((p) => p.key).join(',');
  }

  async function render() {
    const my = ++seq;
    readControls();
    App.writeState(state);
    drawChips();
    App.message(el('msg'), '');
    if (!picked.length) { chart.clear(); el('table').innerHTML = ''; return; }
    try {
      last = await App.api('/api/agg', { regions: state.regions, band: state.band, from: state.from, to: state.to });
    } catch (e) { App.message(el('msg'), e.message); chart.clear(); el('table').innerHTML = ''; return; }
    if (my !== seq) return;
    state.from = last.from; state.to = last.to;
    el('from').value = App.toMonthInput(last.from);
    el('to').value = App.toMonthInput(last.to);
    App.writeState(state);
    for (const s of last.series) { const p = picked.find((x) => x.key === s.key); if (p) p.name = s.name; }
    drawChips();
    if (state.metric === 'range' && picked.length > 1) {
      App.message(el('msg'), '25~75% 범위는 지역을 1개만 골랐을 때 볼 수 있습니다.', 'warn');
      chart.clear();
      el('table').innerHTML = '';
      return;
    }
    draw();
  }

  function valuesFor(s, months) {
    const by = Object.fromEntries(s.points.map((pt) => [pt.ym, pt]));
    let vals = months.map((m) => by[m]?.[state.metric] ?? null);
    if (state.ma) vals = movingAvg(vals, 3);
    if (state.idx) {
      const base = vals.find((v) => v != null);
      vals = vals.map((v) => (v == null || !base ? null : (v / base) * 100));
    }
    return { by, vals };
  }

  function draw() {
    const months = App.monthRange(last.from, last.to);
    const labels = months.map(App.fmt.ym);
    const [title, f] = METRICS[state.metric];
    el('chart-title').textContent = state.idx ? `${title} (시작월=100)` : title;
    const yfmt = state.idx ? (v) => v.toFixed(1) : f;
    const base = App.baseOption();
    const series = [];
    let tooltip = { ...base.tooltip, valueFormatter: (v) => (v == null ? '-' : yfmt(v)) };

    if (state.metric === 'range') {
      const s = last.series[0];
      const p = picked.find((x) => x.key === s.key);
      if (p) {
        const color = App.seriesColor(p.slot);
        const by = Object.fromEntries(s.points.map((pt) => [pt.ym, pt]));
        const lo = months.map((m) => by[m]?.p25_price ?? null);
        const hi = months.map((m) => by[m]?.p75_price ?? null);
        series.push(
          { name: '25%', type: 'line', data: lo, stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, silent: true },
          { name: '25~75%', type: 'line', data: hi.map((h, i) => (h == null || lo[i] == null ? null : h - lo[i])), stack: 'band', symbol: 'none', lineStyle: { opacity: 0 }, areaStyle: { color, opacity: 0.18 }, silent: true },
          { name: s.name, type: 'line', data: months.map((m) => by[m]?.median_price ?? null), color, symbol: 'none', lineStyle: { width: 2 } },
        );
        tooltip = { ...base.tooltip, formatter: (ps) => {
          const m = months[ps[0].dataIndex];
          const pt = by[m];
          return pt ? `${App.fmt.ym(m)}<br>75%: ${App.fmt.eok(pt.p75_price)}<br>중위: <b>${App.fmt.eok(pt.median_price)}</b><br>25%: ${App.fmt.eok(pt.p25_price)}<br>${App.fmt.int(pt.n_trades)}건` : `${App.fmt.ym(m)}<br>거래 없음`;
        } };
      }
    } else {
      for (const s of last.series) {
        const p = picked.find((x) => x.key === s.key);
        if (p) series.push({ name: s.name, type: 'line', data: valuesFor(s, months).vals, color: App.seriesColor(p.slot), symbol: 'none', lineStyle: { width: 2 }, emphasis: { focus: 'series' }, connectNulls: false });
      }
    }
    if (series.length) series[series.length - 1].markArea = App.provisionalArea(last.provisional_from, last.to, last.from);

    const showLegend = state.metric !== 'range' && last.series.length > 1;
    chart.setOption({
      ...base,
      legend: { ...base.legend, show: showLegend, type: showLegend ? 'scroll' : 'plain', top: 4 },
      grid: { ...base.grid, top: showLegend ? 56 : base.grid?.top },
      xAxis: { ...base.xAxis, data: labels },
      yAxis: { ...base.yAxis, scale: state.metric !== 'n_trades', name: state.idx ? `${title} (시작월=100)` : title, nameTextStyle: { color: App.css('--muted') }, axisLabel: { color: App.css('--muted'), formatter: yfmt } },
      tooltip,
      series,
    }, true);
    drawTable(months, yfmt);
  }

  function drawTable(months, yfmt) {
    if (state.metric === 'range') {
      const s = last.series[0];
      const by = Object.fromEntries(s.points.map((pt) => [pt.ym, pt]));
      el('table').innerHTML = `<table class="data-table"><thead><tr><th>계약월</th><th class="num">25%</th><th class="num">중위</th><th class="num">75%</th><th class="num">거래</th></tr></thead><tbody>${
        months.map((m) => `<tr><td>${App.fmt.ym(m)}</td><td class="num">${App.fmt.eok(by[m]?.p25_price)}</td><td class="num">${App.fmt.eok(by[m]?.median_price)}</td><td class="num">${App.fmt.eok(by[m]?.p75_price)}</td><td class="num">${App.fmt.int(by[m]?.n_trades)}</td></tr>`).join('')}</tbody></table>`;
      return;
    }
    const cols = last.series.map((s) => valuesFor(s, months).vals);
    el('table').innerHTML = `<table class="data-table"><thead><tr><th>계약월</th>${last.series.map((s) => `<th class="num">${App.escapeHtml(s.name)}</th>`).join('')}</tr></thead><tbody>${
      months.map((m, i) => `<tr><td>${App.fmt.ym(m)}</td>${cols.map((c) => `<td class="num">${c[i] == null ? '-' : yfmt(c[i])}</td>`).join('')}</tr>`).join('')}</tbody></table>`;
  }

  el('toggle-table').onclick = () => {
    el('table').hidden = !el('table').hidden;
    el('toggle-table').textContent = el('table').hidden ? '표 보기' : '표 숨기기';
  };
  el('png').onclick = () => {
    const a = document.createElement('a');
    a.href = chart.getDataURL({ type: 'png', pixelRatio: 2, backgroundColor: App.css('--card') });
    a.download = `trends_${state.from}_${state.to}.png`;
    a.click();
  };

  render();
})();
