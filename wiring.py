"""모듈 사이 후처리 연결. 앱 시작 때 한 번 부른다.

수집 저장 → 단지 등록·집계 대기열 / 단지 지역 변경 → 집계 대기열 /
경계 전환 → 새 버전 집계·이전 버전 삭제 / 지리 처리 끝 → 대기열 집계
"""
from analytics import aggregates
from collector import store
from geo import complexes, hooks, pipeline


def wire():
    store.AFTER_SAVE = [complexes.register, aggregates.on_saved]
    hooks.ON_REGION_CHANGE = [aggregates.on_region_change]
    hooks.BEFORE_ACTIVATE = [aggregates.before_activate]
    hooks.AFTER_ACTIVATE = [aggregates.after_activate]
    pipeline.AFTER_RUN = [aggregates.refresh_dirty]
