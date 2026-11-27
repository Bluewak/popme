"""설정 창: 준비물 → 브리핑 AI → 캘린더 → (메일) → 로그인 → 관심사 → 캐릭터 → 날씨·시각을 차례로 고르면 config.toml에 써 준다.

install.ps1이 설치할 때 띄우고, 나중엔 트레이(또는 캐릭터 우클릭) > "설정"로 다시 연다.
config.toml의 주석·다른 설정은 그대로 두고 고른 값의 줄만 바꾼다 (파일이 없으면 config.example.toml에서 시작).

연결 방법은 HELP(단계별 안내 + 자주 막히는 질문)에 데이터로 둔다. 화면 안내도, 「AI한테 해결 요청하기」의 답도 이것만 쓴다
(AI는 이 안내 범위 밖이면 답하지 않는다 — ask_help).
  python -m popme.setup_window check-ics '<주소>'          주소가 열리는지, 일정 몇 개인지
  python -m popme.setup_window check-caldav '<Apple 계정 이메일>'   앱 암호를 물어 iCloud 캘린더·일정 수만 보여 줌 (저장 안 함)
"""
import base64
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

from popme import config, tomledit, vault
from popme.tomledit import base_text, set_value  # noqa: F401 (테스트·예전 이름 호환)
from popme.collectors import caldav
from popme.setup_data import AIS, SOURCES, MAIL_DOMAINS, MX_HOSTS, MS_NOTE, REFRESH_PATH, AI_SETUP, CALENDAR_APPS, HELP, HELP_SYSTEM, LOGIN_SITES, CLIP  # noqa: F401 (예전 이름)
from popme.winclip import _clipboard, take_clipboard  # noqa: F401 (예전 이름)

log = logging.getLogger(__name__)
UI = config.ROOT / "popme" / "ui" / "setup.html"


# ---------------------------------------------------------------- 현재 값 읽기 · 고른 값 쓰기

def characters():
    """기본 캐릭터와 내가 만든 캐릭터 파일들 [{file, name, intro, image}] ([persona]가 있는 *.toml). 이름이 같으면 내 것."""
    out = []
    files = {p.name: p for folder in (config.DEFAULTS_DIR, config.DATA_DIR) for p in folder.glob("*.toml")}
    for _, p in sorted(files.items()):
        try:
            persona = tomllib.loads(p.read_text(encoding="utf-8")).get("persona")
        except (tomllib.TOMLDecodeError, OSError):
            continue
        if persona is not None:
            image = config.image_dir(persona.get("images", "")) / "pet.png"
            out.append({"file": p.name, "name": persona.get("name") or p.stem, "intro": persona.get("intro", ""),
                        "image": image_uri(image) if image.exists() else ""})
    return out


def image_uri(path):
    """설정 창이 앱 폴더 밖의 이미지도 보이게 data URI로."""
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def saved_password(section, cfg):
    """저장해 둔 비밀번호: 자격 증명 관리자, 못 옮겼으면 config.toml의 평문."""
    return cfg.get(section, {}).get("password") or vault.get(section)


def charmaker_tones():
    from popme import charmaker
    return charmaker.TONES


def mail_supported():
    """메일 알림 기능(popme/mail.py)이 들어 있는 버전에서만 메일 단계를 보인다."""
    return (config.ROOT / "popme" / "mail.py").exists()


def current():
    cfg = tomllib.loads(base_text())
    cal, w, llm_, mail = cfg.get("calendar", {}), cfg.get("weather", {}), cfg.get("llm", {}), cfg.get("mail", {})
    dav = cfg.get("caldav", {})
    chars = characters()
    char = cfg.get("character", {}).get("file", "character.toml")
    if char not in {c["file"] for c in chars}:
        char = "character.example.toml"
    return {
        "exists": config.CONFIG_PATH.exists(),
        "provider": llm_.get("provider", "claude"),
        "plan": cfg.get("user", {}).get("plan", ""),
        "tools": cfg.get("user", {}).get("tools", ""),
        "briefing_after": cfg.get("schedule", {}).get("briefing_after", "09:00"),
        "source": cal.get("source", "timetree"),
        "ics": [{"name": c.get("name", ""), "url": c.get("url", "")} for c in cal.get("ics", [])],
        "holidays": cal.get("holidays", True),
        "caldav": {"user": dav.get("user", ""), "has_password": bool(saved_password("caldav", cfg)),
                   "calendars": [{"name": c.get("name", ""), "url": c.get("url", "")} for c in dav.get("calendars", [])]},
        "use_windows_location": w.get("use_windows_location", True),
        "place": w.get("name", ""),
        "mail": {"supported": mail_supported(), "enabled": mail.get("enabled", False),
                 "host": mail.get("host", "imap.gmail.com"), "user": mail.get("user", ""),
                 "has_password": bool(saved_password("mail", cfg)), "quiet_hours": mail.get("quiet_hours", "")},
        "mail_domains": MAIL_DOMAINS,
        "character": char,
        "characters": chars,
        "tones": {k: v[0] for k, v in charmaker_tones().items()},
        "ais": AIS,
        "calendar_apps": CALENDAR_APPS,
        "help": HELP,
        "login_sites": {k: {"name": v["name"], "why": v["why"], "optional": v.get("optional", False)}
                        for k, v in LOGIN_SITES.items()},
        "python": sys.version.split()[0],
        # 「생각 중」 화면의 캐릭터 그림 (개인 이미지가 있을 때만 — 없으면 화면이 기본 방울 모양을 그린다)
        "pet_image": next((image_uri(p) for n in ("pet_busy.png", "pet.png") if (p := config.ASSETS_DIR / n).exists()), ""),
        **interest_choices(cfg),
    }


