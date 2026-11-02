"""설정 창 「캐릭터」 단계: 이름·호칭·말투를 받아 캐릭터 파일을 만든다.

대사는 기본 캐릭터(character.example.toml)를 바탕으로 AI(personas/character_writer.toml)가 말투에 맞게 다시 쓴다.
AI가 답하지 못하거나 규칙에 어긋난 칸은 기본 대사를 그대로 쓴다 — AI 때문에 캐릭터를 못 만드는 일은 없다.
"""
import re
import shutil
import string
import tomllib
from datetime import datetime

from popme import config, prepare, setup_window

NEW_PET = "new-pet.png"  # 저장 전 미리 보기용 이미지 (config.DATA_DIR)
MAX_LINE = 80
MAX_PER_CAT = 3
# id → (화면 이름, 말투 설명)
TONES = {
    "calm": ("차분한", "차분하고 다정한 존댓말(해요체)을 쓴다."),
    "bright": ("발랄한", "밝고 기운찬 존댓말을 쓰고 느낌표를 자주 쓴다."),
    "cool": ("시크한", "짧고 무심한 반말을 쓰지만 챙겨 줄 건 챙긴다."),
    "playful": ("장난스러운", "친구처럼 장난기 있는 반말을 쓴다."),
    "polite": ("정중한", "집사처럼 깍듯한 합쇼체를 쓴다."),
}
# AI가 다시 쓰는 화면 문구 (글자만 받고, 기본 문구 끝의 <br>은 코드가 다시 붙인다)
UI_KEYS = ("briefing_lead", "urgent_lead", "warn_lead", "today_events", "no_events", "no_briefing",
           "plan_thinking", "plan_confirm", "plan_fail", "add_fail")
UI_FIELDS = {"name", "user", "n", "msg"}  # character.py가 [ui]를 채울 때 넘기는 자리표시 — 다른 이름이면 켤 때 죽는다
SAMPLES = ("greet_morning", "collected", "take_break", "petted")
HEADER = "# 설정 창에서 만든 캐릭터. 메모장으로 고쳐도 돼요. 칸 설명: docs/CHARACTER.md\n"


def base():
    return tomllib.loads((config.DEFAULTS_DIR / "character.example.toml").read_text(encoding="utf-8"))


def _text(v, n):
    return " ".join(str(v or "").split()).translate({ord(c): None for c in "<>{}\"\\"})[:n]


def clean_info(info):
    """화면에서 받은 이름·호칭·말투 → 쓸 값. 이름이 없으면 ValueError(사용자에게 보일 문장)."""
    name = _text(info.get("name"), 12)
    if not name:
        raise ValueError("캐릭터 이름을 넣어 주세요.")
    tone = info.get("tone") if info.get("tone") in TONES else "calm"
    return {"name": name, "call_user": _text(info.get("call_user"), 12), "tone": tone, "detail": _text(info.get("detail"), 80)}


def persona_prompt(info):
    """아침 브리핑 때 '오늘의 멘트'를 쓰는 AI에게 주는 말투 설명."""
    rules = [TONES[info["tone"]][1]]
    if info["call_user"]:
        rules.append(f'사용자 호칭은 "{info["call_user"]}"이다. 대사에서 이 호칭으로 부른다.')
    if info["detail"]:
        rules.append(f'캐릭터 설정: {info["detail"]}')
    rules += ["한 문장 40자 이내, 이모지는 쓰지 않는다.", "과장하지 않고, 주어진 데이터에 있는 사실만 말한다."]
    return f"너는 데스크톱 비서 캐릭터 '{info['name']}'다.\n말투 규칙:\n" + "\n".join(f"- {r}" for r in rules)


def _fields(text):
    """글의 자리표시 이름들. {n:>3}처럼 형식을 달았거나 모양이 깨졌으면 ValueError."""
    out = set()
    for _, field, spec, conv in string.Formatter().parse(text):
        if field is None:
            continue
        if spec or conv or not field.isidentifier():
            raise ValueError(text)
        out.add(field)
    return out


def _good(text, may, must):
    """자리표시가 may 안에 있고 must를 모두 쓰는 글인가."""
    try:
        return must <= _fields(text) <= may
    except ValueError:
        return False


def _plain(v):
    return " ".join(re.sub(r"<[^>]*>", "", str(v or "")).split())[:MAX_LINE]


def clean_lines(raw, lines=None):
    """AI가 쓴 [lines] → 규칙에 맞는 것만. 비었거나 어긋난 칸은 기본 대사.
    자리표시는 그 칸의 기본 대사에 나온 것만 쓸 수 있고, 모든 기본 대사에 있는 것(warn의 {msg} 등)은 빠지면 안 된다."""
    lines = lines or base()["lines"]
    out = {}
    for cat, default in lines.items():
        used = [_fields(x) for x in default]
        may, must = set().union(*used) | {"user"}, set.intersection(*used)
        got = raw.get(cat) if isinstance(raw, dict) else None
        keep = [x for x in map(_plain, got if isinstance(got, list) else []) if x and _good(x, may, must)]
        out[cat] = keep[:MAX_PER_CAT] or default
    return out


