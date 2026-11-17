import logging
import sys
import threading

from popme import config
from popme.app import main


def _log_uncaught():
    """pythonw엔 콘솔이 없어 잡히지 않은 예외(시작 실패, 수집·설정 스레드 안의 예외)가 어디에도 안 남았다 — 로그로."""
    log = logging.getLogger("popme")
    sys.excepthook = lambda t, v, tb: log.critical("잡히지 않은 예외", exc_info=(t, v, tb))
    threading.excepthook = lambda a: log.error("스레드 %s의 잡히지 않은 예외", a.thread.name if a.thread else "?",
                                               exc_info=(a.exc_type, a.exc_value, a.exc_traceback))


if __name__ == "__main__":
    _log_uncaught()
    config.adopt_old_files()  # git으로 받은 폴더처럼 install.bat을 거치지 않고 새 버전이 된 경우
    config.prepare()  # 평문 비밀번호 숨기기·예전 형식 옮기기 (켤 때 한 번)
    if len(sys.argv) > 1 and sys.argv[1] == "run-once":
        # 위젯 없이 수집+브리핑 1회 (디버깅용)
        from popme.jobs import Jobs
        jobs = Jobs(notify=lambda kind, msg: print(kind, msg))
        jobs.run_full(force=True, blocking=True)
        print(jobs.db.latest_briefing()["markdown"] if jobs.db.latest_briefing() else jobs.state)
    else:
        main(restart=sys.argv[1:2] == ["restart"])  # restart: 설정 저장 후 앞 프로세스가 꺼지길 기다렸다 켬
