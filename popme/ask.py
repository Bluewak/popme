"""질문 답변: 질문 → 검색어·기간 → 전체 기록에서 찾기 → 그 자료로 답 (예전 briefing.py에서 옮김).
재료(build_data)·일정(events_block)은 briefing 모듈 이름으로 부른다 — 테스트가 briefing.build_data를 바꿔 끼운다."""
import logging
import re
from datetime import datetime, timedelta, timezone

from popme import briefing, clock, llm, search

log = logging.getLogger(__name__)

# 질문 답변은 사용자에게 직접 하는 말이라 높임말 (브리핑 카드는 반말 메모체 그대로)
QA_SYSTEM = briefing.SYSTEM.replace("한국어 반말로", "한국어 높임말(해요체)로")

QA_RULES = """아래 데이터와 가장 최근 브리핑만 근거로 질문에 답하라.
"# 질문 관련 기록 검색"은 수집해 둔 전체 기간에서 이 질문의 검색어로 찾은 기록이고, 그 아래는 최근 72시간 자료다
(검색에 걸린 게 있으면 그중 검색어가 든 줄만).
출력은 JSON 하나만. 코드블록·설명 없이.
{"found": true, "summary": "...", "points": [{"date": "2026-09-08", "text": "...", "status": "확인됨",
  "links": [{"label": "원문", "url": "https://..."}]}]}
- summary: 질문에 대한 답을 한두 문장으로. 평문(마크다운 금지), 높임말(해요체, "~했어요").
- points: 답의 근거가 되는 핵심 2~4개, 중요한 순. 장황하게 늘어놓지 말 것.
  중요한 것 = 반응(♥)이 큰 글, 당사자 공식 발표, 여러 글이 같이 다룬 소식. 가장 최근이라는 이유만으로 고르지 말 것
  (♥는 X가 Threads보다 훨씬 크게 나오니 같은 곳끼리 비교).
  text는 "누가/무엇이 + 어떻게 했어요" 한 문장(60자 이내), 높임말. date는 그 글·소식의 날짜(YYYY-MM-DD).
  status는 공식 출처(RSS 공식 변경내역·공식 계정)로 확인되면 "확인됨", 사람 말뿐이면 "미확인".
  links는 1~2개, URL은 데이터에 있는 것을 글자 그대로만.
- 근거가 데이터에 없으면 {"found": false, "summary": "수집한 자료에서는 확인하지 못했어요", "points": []}.

질문: __QUESTION__"""

SEARCH_SYSTEM = "너는 검색어를 뽑는 도우미다. 요청한 JSON 하나만 출력한다."
SEARCH_PLAN = """오늘은 {today}(한국 시간)다. 아래 질문에 답하려고, 수집해 둔 SNS 글·RSS·지난 브리핑·일정 기록에서 찾을 말을 정한다.
출력은 JSON 하나만: {{"keywords": ["..."], "since": "YYYY-MM-DD" 또는 null, "until": "YYYY-MM-DD" 또는 null}}
- keywords: 글 본문·제목에 그대로 나올 검색어 1~8개. 한국어·영어 표기를 같이 쓴다 (예: "클로드코드", "Claude Code").
  사람이면 handle이나 이름. 조사·어미·의문사는 빼고 짧게. 특정 주제 없이 최근 소식 전반을 묻는 질문이면 [].
- since/until: 질문에 기간이 있으면 (지난달, 9월, 지난주, 어제 등) 그 범위. 없으면 둘 다 null.

질문: {question}"""


def plan_search(cfg, question):
    """질문 → (검색어, 시작일, 끝일). Claude가 못 뽑으면 단어에서 조사를 떼어 쓴다."""
    try:
        p = llm.parse_json(llm.ask(cfg, SEARCH_SYSTEM, SEARCH_PLAN.format(
            today=clock.now().strftime("%Y-%m-%d (%a)"), question=question), timeout=120))
        kw = [str(k).strip()[:40] for k in (p.get("keywords") or []) if str(k).strip()][:8]
        day = lambda v: v if isinstance(v, str) and briefing.DATE_RE.match(v) else None
        return kw, day(p.get("since")), day(p.get("until"))
    except Exception:
        log.exception("질문 검색어 뽑기 실패 (단어로 대신 찾음)")
        return search.fallback_keywords(question), None, None


