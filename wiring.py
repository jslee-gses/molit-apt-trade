"""모듈 사이 후처리 연결(수집 저장 → 단지 등록 → …). 앱 시작 때 한 번 부른다."""
from collector import store
from geo import complexes


def wire():
    store.AFTER_SAVE = [complexes.register]
