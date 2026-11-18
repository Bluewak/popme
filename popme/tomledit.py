"""config.toml 줄 단위 편집 (주석·다른 설정 보존)과 저장.

예전엔 설정 창 모듈(setup_window)에 있어서 catalog·interests가 UI 모듈을 불러와야 했다(순환 의존의 뿌리).
config·fileio만 부른다.

알려진 한계: 여러 줄 문자열(\"\"\")이나 줄 첫머리가 [로 시작하는 배열 줄을 표 머리로 오인할 수 있다.
지금 이 모듈로 쓰는 값은 그런 모양을 만들지 않는다 (배열은 한 줄에 값 하나, 들여쓰기).
"""
import json
import logging
import re
import shutil
import tomllib

from popme import config
from popme.fileio import atomic_write_text

log = logging.getLogger(__name__)

_HEADER = re.compile(r"^\s*\[\[?\s*([^\]]+?)\s*\]\]?")
_BARE = re.compile(r"^[A-Za-z0-9_-]+$")
_KEY_PART = re.compile(r'\s*(?:"((?:[^"\\]|\\.)*)"|\'([^\']*)\'|([A-Za-z0-9_-]+))\s*(?:\.|$)')


def split_key(s):
    """TOML 점 키 → 조각 목록. 따옴표 조각도 푼다 (news."custom-축구" → ["news", "custom-축구"]). 못 풀면 None."""
    parts, i = [], 0
    while i < len(s):
        m = _KEY_PART.match(s, i)
        if not m or m.end() == i:
            return None
        if m.group(1) is not None:
            parts.append(json.loads(f'"{m.group(1)}"'))
        else:
            parts.append(m.group(2) if m.group(2) is not None else m.group(3))
        i = m.end()
    return parts


def table_header(section):
    """"news.picks.custom-축구" → [news.picks."custom-축구"]. TOML의 맨 키는 ASCII만 된다 —
    예전엔 한글 이름의 새 관심사를 고치면 [news.picks.custom-축구]가 저장돼 다음 실행부터 설정을 못 읽었다."""
    return "[" + ".".join(p if _BARE.match(p) else json.dumps(p, ensure_ascii=False) for p in section.split(".")) + "]"


def _section_span(lines, name):
    """[name] 표의 (머리 줄, 다음 표 머리 줄) 번호. 없으면 None. 따옴표 조각은 풀어서 견준다."""
    want = name.split(".")
    start = None
    for i, line in enumerate(lines):
        m = _HEADER.match(line)
        if not m:
            continue
        if start is not None:
            return start, i
        if not line.lstrip().startswith("[[") and split_key(m.group(1)) == want:
            start = i
    return (start, len(lines)) if start is not None else None


def _value_end(lines, i, col):
    """lines[i][col:]에서 시작하는 TOML 값이 끝나는 (줄, 칸). 배열·인라인 표는 여러 줄에 걸칠 수 있다."""
    depth, quote = 0, None
    for j in range(i, len(lines)):
        text, k = lines[j], (col if j == i else 0)
        while k < len(text):
            c = text[k]
            if quote:
                if c == "\\" and quote == '"':
                    k += 1
                elif c == quote:
                    quote = None
                    if depth == 0:
                        return j, k + 1
            elif c in "\"'":
                quote = c
            elif c in "[{":
                depth += 1
            elif c in "]}":
                depth -= 1
                if depth == 0:
                    return j, k + 1
            elif c == "#":
                if depth == 0:
                    return j, k
                break  # 배열 안 주석: 다음 줄로
            elif depth == 0 and c.isspace():
                return j, k
            k += 1
        if depth == 0 and not quote:
            return j, len(text)
    return len(lines) - 1, len(lines[-1])


def literal(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON 문자열은 TOML 기본 문자열로도 유효
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + "".join(f"  {literal(x)},\n" for x in v) + "]"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{_key(k)} = {literal(x)}" for k, x in v.items()) + " }"
    raise TypeError(type(v))


def _key(k):
    return k if _BARE.match(k) else json.dumps(k, ensure_ascii=False)


def set_value(text, section, key, value, before=()):
    """[section]의 key 값을 바꾼다 (같으면 그대로). 키가 없으면 표 끝에, 표가 없으면 before 표 앞(없으면 파일 끝)에 만든다."""
    lines = text.split("\n")
    new = literal(value)
    span = _section_span(lines, section)
    if span is None:
        at = next((s[0] for s in (_section_span(lines, b) for b in before) if s), None)
        block = [table_header(section), f"{_key(key)} = {new}", ""]
        if at is None:
            while lines and not lines[-1].strip():
                lines.pop()
            lines += [""] + block
        else:
            lines[at:at] = block
        return "\n".join(lines).rstrip("\n") + "\n"
    s, e = span
    key_re = re.compile(rf"^(\s*{re.escape(key)}\s*=\s*)")
    for i in range(s + 1, e):
        m = key_re.match(lines[i])
        if not m:
            continue
        j, k = _value_end(lines, i, m.end())
        old = (lines[i][m.end():k] if j == i else "\n".join([lines[i][m.end():]] + lines[i + 1:j] + [lines[j][:k]]))
        if old == new:
            return text
        rest = lines[j][k:]
        if rest.lstrip().startswith("#"):  # 오른쪽 주석은 원래 칸에 맞춘다
            col = len(lines[j]) - len(rest.lstrip())
            last = (m.group(1) + new).split("\n")[-1]
            rest = " " * max(1, col - len(last)) + rest.lstrip()
        lines[i:j + 1] = (m.group(1) + new + rest).split("\n")
        return "\n".join(lines)
    last = s  # 표의 마지막 값 줄 뒤 (표 뒤의 빈 줄·다음 표 설명 주석 앞)
    i = s + 1
    while i < e:
        if lines[i].strip() and not lines[i].lstrip().startswith("#") and "=" in lines[i]:
            rest = lines[i][lines[i].index("=") + 1:]
            col = len(lines[i]) - len(rest.lstrip())
            last = _value_end(lines, i, col)[0]  # 여러 줄 배열이면 닫는 줄까지 (안에 끼워 넣지 않게)
            i = last + 1
        else:
            i += 1
    lines[last + 1:last + 1] = f"{_key(key)} = {new}".split("\n")
    return "\n".join(lines)


def base_text():
    """지금 설정 텍스트 (없으면 기본 예시)."""
    path = config.CONFIG_PATH if config.CONFIG_PATH.exists() else config.EXAMPLE_CONFIG
    return path.read_text(encoding="utf-8-sig")


def save_config(text):
    """config.toml 저장: TOML로 다시 읽어 검사 → 지금 파일을 config.toml.last-good으로 → 통째로 바꿔 쓰기 → 비밀번호 숨기기.
    깨진 텍스트는 쓰지 않는다 (ValueError). last-good은 config.load가 사용자 파일을 못 읽을 때 대신 쓴다."""
    tomllib.loads(text)
    path = config.CONFIG_PATH
    if path.exists():
        try:
            tomllib.loads(path.read_text(encoding="utf-8-sig"))
            shutil.copyfile(path, config.last_good())
            config._hide(config.last_good(), overwrite=False)  # 복사본에도 평문 비밀번호를 두지 않는다
        except (OSError, ValueError):
            pass  # 지금 파일이 이미 깨졌으면 last-good을 덮지 않는다
    atomic_write_text(path, text)
    config.hide_passwords()