def interest_choices(cfg):
    """관심사 단계 ①의 카드: 미리 만든 것 + 내가 만든 것, 지금 고른 것."""
    from popme import catalog
    feeds = catalog.feeds()
    out = []
    for p in catalog.profiles():
        on = [e["id"] for e in p.get("feeds", []) if e.get("default", True) is not False and e["id"] in feeds]
        n_ent = len(p.get("people", [])) + len(p.get("companies", []))
        out.append({"id": p["id"], "name": p["name"], "tag": p["tag"], "summary": p.get("summary", ""),
                    "custom": bool(p.get("custom")), "count": len(on) or n_ent,
                    "examples": [feeds[i]["name"] for i in on[:3]] or [e["name"] for e in p.get("people", [])[:3]]})
    return {"interests": out, "interests_on": [p["id"] for p in catalog.chosen(cfg)], "taxonomy": catalog.taxonomy()}


def _check_url(url):
    if not re.match(r"^(https?|webcal)://\S+$", url.strip()):
        raise ValueError(f"주소 모양이 이상해요: {url[:60]} (https:// 또는 webcal://로 시작해야 해요)")
    return url.strip()


def validate(ch):
    """화면에서 받은 값 → 쓸 값. 잘못되면 ValueError(사용자에게 보일 문장)."""
    if ch.get("provider") not in AIS:
        raise ValueError("브리핑 쓸 AI를 골라 주세요.")
    t = (ch.get("briefing_after") or "").strip()
    if not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", t):
        raise ValueError("브리핑 시각은 09:00처럼 넣어 주세요.")
    if ch.get("source") not in SOURCES:
        raise ValueError("캘린더를 골라 주세요.")
    rows = []
    for r in ch.get("ics") or []:
        url = (r.get("url") or "").strip()
        if url:
            rows.append({"name": (r.get("name") or "").strip() or f"캘린더 {len(rows) + 1}", "url": _check_url(url)})
    if ch["source"] == "ics" and not rows:
        raise ValueError("캘린더 주소를 하나 이상 넣어 주세요. 나중에 넣으려면 「안 씀」으로 두고 설정을 다시 열면 돼요.")
    dav = None
    if ch["source"] == "caldav":
        d = ch.get("caldav") or {}
        dav = {"user": (d.get("user") or "").strip(), "password": (d.get("password") or "").strip(),
               "calendars": [{"name": (c.get("name") or "").strip() or "캘린더", "url": c["url"].strip()}
                             for c in d.get("calendars") or [] if re.match(r"^https?://\S+$", (c.get("url") or "").strip())]}
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", dav["user"]):
            raise ValueError("Apple 계정 이메일을 확인해 주세요.")
        if not dav["password"] and not current()["caldav"]["has_password"]:
            raise ValueError("앱 암호를 넣어 주세요.")
        if not dav["calendars"]:
            raise ValueError("「연결 확인」을 눌러 읽을 캘린더를 하나 이상 골라 주세요.")
    mail = ch.get("mail")
    if mail and mail_supported():
        mail = {**mail, "enabled": bool(mail.get("enabled")), "user": (mail.get("user") or "").strip(),
                "password": (mail.get("password") or "").replace(" ", ""),  # 앱 비밀번호는 띄어 써서 보여줌
                "quiet_hours": (mail.get("quiet_hours") or "").strip()}
        mail["host"] = (mail.get("host") or "").strip().lower()
        if mail["enabled"] and not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", mail["host"]):
            raise ValueError("메일 서버 주소를 확인해 주세요 (예: imap.gmail.com).")
        if mail["enabled"] and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", mail["user"]):
            raise ValueError("메일 주소를 확인해 주세요.")
        saved = current()["mail"]
        if mail["enabled"] and not mail["password"]:
            if not saved["has_password"]:
                raise ValueError("메일 앱 비밀번호를 넣어 주세요.")
            if saved["user"].lower() != mail["user"].lower() or saved["host"] != mail["host"]:
                raise ValueError("메일 주소가 바뀌었어요. 새 계정의 비밀번호를 넣어 주세요.")
        hm = r"([01]\d|2[0-3]):[0-5]\d"
        if mail["quiet_hours"] and not re.fullmatch(f"{hm}-{hm}", mail["quiet_hours"]):
            raise ValueError("방해 금지 시간은 23:00-07:00처럼 넣어 주세요.")
    else:
        mail = None
    char = ch.get("character")
    if char == "character.example.toml" and not (config.DATA_DIR / "character.toml").exists():
        # 기본 캐릭터는 이름을 박지 않는다: character.toml을 나중에 만들면 그대로 쓰이고, 없으면 예시로 넘어간다
        char = "character.toml"
    if ch.get("new_character"):
        from popme import charmaker
        ch = {**ch, "new_character": charmaker.clean(ch["new_character"])}
    return {**ch, "briefing_after": t, "ics": rows, "caldav": dav, "mail": mail, "character": char,
            "plan": (ch.get("plan") or "").strip(), "tools": (ch.get("tools") or "").strip(),
            **validate_interests(ch.get("interests"))}


