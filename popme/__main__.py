import sys

from popme.app import main

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "run-once":
        # 위젯 없이 수집+브리핑 1회 (디버깅용)
        from popme.jobs import Jobs
        jobs = Jobs(notify=lambda kind, msg: print(kind, msg))
        jobs.run_full(force=True, blocking=True)
        print(jobs.db.latest_briefing()["markdown"] if jobs.db.latest_briefing() else jobs.state)
    else:
        main()