def clean_ui(raw, ui=None):
    """AI가 쓴 [ui] 문구 → 규칙에 맞는 것만 기본 문구 위에 덮는다."""
    ui = ui or base()["ui"]
    out = dict(ui)
    for k in UI_KEYS:
        text = _plain(raw.get(k)) if isinstance(raw, dict) else ""
        if text and _good(text, UI_FIELDS, _fields(ui[k]) & UI_FIELDS):
            out[k] = text + ("<br>" if ui[k].endswith("<br>") else "")
    return out


def samples(lines, info):
    """화면에 보일 대사 몇 줄 (자리표시를 채운 글)."""
    return [lines[c][0].format(user=info["call_user"]) for c in SAMPLES if c in lines]


def draft(cfg, info):
    """AI에게 대사를 맡긴다 → {lines, ui, samples}. 실패하면 ValueError(사용자에게 보일 문장)."""
    from popme import persona
    info, b = clean_info(info), base()
    try:
        out = persona.ask(cfg, "character_writer", {
            "name": info["name"], "call_user": info["call_user"], "tone": TONES[info["tone"]][1], "detail": info["detail"],
            "lines": b["lines"], "ui": {k: _plain(b["ui"][k]) for k in UI_KEYS}}, timeout=240)
    except Exception as e:
        raise ValueError(f"AI가 대사를 쓰지 못했어요 ({str(e)[:80]}). 「다음」을 누르면 기본 대사로 만들어요. "
                         "말투는 저장되니 AI가 준비되면 오늘의 멘트부터 이 말투로 나와요.")
    lines = clean_lines(out["lines"], b["lines"])
    return {"lines": lines, "ui": clean_ui(out["ui"], b["ui"]), "samples": samples(lines, info)}


def clean(nc):
    """저장할 새 캐릭터 값 정리: 화면을 거쳐 온 대사도 다시 검사한다."""
    return {**clean_info(nc), "lines": clean_lines(nc.get("lines")), "ui": clean_ui(nc.get("ui"))}


def _multiline(text):
    return '"""\n' + text.replace("\\", "\\\\").replace('"""', '\\"""') + '"""'


def dump(cfg):
    """캐릭터 dict → TOML 글. 말투 설명은 메모장으로 고치기 쉽게 여러 줄로 쓴다."""
    out = [HEADER]
    for section, values in cfg.items():
        out.append(f"[{section}]")
        out += [f"{k} = {_multiline(v) if isinstance(v, str) and chr(10) in v else setup_window._literal(v)}"
                for k, v in values.items()]
        out.append("")
    return "\n".join(out)


def new_file():
    """쓸 파일 이름: character.toml이 비어 있으면 그것, 있으면 character-2.toml… (있는 파일은 덮어쓰지 않는다)."""
    if not (config.DATA_DIR / "character.toml").exists():
        return "character.toml"
    n = 2
    while (config.DATA_DIR / f"character-{n}.toml").exists():
        n += 1
    return f"character-{n}.toml"


def save(nc, image=None):
    """clean을 거친 값으로 캐릭터 파일을 만든다 → 파일 이름. image: prepare_image 결과 (있으면 지금 모습을 바꾼다)."""
    b = base()
    b["persona"] = {"name": nc["name"], "call_user": nc["call_user"], "prompt": persona_prompt(nc)}
    b["ui"], b["lines"] = nc["ui"], nc["lines"]
    text = dump(b)
    tomllib.loads(text)
    name = new_file()
    (config.DATA_DIR / name).write_text(text, encoding="utf-8", newline="\n")
    if image:
        install_image(image)
    return name


def prepare_image(src):
    """고른 이미지 → 배경을 따서 임시 파일로 (popme/prepare.py). 이미지가 아니면 ValueError."""
    out = config.DATA_DIR / NEW_PET
    try:
        prepare.prepare(src, "pet", out=out)
    except Exception as e:
        raise ValueError(f"이미지를 열지 못했어요: {str(e)[:80]}")
    return out


def install_image(path):
    """평소 모습으로 넣는다. 예전 이미지는 지우지 않고 내 이미지 폴더의 backup/으로 — 새 캐릭터에 옛 표정이 섞이지 않게 모두 옮긴다."""
    assets = config.ASSETS_DIR
    old = [p for p in assets.glob("pet*") if p.suffix.lower() in (".png", ".webp", ".gif")]
    if old:
        keep = assets / "backup" / datetime.now().strftime("%Y%m%d-%H%M%S")
        keep.mkdir(parents=True, exist_ok=True)
        for p in old:
            shutil.move(p, keep / p.name)
    shutil.move(path, assets / "pet.png")