def validate_interests(it):
    """관심사 단계 값 → {interests: …}. 단계를 안 거쳤으면(예전 화면) 건드리지 않는다."""
    if not it:
        return {}
    from popme import catalog
    known = {p["id"] for p in catalog.profiles()}
    chosen = [i for i in it.get("chosen") or [] if i in known]
    if not chosen and not it.get("customs"):
        raise ValueError("관심사를 하나 이상 골라 주세요.")
    clean = lambda xs, n: [str(x)[:n] for x in xs or [] if str(x).strip()][:20]
    picks = {}
    for pid, pk in (it.get("picks") or {}).items():
        if pid not in known:
            continue
        out = {}
        if pk.get("scope") in ("kr", "world"):
            out["scope"] = pk["scope"]
        for k in ("subs", "uses", "people", "companies", "topics"):
            if k in pk:
                out[k] = clean(pk[k], 40)
        if "extra" in pk:
            out["extra"] = [{k: str(e[k])[:40] for k in ("kind", "name", "why", "x", "region", "news", "news_en") if e.get(k)}
                            for e in pk["extra"][:10] if e.get("name")]
        if isinstance(pk.get("taste"), dict):  # ④ 취향 문답 결과 → taste(한두 문장)·likes·less
            t = taste_of(pk["taste"])
            out.update(taste=t["summary"], likes=t["likes"], less=t["less"])
        picks[pid] = out
    customs = []
    for c in (it.get("customs") or [])[:5]:
        f = c.get("final") or {}
        if not str(c.get("field") or "").strip() or not f:
            continue
        ans = c.get("answers") if isinstance(c.get("answers"), dict) else {}
        customs.append({"domain": c.get("domain") if isinstance(c.get("domain"), dict) else None,
                        "taste": taste_of(c["taste"]) if isinstance(c.get("taste"), dict) else None,
                        "field": str(c["field"]).strip()[:40], "scope": "kr" if c.get("scope") == "kr" else "world",
                        "lens": c.get("lens") if c.get("lens") in ("overview", "target", "both") else "target",
                        "type": str(c.get("type") or "기타")[:10],
                        "answers": {str(k)[:6]: clean(v, 20)[:10] for k, v in list(ans.items())[:3]},
                        "verified": [e for e in c.get("verified") or [] if isinstance(e, dict) and e.get("name")][:20],
                        "topics": clean(c.get("topics"), 10),
                        "final": {"name": str(f.get("name") or c["field"])[:12], "tag": str(f.get("tag") or c["field"])[:6],
                                  "summary": str(f.get("summary") or "")[:30], "about": str(f.get("about") or "")[:120],
                                  "discovery": str(f.get("discovery") or "")[:80]}})
    return {"interests": {"chosen": chosen, "picks": picks, "customs": customs}}


def taste_of(t):
    """④ 취향 문답 결과 정리: {summary(80자), likes·less(소식 종류 6개까지)}."""
    names = lambda xs: [str(x)[:16] for x in (xs if isinstance(xs, list) else []) if str(x).strip()][:6]
    return {"summary": str(t.get("summary") or "")[:80], "likes": names(t.get("likes")), "less": names(t.get("less"))}


def apply_interests(text, ids, picks):
    """고른 관심사 + 미리 만든 관심사에서 고친 것(고친 칸만)을 config 텍스트에."""
    text = set_value(text, "news", "interests", ids, before=("llm",))
    for pid, pk in (picks or {}).items():
        for k in ("subs", "uses", "scope", "people", "companies", "topics", "extra", "taste", "likes", "less"):
            if k in pk:
                text = set_value(text, f"news.picks.{pid}", k, pk[k], before=("llm",))
    return text


def save_interest_choices(it):
    """관심사 단계 값(validate_interests 거친 것) 저장: 새 관심사 파일 → 고른 관심사·고친 칸. 반환: 고른 관심사 id."""
    from popme import fieldcard, interests
    # 분야 카드가 화면에 없으면(사람 추천이 기다리다 넘어감) 뒤에서 끝난 것을 그때 받는다
    made = []
    for c in it["customs"]:
        if not (c.get("field") and c.get("final")):
            continue
        pid = interests.save_custom(c["field"], c.get("scope", "world"), c.get("verified", []), c.get("topics", []),
                                    c["final"], c.get("lens"), c.get("type"), c.get("answers"),
                                    c.get("domain") or fieldcard.verified(c["field"], wait=0), c.get("taste"))
        fieldcard.attach(c["field"], pid)  # 발굴 기준(대표 계정 팔로워)은 재는 대로 카드에
        made.append(pid)
    return list(dict.fromkeys(it["chosen"] + made))


