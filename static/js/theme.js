// 화면 테마 전환: 시스템(기본) / 밝게 / 어둡게. 선택은 브라우저에 저장하고 <html data-theme>으로 적용한다.
// 처음 적용은 base.html head의 스크립트가 맡고(깜빡임 방지), 여기서는 버튼과 시스템 테마 변경을 처리한다.
// 차트는 그릴 때 색 토큰을 읽으므로, 차트가 있는 화면은 테마가 바뀌면 새로고침한다(조건은 주소에 남아 있다).
(() => {
  const root = document.documentElement;
  const media = matchMedia('(prefers-color-scheme: dark)');
  const group = document.getElementById('theme');

  function read() {
    let v = null;
    try { v = localStorage.getItem('theme'); } catch (e) { /* 저장소 사용 불가 */ }
    return v === 'light' || v === 'dark' ? v : 'system';
  }
  function save(pref) {
    try {
      if (pref === 'system') localStorage.removeItem('theme'); else localStorage.setItem('theme', pref);
    } catch (e) { /* 저장이 막힌 환경: 이번 화면에만 적용 */ }
  }
  function mark(pref) {
    if (!group) return;
    group.querySelectorAll('.pill').forEach((b) => b.setAttribute('aria-pressed', String(b.dataset.value === pref)));
  }
  function apply(pref) {
    const theme = pref === 'dark' || (pref === 'system' && media.matches) ? 'dark' : 'light';
    const changed = root.getAttribute('data-theme') !== theme;
    root.setAttribute('data-theme', theme);
    if (changed && window.echarts) location.reload();
  }

  let pref = read();
  mark(pref);
  if (group) {
    group.addEventListener('click', (ev) => {
      const b = ev.target.closest('.pill');
      if (!b || !group.contains(b) || b.dataset.value === pref) return;
      pref = b.dataset.value;
      save(pref);
      mark(pref);
      apply(pref);
    });
  }
  media.addEventListener('change', () => { if (pref === 'system') apply('system'); });
})();
