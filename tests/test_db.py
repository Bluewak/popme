"""DB 파일 방식 (popme/db.py)."""
import sqlite3

from popme.db import DB


def test_wal_lets_reads_go_on_while_writing(tmp_path):
    """WAL: 수집이 쓰는 중(트랜잭션 열림)에도 읽기 전용 연결(measure.py·평가 도구)이 기다리지 않고 읽는다."""
    db = DB(tmp_path / "t.db")
    assert db.conn().execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    db.upsert_tweets([{"id": "1", "author": "a", "text": "x", "created_at": "2026-11-01T00:00:00+00:00",
                       "likes": 1, "via": "search:0", "kind": "post"}])
    w = db.conn()
    w.execute("BEGIN IMMEDIATE")
    w.execute("UPDATE tweets SET likes = 2")
    r = sqlite3.connect(f"file:{tmp_path / 't.db'}?mode=ro", uri=True, timeout=0)
    assert r.execute("SELECT likes FROM tweets").fetchone() == (1,)  # 아직 저장 전 값
    w.commit()
    assert r.execute("SELECT likes FROM tweets").fetchone() == (2,)
    r.close()