def apply(text, ch):
    """고른 값(validate 거친 것)을 config 텍스트에 반영. 결과는 TOML로 다시 읽어 검사한다."""
    if ch.get("interest_ids") is not None:  # 관심사 단계
        text = apply_interests(text, ch["interest_ids"], ch.get("picks"))
    if ch.get("character"):
        text = set_value(text, "character", "file", ch["character"], before=("user",))
    text = set_value(text, "user", "plan", ch["plan"], before=("schedule",))
    text = set_value(text, "user", "tools", ch["tools"], before=("schedule",))
    text = set_value(text, "schedule", "briefing_after", ch["briefing_after"], before=("chrome",))
    text = set_value(text, "llm", "provider", ch["provider"])
    text = set_value(text, "calendar", "source", ch["source"], before=("timetree", "weather"))
    if ch["source"] == "ics":
        text = set_value(text, "calendar", "ics", ch["ics"])
    if ch["source"] in ("ics", "caldav"):
        text = set_value(text, "calendar", "holidays", bool(ch.get("holidays", True)))
    if ch["source"] == "caldav":
        d = ch["caldav"]
        text = set_value(text, "caldav", "server", caldav.ICLOUD, before=("timetree", "weather"))
        text = set_value(text, "caldav", "user", d["user"])
        if d["password"]:  # 비워 두면 저장된 앱 암호를 그대로 둔다
            text = set_value(text, "caldav", "password", d["password"])
        text = set_value(text, "caldav", "calendars", d["calendars"])
    if "use_windows_location" in ch:
        text = set_value(text, "weather", "use_windows_location", bool(ch["use_windows_location"]))
    if ch.get("mail"):
        m = ch["mail"]
        if m["enabled"] or "[mail]" in text:  # 안 켤 거면 [mail] 표를 새로 만들지 않는다
            for k in ("enabled", "host", "user", "password", "quiet_hours"):
                if k != "password" or m[k]:  # 비밀번호 칸을 비워 두면 저장된 비밀번호를 그대로 둔다
                    text = set_value(text, "mail", k, m[k])
    if ch.get("weather"):  # 고정 지역을 바꿨을 때만 {name, lat, lon}
        for k in ("name", "lat", "lon"):
            text = set_value(text, "weather", k, ch["weather"][k])
    tomllib.loads(text)
    return text


def write(text):
    """설정 저장: 검사 → 통째로 바꿔 쓰기 → 비밀번호 숨기기 (tomledit.save_config)."""
    tomledit.save_config(text)


def geocode_place(name):
    from popme import location
    res = location._nominatim("search", {"q": name, "countrycodes": "kr", "limit": 1})
    if not res:
        raise ValueError(f"「{name}」 지역을 못 찾았어요. 「서울」「부산 해운대구」처럼 넣어 주세요.")
    return {"name": name, "lat": round(float(res[0]["lat"]), 2), "lon": round(float(res[0]["lon"]), 2)}


def host_from_mx(domain, mx_names):
    """MX 서버 이름들 → {host, note}. 아는 곳이 없으면 imap.<도메인>."""
    for mx in mx_names:
        mx = mx.lower().rstrip(".")
        for end, host in MX_HOSTS.items():
            if mx == end or mx.endswith("." + end):
                return {"host": host, "note": ""}
        if mx.endswith(".outlook.com") or mx.endswith(".office365.com"):
            return {"host": "outlook.office365.com", "note": MS_NOTE}
    return {"host": f"imap.{domain}", "note": ""}


def guess_mail_host(domain):
    """메일 주소 도메인 → {host, note}. 목록에 없으면 nslookup으로 MX를 보고 추측한다."""
    domain = (domain or "").strip().lower()
    if domain in MAIL_DOMAINS:
        return {"host": MAIL_DOMAINS[domain], "note": ""}
    if not re.fullmatch(r"[a-z0-9-]+(\.[a-z0-9-]+)+", domain):
        return {"host": "", "note": ""}
    try:
        r = subprocess.run(["nslookup", "-type=mx", domain], capture_output=True, text=True, errors="replace",
                           timeout=8, creationflags=subprocess.CREATE_NO_WINDOW)
        mxs = re.findall(r"mail exchanger = (\S+)", r.stdout)
    except (OSError, subprocess.TimeoutExpired):
        mxs = []
    return host_from_mx(domain, mxs)


def saved_mail_password(host, user):
    """저장된 메일 비밀번호 — 저장된 것과 같은 계정(서버·주소)일 때만. 계정을 바꿨으면 "" (예전 계정 비밀번호를 쓰지 않게)."""
    cfg = tomllib.loads(base_text())
    m = cfg.get("mail", {})
    same = (m.get("user") or "").strip().lower() == (user or "").strip().lower() and m.get("host") == (host or "").strip()
    return saved_password("mail", cfg) if same else ""


