"""백그라운드 작업 등록(APScheduler). 계획 2·3에서 좌표·집계 작업을 여기에 더한다."""
from datetime import timedelta

from apscheduler.schedulers.background import BackgroundScheduler

import settings
from collector import jobs
from geo import pipeline

_scheduler = None


def get():
    return _scheduler


def start():
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    s = BackgroundScheduler(timezone="Asia/Seoul")
    s.add_job(jobs.run_batch, "interval", minutes=settings.BATCH_MINUTES,
              kwargs={"max_jobs": settings.BATCH_JOBS}, id="collect", max_instances=1, coalesce=True,
              next_run_time=settings.now_kst())  # 시작 직후 1회
    s.add_job(pipeline.run, "interval", minutes=10, id="geo", max_instances=1, coalesce=True,
              next_run_time=settings.now_kst() + timedelta(minutes=1))
    s.start()
    _scheduler = s
    return s
