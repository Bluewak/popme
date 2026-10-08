import sqlite3
import threading
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS tweets (
    id TEXT PRIMARY KEY,
    author TEXT, author_name TEXT, text TEXT, created_at TEXT, lang TEXT,
    likes INTEGER, retweets INTEGER, replies INTEGER, quotes INTEGER, views INTEGER,
    urls TEXT, kind TEXT, ref_author TEXT, ref_id TEXT,
    via TEXT, collected_at TEXT
);
CREATE INDEX IF NOT EXISTS tweets_author ON tweets(author COLLATE NOCASE, created_at);

CREATE TABLE IF NOT EXISTS items (
    url TEXT PRIMARY KEY,
    source TEXT, grp TEXT, title TEXT, summary TEXT, published TEXT, collected_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id TEXT PRIMARY KEY,
    calendar_id TEXT, title TEXT, start_at TEXT, end_at TEXT, all_day INTEGER,
    updated_at TEXT, deleted INTEGER DEFAULT 0,
    first_seen TEXT, change_kind TEXT, changed_at TEXT
);

CREATE TABLE IF NOT EXISTS briefings (
    date TEXT PRIMARY KEY, markdown TEXT, created_at TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
    handle TEXT PRIMARY KEY COLLATE NOCASE,
    name TEXT, reason TEXT, score REAL, status TEXT DEFAULT 'new', updated_at TEXT
);

-- 일정에서 뽑은 장소 (Claude, 제목이 바뀌면 다시 뽑음) / 지명 → 좌표 캐시 (Nominatim)
CREATE TABLE IF NOT EXISTS event_places (id TEXT PRIMARY KEY, title TEXT, place TEXT, query TEXT);
CREATE TABLE IF NOT EXISTS geocache (query TEXT PRIMARY KEY, lat REAL, lon REAL);

