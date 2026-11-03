"""질문 답변 시험 세트 — 내 DB에서 「이 질문이면 이 글들이 근거로 실려야 한다」를 재서, 검색·자료 고르기를 고칠 때 전후를 비교한다.

질문은 내 기록(일정·계정)이 들어가므로 저장소 밖 파일에 둔다. CI에는 넣지 않는다 (내 DB가 있어야 한다).
    python tools/ask_eval.py                       # local/ask_eval.json, AI 없이 (검색어는 파일에 적힌 것)
    python tools/ask_eval.py 질문들.json --answer   # 실제 AI로 검색어 뽑기·답까지 (구독을 쓴다, 질문마다 1~2분)
DB는 임시 복사본으로 연다 (앱이 돌고 있어도 된다).

질문 파일: JSON 목록. 한 항목:
    {"q": "요즘 Codex 사용량 리셋 얘기 뭐 있었어?",
     "keywords": ["Codex", "코덱스", "리셋"], "since": null, "until": null,   # plan_search가 낼 법한 값 (AI 없이 재현)
     "gold": [["Codex", "코덱스"], ["reset", "리셋"]], "days": 30,            # 정답 글: 묶음마다 하나 이상 걸리고 최근 30일
     "expect": ["한국사 시험"]}                                                # (선택) 자료에 꼭 있어야 할 글자
gold 규칙은 글(본문·계정)·RSS(제목·요약·출처)에 대소문자 무시 부분 일치로 적용하고, since/until이 있으면 그 기간 안만.
점수: 정답 글 중 자료 어딘가(검색 결과·오늘 브리핑·72시간 자료)에 링크가 실린 비율. 검색 결과는 글 60·RSS 30개가
상한이라 정답이 많으면 100%가 안 나온다 — 전후 비교용 숫자다. 정답을 검색어 규칙으로 정하므로
같은 뜻 다른 말을 놓치는지는 재지 못한다 — 그건 --answer로 답을 읽고 본다.
"""
import argparse
import json
import shutil
import sqlite3
import statistics
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from popme import briefing, config, search  # noqa: E402
from popme.db import DB  # noqa: E402

def copy_db(tmp):
    """앱이 쓰는 DB를 건드리지 않게 임시 복사본으로."""
    dst = Path(tmp) / "popme.db"
    src = sqlite3.connect(f"file:{config.DB_PATH}?mode=ro", uri=True)
    out = sqlite3.connect(dst)
    src.backup(out)
    src.close()
    out.close()
    return DB(dst)


def gold(db, case):
    """정답 글의 링크 {"posts": set, "items": set}."""
    groups = [[w.lower() for w in g] for g in case.get("gold") or []]
    if not groups:
        return {"posts": set(), "items": set()}
    lo, hi = search._range(case.get("since"), case.get("until"))
    if case.get("days"):
        lo = max(lo, (datetime.now(timezone.utc) - timedelta(days=case["days"])).isoformat())
    ok = lambda text: all(any(w in text for w in g) for g in groups)
    posts = {t.get("link") or f"https://x.com/{t['author']}/status/{t['id']}"
             for t in db.q("SELECT * FROM tweets WHERE created_at >= ? AND created_at < ?", lo, hi)
             if ok(f"{t['text']} {t['author']} {t['author_name']}".lower())}
    items = {i["url"] for i in db.q("SELECT * FROM items WHERE COALESCE(published, collected_at) >= ? "
                                    "AND COALESCE(published, collected_at) < ?", lo, hi)
             if ok(f"{i['title']} {i['summary']} {i['source']}".lower())}
    return {"posts": posts, "items": items}


def post_ages(data):
    """자료의 「## SNS 글」 칸에 실린 글의 나이(일)."""
    today = datetime.now().date()
    ages, on = [], False
    for line in data.splitlines():
        if line.startswith("#"):
            on = line.startswith("## SNS 글")
        elif on and line.startswith("- [") and line[3:13].count("-") == 2:
            ages.append((today - datetime.strptime(line[3:13], "%Y-%m-%d").date()).days)
    return ages


def run_case(cfg, db, case, use_ai):
    t = time.time()
    if use_ai:
        kw, since, until = briefing.plan_search(cfg, case["q"])
    else:
        kw, since, until = case.get("keywords") or [], case.get("since"), case.get("until")
    data, counts = briefing.ask_data(cfg, db, kw, since, until)
    g = gold(db, case)
    score = {k: (sum(1 for link in g[k] if link in data), len(g[k])) for k in g}
    out = {"q": case["q"], "keywords": kw, "counts": counts, "chars": len(data), "score": score,
           "ages": post_ages(data), "missing": [s for s in case.get("expect") or [] if s not in data]}
    if use_ai:
        res = briefing.answer(cfg, db, case["q"])  # 검색어를 한 번 더 뽑는다 (answer가 스스로 뽑으므로)
        links = {link["url"] for p in res["points"] for link in p["links"]}
        out["answer"] = {"found": res["found"], "summary": res["summary"],
                         "gold_links": sum(1 for link in links if link in g["posts"] | g["items"]), "links": len(links)}
    out["sec"] = round(time.time() - t, 1)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cases", nargs="?", default=str(config.ROOT / "local" / "ask_eval.json"))
    ap.add_argument("--answer", action="store_true", help="실제 AI로 검색어 뽑기·답까지")
    args = ap.parse_args()
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    cfg = config.load()
    tmp = tempfile.mkdtemp(prefix="popme-ask-eval-")
    try:
        db = copy_db(tmp)
        results = [run_case(cfg, db, c, args.answer) for c in cases]
        db.conn().close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    recalls, chars, ages = [], [], []
    for r in results:
        got = sum(v[0] for v in r["score"].values())
        can = sum(v[1] for v in r["score"].values())
        if can:
            recalls.append(got / can)
        chars.append(r["chars"])
        ages += r["ages"]
        age = f"글 나이 중앙값 {statistics.median(r['ages']):.0f}일" if r["ages"] else "글 없음"
        print(f"\n== {r['q']}\n   검색어 {r['keywords']} → {r['counts']}")
        print(f"   정답 실림 {got}/{can}" + (f" ({got / can:.0%})" if can else "") + f" · 자료 {r['chars']:,}자 · {age}")
        if r["missing"]:
            print(f"   자료에 없음: {r['missing']}")
        if "answer" in r:
            a = r["answer"]
            print(f"   답 ({r['sec']}초): {'찾음' if a['found'] else '못 찾음'} · 링크 {a['links']}개 중 정답 {a['gold_links']}개 — {a['summary']}")
    print(f"\n합계 {len(results)}문항 · 정답 실림 평균 {statistics.mean(recalls):.0%} (정답 있는 {len(recalls)}문항)"
          f" · 자료 중앙값 {statistics.median(chars):,.0f}자 · 실린 글 나이 중앙값 "
          f"{statistics.median(ages) if ages else 0:.0f}일 · 「자료에 없음」 {sum(1 for r in results if r['missing'])}문항")


if __name__ == "__main__":
    main()