def _mail_login_error(host, raw):
    low = raw.lower()
    if "application-specific password required" in low:  # 2단계 인증 켠 구글 계정에 구글 비밀번호를 넣은 경우
        return "구글 계정 비밀번호를 넣으셨어요. 구글 비밀번호 말고 「앱 비밀번호」 16자리를 넣어 주세요."
    if host == "imap.gmail.com":
        return ("구글이 로그인을 거절했어요. 구글 비밀번호 말고 「앱 비밀번호」 16자리를 넣었는지, "
                "위 메일 주소와 같은 구글 계정에서 만들었는지 봐 주세요.")
    return "로그인하지 못했어요. 메일 설정에서 「IMAP 사용」을 켰는지, 2단계 인증을 쓰면 앱 비밀번호를 넣었는지 봐 주세요."


def check_mail(host, user, password):
    """IMAP 로그인 → 받은편지함을 읽기 전용으로 열어 보기만 한다. 실패하면 ValueError(사람이 읽는 문장, 원문은 로그에)."""
    import imaplib
    import socket
    host, user = (host or "").strip(), (user or "").strip()
    gmail = host == "imap.gmail.com"
    if "@" not in user:
        raise ValueError("메일 주소를 먼저 넣어 주세요.")
    password = (password or "").replace(" ", "")
    if not password:  # 비밀번호 칸을 비웠으면 저장된 것으로 — 같은 계정일 때만
        password = saved_mail_password(host, user)
        if not password and saved_password("mail", tomllib.loads(base_text())):
            raise ValueError(f"메일 주소가 바뀌었어요. 새 계정의 {'앱 비밀번호' if gmail else '비밀번호'}를 넣어 주세요.")
        if not password:
            raise ValueError("앱 비밀번호 칸이 비어 있어요. ②의 「앱 비밀번호 만들기」를 눌러 주세요." if gmail
                             else "비밀번호 칸이 비어 있어요.")
    try:
        M = imaplib.IMAP4_SSL(host, 993, timeout=20)
        try:
            M.login(user.strip(), password)
            M.select("INBOX", readonly=True)
        finally:
            try:
                M.logout()
            except Exception:
                pass
    except imaplib.IMAP4.error as e:
        log.warning("메일 로그인 실패 (%s): %s", host, e)
        raise ValueError(_mail_login_error(host, str(e))) from None
    except socket.gaierror as e:
        log.warning("메일 서버 이름 실패 (%s): %s", host, e)
        raise ValueError(f"메일 서버({host})를 찾지 못했어요. 인터넷이 연결돼 있는지, @ 뒤 주소가 맞는지 봐 주세요.") from None
    except TimeoutError:
        raise ValueError("메일 서버가 답하지 않아요. 잠시 뒤 다시 눌러 주세요.") from None
    except OSError as e:
        log.warning("메일 서버 연결 실패 (%s): %s", host, e)
        raise ValueError("메일 서버에 연결하지 못했어요. 인터넷 연결을 확인하고 다시 눌러 주세요.") from None
    return True


def check_ics(url):
    """주소를 받아 일정 수·캘린더 이름을 돌려준다. 실패하면 ValueError."""
    import icalendar
    from popme.collectors import ics
    url = _check_url(url)
    try:
        cal = icalendar.Calendar.from_ical(ics.fetch(url, timeout=20))
    except Exception as e:
        raise ValueError(f"주소를 열지 못했어요 ({type(e).__name__}: {str(e)[:120]}). 주소 전체를 다시 복사해 주세요.")
    return {"events": len(cal.walk("VEVENT")), "name": str(cal.get("X-WR-CALNAME", "") or "")}


# ---------------------------------------------------------------- 로그인 단계 (전용 Chrome)

def _chrome_port():
    return int(tomllib.loads(base_text()).get("chrome", {}).get("port", 9333))


def login_status(sites):
    """{"chrome": 전용 Chrome이 켜져 있나, site: True/False}. 꺼져 있으면 site는 None (알 수 없음)."""
    from popme import chrome
    port = _chrome_port()
    if not chrome.is_running(port):
        return {"chrome": False, **{s: None for s in sites}}
    out = {"chrome": True}
    with chrome.attached(port) as ctx:
        for s in sites:
            site = LOGIN_SITES[s]
            try:
                if "cookie" in site:
                    url, name = site["cookie"]
                    out[s] = any(c["name"] == name and c["value"] for c in ctx.cookies([url]))
                else:  # 쿠키 이름으로는 알 수 없는 곳: 로그인 페이지로 넘기는지 본다 (화면은 안 연다)
                    out[s] = ctx.request.get(site["page"], max_redirects=0, timeout=15000).status == 200
            except Exception:
                log.warning("%s 로그인 확인 실패", s, exc_info=True)
                out[s] = None
    return out


def open_login(site):
    """전용 Chrome에 그 사이트 로그인 화면을 연다 (꺼져 있으면 켠다)."""
    from popme import chrome
    port, url = _chrome_port(), LOGIN_SITES[site]["url"]
    if not chrome.is_running(port):
        chrome.ensure_chrome(port, [url])
        return
    with chrome.attached(port) as ctx:
        page = ctx.new_page()
        page.goto(url)
        page.bring_to_front()