-- 같은 글이 여러 검색어·태그에서 보였는지 (발굴 점수의 "반복 등장")
CREATE TABLE IF NOT EXISTS sightings (
    id TEXT, via TEXT, seen_at TEXT, PRIMARY KEY (id, via)
);
"""

# 기존 DB에 나중에 생긴 컬럼 추가
MIGRATIONS = [
    "ALTER TABLE tweets ADD COLUMN platform TEXT DEFAULT 'x'",
    "ALTER TABLE tweets ADD COLUMN link TEXT",
    "ALTER TABLE candidates ADD COLUMN platform TEXT DEFAULT 'x'",
    "ALTER TABLE events ADD COLUMN recurrences TEXT DEFAULT '[]'",
    "ALTER TABLE events ADD COLUMN location TEXT",
    "ALTER TABLE briefings ADD COLUMN items TEXT",  # 카드 형식 브리핑 JSON (예전 글 형식은 NULL)
    # 초기 버전은 공휴일만 잘못 저장했으므로 정리 (holiday: 접두사 없는 공휴일 행)
    "DELETE FROM events WHERE id NOT LIKE 'holiday:%' AND calendar_id = ''",
]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class DB:
    def __init__(self, path):
        self.path = path
        self._local = threading.local()
        self.conn().executescript(SCHEMA)
        for m in MIGRATIONS:
            try:
                self.conn().execute(m)
            except sqlite3.OperationalError:  # 이미 있음
                pass
        self.conn().commit()  # DELETE가 연 트랜잭션을 닫아야 다른 스레드·프로세스가 쓸 수 있다

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, "c", None)
        if c is None:
            c = sqlite3.connect(self.path, timeout=30)
            c.row_factory = sqlite3.Row
            self._local.c = c
        return c

    def q(self, sql, *args):
        return [dict(r) for r in self.conn().execute(sql, args).fetchall()]

    # --- X ---
    def upsert_tweets(self, tweets):
        cols = ["id", "author", "author_name", "text", "created_at", "lang", "likes", "retweets",
                "replies", "quotes", "views", "urls", "kind", "ref_author", "ref_id", "via", "platform", "link"]
        with self.conn() as c:
            for t in tweets:
                t.setdefault("platform", "x")
                t.setdefault("link", f"https://x.com/{t['author']}/status/{t['id']}")
                c.execute("INSERT OR IGNORE INTO sightings VALUES (?,?,?)", (t["id"], t["via"], now_iso()))
                # via는 처음 본 경로를 유지하고, 반응 수치만 최신으로 갱신
                c.execute(
                    f"INSERT INTO tweets ({','.join(cols)}, collected_at) VALUES ({','.join('?' * len(cols))}, ?) "
                    "ON CONFLICT(id) DO UPDATE SET likes=excluded.likes, retweets=excluded.retweets, "
                    "replies=excluded.replies, quotes=excluded.quotes, views=excluded.views, text=excluded.text",
                    [t.get(k) for k in cols] + [now_iso()],
                )

    # --- RSS ---
    def upsert_items(self, items):
        with self.conn() as c:
            for it in items:
                c.execute(
                    "INSERT INTO items (url, source, grp, title, summary, published, collected_at) "
                    "VALUES (?,?,?,?,?,?,?) ON CONFLICT(url) DO NOTHING",
                    (it["url"], it["source"], it["grp"], it["title"], it["summary"], it["published"], now_iso()),
                )

    # --- TimeTree ---
    def upsert_events(self, events, synced_calendars=()):
        """새로 생김/바뀜/지워짐을 표시한다. 캘린더를 처음 동기화할 때는 표시하지 않는다.
        synced_calendars: 전체 동기화한 캘린더 — 거기서 사라진 일정은 지워짐으로 본다."""
        ts = now_iso()
        known = {r["calendar_id"] for r in self.q("SELECT DISTINCT calendar_id FROM events")}
        cols = ("calendar_id", "title", "start_at", "end_at", "all_day", "updated_at", "deleted", "recurrences",
                "location")
        with self.conn() as c:
            for e in events:
                first_sync = e["calendar_id"] not in known
                old = c.execute("SELECT updated_at, deleted, location FROM events WHERE id=?", (e["id"],)).fetchone()
                if old is not None and old["location"] != e.get("location"):  # 장소 칸은 나중에 추가된 컬럼이라 조용히 채움
                    c.execute("UPDATE events SET location=? WHERE id=?", (e.get("location"), e["id"]))
                if old is None:
                    kind = None if first_sync else ("deleted" if e["deleted"] else "new")
                    c.execute(
                        f"INSERT INTO events (id, {','.join(cols)}, first_seen, change_kind, changed_at) "
                        f"VALUES (?,{','.join('?' * len(cols))},?,?,?)",
                        (e["id"], *[e[k] for k in cols], ts, kind, ts if kind else None),
                    )
                elif old["updated_at"] != e["updated_at"] or old["deleted"] != e["deleted"]:
                    kind = "deleted" if e["deleted"] else "changed"
                    c.execute(
                        f"UPDATE events SET {','.join(k + '=?' for k in cols)}, change_kind=?, changed_at=? WHERE id=?",
                        (*[e[k] for k in cols], kind, ts, e["id"]),
                    )
            seen = {e["id"] for e in events}
            for cal in synced_calendars:
                for r in c.execute("SELECT id FROM events WHERE calendar_id=? AND deleted=0", (cal,)).fetchall():
                    if r["id"] not in seen:
                        c.execute("UPDATE events SET deleted=1, change_kind='deleted', changed_at=? WHERE id=?",
                                  (ts, r["id"]))

    # --- 브리핑 ---
    def save_briefing(self, date, markdown, items=None):
        with self.conn() as c:
            c.execute("INSERT OR REPLACE INTO briefings (date, markdown, created_at, items) VALUES (?,?,?,?)",
                      (date, markdown, now_iso(), items))

    def latest_briefing(self):
        rows = self.q("SELECT * FROM briefings ORDER BY date DESC LIMIT 1")
        return rows[0] if rows else None

    def briefing_for(self, date):
        rows = self.q("SELECT * FROM briefings WHERE date=?", date)
        return rows[0] if rows else None

    # --- 발굴 후보 ---
    def upsert_candidate(self, handle, name, reason, score, platform="x"):
        """Threads 후보는 handle을 'threads/username'으로 저장해 X 계정과 겹치지 않게 한다."""
        with self.conn() as c:
            c.execute(
                "INSERT INTO candidates (handle, name, reason, score, updated_at, platform) VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(handle) DO UPDATE SET name=excluded.name, reason=excluded.reason, "
                "score=excluded.score, updated_at=excluded.updated_at",
                (handle, name, reason, score, now_iso(), platform),
            )

    def set_candidate_status(self, handle, status):
        with self.conn() as c:
            c.execute("UPDATE candidates SET status=? WHERE handle=?", (status, handle))
