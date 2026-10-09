"""계정 규모(팔로워 등) 기록: 수집 응답에 실려 오는 값을 꺼내 DB에 남긴다."""
from popme.collectors import x
from popme.db import DB

NEW_USER = {"__typename": "User", "core": {"screen_name": "LangChain", "name": "LangChain"},
            "relationship_counts": {"followers": 269058, "following": 200}, "tweet_counts": {"tweets": 11879},
            "verification": {"verified_type": "Business"}}
OLD_USER = {"__typename": "User", "legacy": {"screen_name": "old", "name": "Old", "followers_count": 10,
                                             "friends_count": 5, "statuses_count": 7}, "is_blue_verified": True}


def test_parse_user_new_and_old_shapes():
    assert x.parse_user(NEW_USER) == {"platform": "x", "handle": "LangChain", "name": "LangChain",
                                      "followers": 269058, "following": 200, "posts": 11879, "verified": "Business",
                                      "org": None, "lang": None}
    assert x.parse_user(OLD_USER)["followers"] == 10 and x.parse_user(OLD_USER)["verified"] == "blue"
    assert x.parse_user({"__typename": "User", "core": {"screen_name": "nofollowers"}}) is None


def test_walk_collects_tweet_authors():
    tweet = {"__typename": "Tweet", "rest_id": "1", "core": {"user_results": {"result": NEW_USER}},
             "legacy": {"full_text": "t", "created_at": "Thu Oct 08 12:00:00 +0000 2026"}}
    tweets, users = [], {}
    x.walk({"data": [tweet]}, tweets, users)
    assert len(tweets) == 1 and users["langchain"]["followers"] == 269058


def test_upsert_profiles_updates_counts(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_profiles([x.parse_user(NEW_USER)])
    db.upsert_profiles([{**x.parse_user(NEW_USER), "followers": 270000, "following": None}])
    row = db.q("SELECT * FROM profiles WHERE handle='langchain'")[0]  # handle은 대소문자 무시
    assert row["followers"] == 270000 and row["following"] == 200  # 모르는 값(None)은 덮어쓰지 않음


def test_threads_walk_collects_follower_count():
    from popme.collectors import threads
    page_json = {"user": {"username": "withbeno", "full_name": "베노", "follower_count": 11404, "is_verified": False},
                 "posts": [{"code": "c1", "user": {"username": "withbeno"}, "caption": {"text": "t"}, "taken_at": 1}]}
    posts, users = [], {}
    threads.walk(page_json, posts, users)
    assert len(posts) == 1
    assert users == {"withbeno": {"platform": "threads", "handle": "withbeno", "name": "베노", "followers": 11404,
                                  "following": None, "posts": None, "verified": "", "org": None, "lang": None}}


def test_parse_user_reads_affiliate_org():
    staff = {**NEW_USER, "core": {"screen_name": "sydneyrunkle", "name": "Sydney"}, "verification": {},
             "profile_description_language": "en",
             "affiliates_highlighted_label": {"label": {"description": "LangChain",
                                                        "url": {"url": "https://twitter.com/LangChain"}}}}
    u = x.parse_user(staff)
    assert u["org"] == "LangChain" and u["lang"] == "en"


def test_snapshots_one_per_day(tmp_path):
    db = DB(tmp_path / "t.db")
    db.upsert_profiles([x.parse_user(NEW_USER)])
    db.upsert_profiles([{**x.parse_user(NEW_USER), "followers": 270000}])
    assert db.q("SELECT followers FROM profile_snapshots") == [{"followers": 270000}]