def request_first_briefing():
    """저장 뒤 POPME가 켜지면 바로 수집·브리핑을 시작하라는 표시 (app.py가 보고 지운다)."""
    config.FIRST_BRIEFING.write_text("1", encoding="utf-8")


def _plain(html):
    return re.sub(r"<[^>]+>", "", html)


HELP_TURNS = 4  # 이어 묻기 맥락으로 함께 보낼 앞 대화 수


def ask_help(provider, key, question, history=()):
    """「AI한테 해결 요청하기」 — 사용자가 적은 상황에 HELP[key] 안내 안에서만 짧게 답한다 (도구 끈 AI).
    history = 이 단계의 앞 대화 [{q, a}] — 「그래도 안 돼요」 같은 이어 묻기에 맥락을 준다. 실패하면 ValueError."""
    from popme import llm
    h = HELP[key]
    guide = "\n".join(f"{i}. {_plain(t)}" for i, t in enumerate(h["steps"], 1))
    faq = "\n".join(f"- {q}: {_plain(a)}" for q, a in h.get("faq", []))
    past = "\n".join(f"사용자: {str(t.get('q', ''))[:300]}\n도우미: {_plain(str(t.get('a', '')))[:400]}"
                     for t in list(history or [])[-HELP_TURNS:])
    prompt = (f"[안내]\n{guide}\n\n[자주 막히는 곳]\n{faq}\n\n"
              + (f"[지금까지 대화]\n{past}\n\n" if past else "")
              + f"[사용자가 막힌 상황]\n{question.strip()[:300]}")
    try:
        return llm.ask(_llm_cfg(provider), HELP_SYSTEM, prompt, timeout=90).strip()[:400]
    except Exception as e:
        raise ValueError(f"AI가 지금 답하지 못했어요. 「브리핑 AI」 단계에서 준비됐는지 봐 주세요. ({str(e)[:80]})")


def check_caldav(user, password):
    """Apple 계정 + 앱 암호로 로그인해 일정 캘린더 목록. 앱 암호를 비우면 저장된 것으로. 실패하면 ValueError."""
    password = (password or "").strip()
    if not password:
        password = saved_password("caldav", tomllib.loads(base_text()))
    if not (user or "").strip() or not password:
        raise ValueError("Apple 계정 이메일과 앱 암호를 넣어 주세요.")
    cals = caldav.discover(user, password)
    if not cals:
        raise ValueError("로그인은 됐는데 일정 캘린더가 없어요. 아이폰 설정에서 iCloud 캘린더가 켜져 있는지 봐 주세요.")
    return cals


# ---------------------------------------------------------------- AI CLI 설치·로그인·캘린더 연결 (새 터미널)

def _llm_cfg(provider):
    """저장된 [llm] 설정(claude_path 등)에 고른 AI만 바꿔 끼운 cfg."""
    return {"llm": {**tomllib.loads(base_text()).get("llm", {}), "provider": provider}}


def ai_status(provider):
    """{installed, logged_in(True/False/None=알 수 없음), path, plan(알아낸 요금제, 없으면 "")}"""
    from popme import llm
    finder = {"claude": llm.find_claude, "codex": llm.find_codex, "gemini": llm.find_gemini}[provider]
    try:
        exe = finder(_llm_cfg(provider))
    except RuntimeError:
        return {"installed": False, "logged_in": False, "plan": ""}
    home, plan = Path.home(), ""

    def run(*args):
        return llm._run([exe, *args], "", 20, None)  # npm .cmd면 시간이 넘을 때 node까지 끝낸다
    if provider == "codex":
        try:
            r = run("login", "status")
            logged = "logged in" in (r.stdout + r.stderr).lower()
        except Exception:
            logged = None
    elif provider == "claude":
        logged = None
        try:  # claude auth status: {"loggedIn": true, "subscriptionType": "pro", ...} (옛 버전엔 없음)
            st = json.loads(run("auth", "status", "--json").stdout)
            logged = bool(st.get("loggedIn"))
            plan = {"pro": "Claude Pro", "max": "Claude Max"}.get(str(st.get("subscriptionType", "")).lower(), "")
        except Exception:
            logged = (home / ".claude" / ".credentials.json").exists() or None  # 다른 방식 로그인이면 알 수 없음
    else:
        logged = (home / ".gemini" / "oauth_creds.json").exists() or None
    return {"installed": True, "logged_in": logged, "path": exe, "plan": plan}