def clean_answer(raw, data):
    """모델 답 검사·정리: 칸 채우기, 핵심 4개까지, 데이터에 없는 링크 버리기 (브리핑 카드와 같은 원칙)."""
    known = {u.rstrip("/.,)") for u in re.findall(r"https?://[^\s|<>\"]+", data)}
    points = []
    for p in (raw.get("points") or [])[:4]:
        if not isinstance(p, dict) or not str(p.get("text") or "").strip():
            continue
        links = [{"label": briefing._short(l.get("label") or "원문", 14), "url": str(l.get("url", "")).rstrip("/.,)")}
                 for l in (p.get("links") or []) if isinstance(l, dict)
                 and str(l.get("url", "")).rstrip("/.,)") in known][:2]
        points.append({"date": p["date"] if isinstance(p.get("date"), str) and briefing.DATE_RE.match(p["date"]) else "",
                       "text": briefing._short(p["text"], 120), "status": "확인됨" if p.get("status") == "확인됨" else "미확인",
                       "links": links})
    summary = " ".join(str(raw.get("summary") or "").split()) or "수집한 자료에서는 확인하지 못했어요"
    return {"found": bool(raw.get("found", True)) and bool(points or raw.get("summary")),
            "summary": summary, "points": points}


def ask_data(cfg, db, kw, since=None, until=None):
    """질문 답변에 넘길 자료 → (데이터 블록, 찾은 개수). 가장 최근 브리핑에, 검색에 걸린 게 있으면 그 기록·일정과
    최근 72시간 자료 중 검색어가 든 줄만, 없으면 (최근 소식 전반을 묻는 질문 등) 72시간 자료를 통째로.
    예전엔 둘 다 통째로 넣어 질문마다 26만 자였다 (2026-11). 시험 세트(tools/ask_eval.py) 18문항에서
    자료는 76% 줄고 근거로 실린 정답 글은 903 → 918개. 72시간 자료를 아예 빼면 846개로 줄었다."""
    found, counts = search.find(db, kw, since, until)
    recent = briefing.build_data(cfg, db, (datetime.now(timezone.utc) - timedelta(hours=72)).isoformat(), with_events=not found)
    if found:
        _, ev = briefing.events_block(db, cfg)  # 일정은 검색어와 상관없이 통째로 (짧다)
        hits = search.lines_with(recent, kw)
        data = f"{found}\n\n# 일정 (TimeTree)\n{ev}" + (f"\n\n# 최근 72시간 자료 중 검색어가 든 것\n{hits}" if hits else "")
    else:
        data = recent
    b = db.latest_briefing()
    if b:  # 아침 수집 전·브리핑이 실패한 날엔 어제 이전 것이다 — 오늘 소식처럼 답하지 않게 날짜를 붙인다
        stale = "" if b["date"] == clock.now().strftime("%Y-%m-%d") else "(오늘 브리핑은 아직 없다)\n"
        data = f"# 가장 최근 브리핑 ({b['date']})\n{stale}{b['markdown']}\n\n{data}"
    return data, counts


def answer(cfg, db, question, progress=lambda stage, info=None: None):
    """질문 → 검색어·기간을 뽑아 전체 기록에서 찾고 (ask_data), 그 자료로 답한다.
    progress(stage, info): "plan" → "search"({keywords, since, until}) → "write"({counts}) 순으로 알린다.
    반환: {"found", "summary", "points": [{"date", "text", "status", "links"}], "search": {...}}"""
    progress("plan")
    kw, lo, hi = plan_search(cfg, question)
    progress("search", {"keywords": kw, "since": lo, "until": hi})
    data, counts = ask_data(cfg, db, kw, lo, hi)
    log.info("질문 검색: %s (%s~%s) → %s", kw, lo, hi, counts)
    progress("write", {"counts": counts})
    prompt = QA_RULES.replace("__QUESTION__", question) + f"\n<data>\n{data}\n</data>"
    text = llm.ask(cfg, QA_SYSTEM, prompt, timeout=300)
    try:
        raw = llm.parse_json(text)
    except ValueError:  # 형식이 깨졌으면 한 번만 다시
        log.warning("답변 JSON 파싱 실패, 다시 요청")
        raw = llm.parse_json(llm.ask(cfg, QA_SYSTEM, prompt + "\n\n(주의: 반드시 JSON 하나만 출력)", timeout=300))
    return {**clean_answer(raw, data), "search": {"keywords": kw, "since": lo, "until": hi, "counts": counts}}
