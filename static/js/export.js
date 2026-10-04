// 추출 폼: 지역 선택(시도→시군구→읍면동), 기간(월), 형식에 따라 /export.csv 또는 /export.parquet
(async () => {
  const el = (id) => document.getElementById(id);
  const form = el('export-form');
  // 알약 선택값은 같은 이름의 숨은 입력(target, band)으로 폼에 실린다. 형식은 폼 주소로 고른다.
  const targetPills = App.pills(el('target'), (v) => { el('target-v').value = v; });
  App.pills(el('band'), (v) => { el('band-v').value = v; });
  const formatPills = App.pills(el('format'));
  async function fill(sel, level, parent, placeholder) {
    sel.innerHTML = '';
    sel.add(new Option(placeholder, ''));
    if (level !== 'sido' && !parent) return;
    try {
      const data = await App.api('/api/regions', { level, parent });
      for (const r of data.regions) sel.add(new Option(r.name, r.region_cd));
    } catch (e) { App.message(el('msg'), e.message); }
  }
  await fill(el('sel-sido'), 'sido', null, '전국');
  await fill(el('sel-sgg'), 'sgg', null, '시군구 전체');
  await fill(el('sel-umd'), 'umd', null, '읍면동 전체');
  el('sel-sido').onchange = () => { fill(el('sel-sgg'), 'sgg', el('sel-sido').value, '시군구 전체'); fill(el('sel-umd'), 'umd', null, '읍면동 전체'); };
  el('sel-sgg').onchange = () => fill(el('sel-umd'), 'umd', el('sel-sgg').value, '읍면동 전체');

  const now = new Date();
  const thisYm = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}`;
  el('to').value = App.toMonthInput(thisYm);
  el('from').value = App.toMonthInput(App.shiftYm(thisYm, -11));

  form.onsubmit = () => {
    const umd = el('sel-umd').value, sgg = el('sel-sgg').value, sido = el('sel-sido').value;
    el('region').value = umd ? `umd:${umd}` : sgg ? `sgg:${sgg}` : sido ? `sido:${sido}` : '';
    el('region').disabled = !el('region').value;
    el('from-v').value = App.fromMonthInput(el('from').value);
    el('to-v').value = App.fromMonthInput(el('to').value);

    const fromV = el('from-v').value;
    const toV = el('to-v').value;

    // 시작월이 종료월보다 늦으면 막는다
    if (fromV && toV && fromV > toV) {
      App.message(el('msg'), '시작월이 종료월보다 늦습니다.');
      return false;
    }

    // 원본은 60개월까지
    if (targetPills.value === 'raw' && fromV && toV) {
      const monthRange = App.monthRange(fromV, toV);
      if (monthRange.length > 60) {
        App.message(el('msg'), '원본은 한 번에 최대 60개월까지 받을 수 있습니다.');
        return false;
      }
    }

    form.action = formatPills.value === 'parquet' ? '/export.parquet' : '/export.csv';
    App.message(el('msg'), '파일을 만드는 중입니다. 범위가 크면 시간이 걸립니다.', 'muted');
    return true;
  };
})();