def ai_test(provider):
    """고른 AI를 실제로 한 번 불러 본다 (도구 끈 상태, 짧은 답). 실패하면 ValueError(사용자에게 보일 문장)."""
    from popme import llm
    name = AIS[provider]["name"]
    try:
        answer = llm.ask(_llm_cfg(provider), "짧게 한 문장으로만 답한다.", "「준비됐어요」라고만 답해 주세요.", timeout=120)
    except subprocess.TimeoutExpired:
        raise ValueError(f"{name}이(가) 2분 안에 답하지 않았어요. 잠시 뒤 「다시 불러 보기」를 눌러 주세요.")
    except (RuntimeError, OSError) as e:
        err = str(e).lower()
        if llm.is_login(err):
            raise ValueError(f"{name} 로그인이 필요해요. 「로그인 하기」를 눌러 주세요.")
        if llm.is_limit(err):
            raise ValueError(f"{name} 사용 한도에 걸렸어요. 한도가 풀리면 브리핑도 써져요. 그대로 다음으로 가도 돼요.")
        raise ValueError(f"{name}을(를) 불러 보지 못했어요: {str(e)[:200]}")
    if not answer.strip():
        raise ValueError(f"{name}이(가) 빈 답을 줬어요. 「다시 불러 보기」를 눌러 주세요.")
    return answer.strip()[:60]


def _console(lines, title):
    """새 PowerShell 창에서 명령 실행 (창은 열어 둔다)."""
    script = "\n".join([f"$Host.UI.RawUI.WindowTitle = '{title}'",
                        "[Console]::OutputEncoding = [Text.Encoding]::UTF8", *lines])
    fd, path = tempfile.mkstemp(prefix="popme-", suffix=".ps1", dir=config.DATA_DIR)
    with os.fdopen(fd, "w", encoding="utf-8-sig") as f:  # PowerShell 5.1은 BOM이 있어야 UTF-8로 읽음
        f.write(script)
    subprocess.Popen(["powershell", "-NoExit", "-ExecutionPolicy", "Bypass", "-File", path], cwd=config.ROOT,
                     creationflags=subprocess.CREATE_NEW_CONSOLE)


def open_ai_setup(provider):
    _console(AI_SETUP[provider], f"POPME - {AIS[provider]['name']} 설치·로그인")


def chrome_found():
    from popme import chrome
    try:
        chrome.chrome_exe()
        return True
    except RuntimeError:
        return False


# ---------------------------------------------------------------- 창

class Api:
    def __init__(self, mode=None):
        self._window = None
        self._saved = False
        self._mode = mode  # {"start": pick|custom|edit, "pid"} — 브리핑 탭에서 「관심사」만 열 때
        self._char_image = None  # 새 캐릭터로 고른 이미지 (저장할 때 assets로)

    def get(self):
        return {**current(), "chrome": chrome_found(), "mode": self._mode}

    def save_interests(self, it):
        """「관심사」만 연 창의 저장: 다른 설정(AI·캘린더·메일)은 건드리지 않는다."""
        try:
            v = validate_interests(it)["interests"]
            write(apply_interests(base_text(), save_interest_choices(v), v["picks"]))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.exception("관심사 저장 실패")
            return {"ok": False, "error": f"저장하지 못했어요: {e}"}
        self._saved = True
        return {"ok": True}

    def ai_status(self, provider):
        return ai_status(provider)

    def ai_setup(self, provider):
        open_ai_setup(provider)

    def ai_test(self, provider):
        try:
            return {"ok": True, "answer": ai_test(provider)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def guess_mail_host(self, domain):
        return guess_mail_host(domain)

    def open_url(self, url):
        import webbrowser
        webbrowser.open(url)

    def check_ics(self, url):
        try:
            return {"ok": True, **check_ics(url)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def check_caldav(self, user, password):
        try:
            return {"ok": True, "calendars": check_caldav(user, password)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def take_clipboard(self, kind):
        try:
            return take_clipboard(kind)
        except Exception:
            log.exception("클립보드 읽기 실패")
            return ""

    def login_status(self, sites):
        try:
            return login_status(sites)
        except Exception as e:
            log.exception("로그인 확인 실패")
            return {"chrome": False, "error": str(e)[:200]}

    def open_login(self, site):
        try:
            open_login(site)
            return {"ok": True}
        except Exception as e:
            log.exception("로그인 창 열기 실패")
            return {"ok": False, "error": f"전용 Chrome을 열지 못했어요: {str(e)[:150]}"}

    def ask_help(self, provider, key, question, history=None):
        try:
            return {"ok": True, "answer": ask_help(provider, key, question, history or [])}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def request_first_briefing(self):
        request_first_briefing()

    def check_mail(self, host, user, password):
        try:
            return {"ok": check_mail(host, user, password)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}

    def save(self, ch):
        try:
            ch = validate(ch)
            place = (ch.get("place") or "").strip()
            if place and place != current()["place"]:
                ch["weather"] = geocode_place(place)
            if ch.get("interests"):  # 새 관심사 파일을 먼저 만들고, 고른 관심사 목록에 그 id를 넣는다
                ch["interest_ids"] = save_interest_choices(ch["interests"])
                ch["picks"] = ch["interests"]["picks"]
            if ch.get("new_character"):  # 다른 값을 다 확인한 뒤에: 저장을 다시 눌러도 파일이 늘지 않게
                from popme import charmaker
                ch["character"] = charmaker.save(ch["new_character"], self._char_image)
                self._char_image = None
            write(apply(base_text(), ch))
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.exception("설정 저장 실패")
            return {"ok": False, "error": f"저장하지 못했어요: {e}"}
        self._saved = True
        return {"ok": True}

    # --- 캐릭터 단계 (popme/charmaker.py) ---
    def character_draft(self, info, provider):
        from popme import charmaker
        try:
            return {"ok": True, **charmaker.draft(self._cfg(provider), info)}
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            log.exception("캐릭터 대사 만들기 실패")
            return {"ok": False, "error": f"대사를 만들지 못했어요: {str(e)[:120]}"}

    def character_image(self):
        """이미지 고르기 창 → 배경을 딴 미리 보기. 고르지 않고 닫으면 {"ok": False} (error 없음)."""
        import base64
        import webview
        from popme import charmaker
        picked = self._window.create_file_dialog(webview.OPEN_DIALOG, file_types=("이미지 (*.png;*.jpg;*.jpeg;*.webp;*.gif)",))
        if not picked:
            return {"ok": False}
        try:
            self._char_image = charmaker.prepare_image(picked[0])
        except ValueError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "preview": "data:image/png;base64," + base64.b64encode(self._char_image.read_bytes()).decode()}

    # --- 관심사 단계 (popme/interests.py) ---
    def _cfg(self, provider):
        cfg = tomllib.loads(base_text())
        if provider in AIS:  # 이번 마법사에서 고른 AI (아직 저장 전일 수 있다)
            cfg.setdefault("llm", {})["provider"] = provider
        return cfg

    def interest_preset(self, pid):
        from popme import interests
        return interests.preset_view(pid, tomllib.loads(base_text()))

    def interest_research(self, field):
        from popme import interests
        return interests.research(field)

    def interest_questions(self, field, lens, provider, headlines=None, context=""):
        from popme import interests
        return interests.questions(self._cfg(provider), field, lens, headlines, context)

    def interest_taste_heads(self, field, provider, domain=None, scope="world", subs=None, targets=None, extra=None, exclude=None, names=None):
        """④ 취향 문답: 고른 세부·대상의 최근 소식에서 편집자가 고른 서로 다른 종류의 헤드라인 [{title, category, group}].
        subs·targets: [{label, news, en, region}] 또는 이름."""
        from popme import taste
        return taste.headlines(self._cfg(provider), field, domain, scope, subs or [], targets or [], extra or [], exclude or [],
                               names=names if isinstance(names, dict) else None)

    def interest_taste(self, field, provider, domain=None, shown=None, liked=None, answers=None):
        """④ 누른 헤드라인 → {likes, less, summary}. answers: ③까지 고른 것 (줄이지 않는다)."""
        from popme import taste
        return taste.analyze(self._cfg(provider), field, domain, shown or [], liked or [], answers or [])

    def interest_suggest(self, field, scope, provider, lens=None, answers=None, headlines=None, context="", domain=None):
        from popme import interests
        return interests.suggest(self._cfg(provider), field, scope, lens, answers, headlines, context, domain)

    def interest_verify(self, entities, scope):
        from popme import interests
        try:
            return interests.verify(entities, scope)
        except Exception:
            log.exception("관심사 확인 실패")
            return [{**e, "x_status": "unknown", "feed": None, "news_count": 0} for e in entities]

    def interest_finalize(self, field, scope, people, companies, topics, provider, lens=None, answers=None):
        from popme import interests
        return interests.finalize(self._cfg(provider), field, scope, people, companies, topics, lens, answers)

    def close(self):
        self._window.destroy()


def main(mode=None):
    import webview
    api = Api(mode)
    api._window = webview.create_window("POPME 관심사" if mode else "POPME 설정", url=str(UI), js_api=api,
                                        width=520, height=760, min_size=(420, 560))
    webview.start()
    return 0 if api._saved else 1


if __name__ == "__main__":
    if sys.stdout:  # 트레이에서 pythonw로 띄우면 콘솔이 없어 None — 여기서 죽으면 설정 창이 아예 안 뜬다
        sys.stdout.reconfigure(encoding="utf-8")
    args = sys.argv[1:]
    try:
        if args[:1] == ["check-ics"] and len(args) == 2:
            info = check_ics(args[1])
            print(f"확인됨: 일정 {info['events']}개" + (f", 캘린더 이름 「{info['name']}」" if info["name"] else ""))
        elif args[:1] == ["check-caldav"] and len(args) == 2:  # 설정은 그대로 두고 iCloud 연결만 시험
            import getpass
            pw = getpass.getpass("앱 암호 (입력해도 화면에 안 보여요): ")
            for c in check_caldav(args[1], pw):
                n = len(caldav.fetch_events(c, args[1], pw))
                print(f"  {c['name']}: 일정 {n}개")
        elif not args:
            sys.exit(main())
        elif args[:1] == ["interests"]:  # 브리핑 탭에서: 「관심사」 단계만 (start: pick | custom | edit, edit이면 관심사 id)
            sys.exit(main({"start": (args[1:2] or ["pick"])[0], "pid": (args[2:3] or [""])[0]}))
        else:
            print(__doc__)
            sys.exit(2)
    except ValueError as e:
        print("실패:", e)
        sys.exit(1)
