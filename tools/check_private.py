"""공개 저장소에 개인 파일·문구가 섞이지 않았는지 검사한다. pre-commit 훅과 GitHub Actions가 같이 쓴다.

사용:
  python tools/check_private.py --staged   # 지금 커밋하려는 내용 (pre-commit 훅)
  python tools/check_private.py --all      # 저장소의 모든 파일 (CI)
  python tools/check_private.py --install  # 이 PC의 저장소에 pre-commit 훅 설치
  python tools/check_private.py --sync-secret  # .private-words.txt를 GitHub Secret PRIVATE_WORDS로 (CI가 같은 목록·예외를 쓰게)

검사 내용:
  1. 올리면 안 되는 파일: 개인 설정·캐릭터·이미지·DB·로그·묶음 zip
  2. 개인 이메일 주소 (GitHub noreply·예시 주소는 허용)
  3. 개인 단어 — 목록 자체가 개인 정보라 코드에 적지 않는다.
     내 PC는 저장소 루트의 .private-words.txt(올라가지 않음), GitHub Actions는 Secret PRIVATE_WORDS에서 읽는다.
     「!」로 시작하는 줄은 예외(공개 고유명사 한 덩어리, 예: 지역명이 붙은 구단 이름) — 그 자리를 덮으면 넘어간다.
     내 PC에서 걸리면 개인정보 검토자 페르소나(popme/personas/privacy_reviewer.toml, LangGraph)에게 묻고,
     공개 고유명사의 일부로 확신하면 예외를 .private-words.txt에 적는다. CI는 AI 없이 예외만 본다 —
     예외를 GitHub에도 올리려면: python tools/check_private.py --sync-secret
  4. 너무 큰 파일 (5MB 초과)
"""
import fnmatch
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

BLOCKED_FILES = [
    "config.toml", "config.toml.bak-*", "PLAN.md", ".private-words.txt", "*.db", "*.log", "*.zip",
    "assets/backup/*", "docs/bubble_mockups.png",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico",  # 이미지는 아래 허용 목록에 있는 것만
]
# 캐릭터 파일은 공개 예시(character.example.toml)만. 그 밖의 *.toml 캐릭터는 개인 것
ALLOWED_TOML = {"config.example.toml", "character.example.toml"}
# 앱이 챙기는 소식 출처·관심사 목록 (개인 설정 아님 — 내용의 개인 단어 검사는 그대로 한다)
ALLOWED_TOML_PATHS = ["popme/feeds.toml", "popme/profiles/*.toml", "popme/taxonomy.toml", "popme/personas/*.toml"]
ALLOWED_IMAGES = set()  # 공개해도 되는 이미지를 추가할 때 여기에 경로를 적는다
SKIP_CONTENT = {".gitignore"}  # 올리지 않을 파일 이름을 적는 곳이라 단어 검사에서 뺀다
TEXT_EXT = {".py", ".md", ".toml", ".html", ".ps1", ".txt", ".json", ".yml", ".yaml", ".js", ".css", ".cfg", ""}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
EMAIL_OK = re.compile(r"(users\.noreply\.github\.com|@example\.(com|org)|noreply@)", re.I)
MAX_BYTES = 5 * 1024 * 1024


WORDS_FILE = ROOT / ".private-words.txt"


def private_lines():
    env = os.environ.get("PRIVATE_WORDS")
    if env:
        lines = env.splitlines()
    else:
        lines = WORDS_FILE.read_text(encoding="utf-8").splitlines() if WORDS_FILE.exists() else []
    return [w.strip() for w in lines if w.strip() and not w.strip().startswith("#")]


def private_words():
    return [w for w in private_lines() if not w.startswith("!")]


def exceptions():
    """「!」 줄: 개인 단어가 들어 있어도 되는 공개 고유명사 (그 덩어리가 그 자리를 덮을 때만)."""
    return [w[1:].strip() for w in private_lines() if w.startswith("!") and w[1:].strip()]


def covered(line, start, end, allow):
    """line[start:end](개인 단어)를 예외 덩어리 하나가 덮나."""
    for a in allow:
        i = line.find(a)
        while i >= 0:
            if i <= start and end <= i + len(a):
                return True
            i = line.find(a, i + 1)
    return False


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def targets(mode):
    """(경로, 내용 bytes) 목록. --staged는 실제로 커밋될 내용(스테이징된 버전)을 읽는다."""
    if mode == "--staged":
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8").split("\0")
        return [(n, git("show", f":{n}")) for n in names if n]
    names = git("ls-files", "-z").decode("utf-8").split("\0")
    return [(n, (ROOT / n).read_bytes()) for n in names if n and (ROOT / n).is_file()]


def check(items, words, allow=(), hits=None):
    """문제 문구 목록. hits(리스트)를 주면 개인 단어 걸림을 {path, lineno, word, line, context, wide}로도 모은다 (AI 검토용)."""
    problems = []
    for path, data in items:
        name = path.rsplit("/", 1)[-1]
        if path not in ALLOWED_IMAGES and any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p)
                                              for p in BLOCKED_FILES):
            problems.append(f"{path}: 올리면 안 되는 파일")
            continue
        if (path.endswith(".toml") and name not in ALLOWED_TOML
                and not any(fnmatch.fnmatch(path, p) for p in ALLOWED_TOML_PATHS)):
            problems.append(f"{path}: 개인 설정·캐릭터 파일일 수 있음 (공개는 *.example.toml만)")
            continue
        if len(data) > MAX_BYTES:
            problems.append(f"{path}: {len(data) // 1024 // 1024}MB — 너무 큼")
        if name in SKIP_CONTENT or Path(path).suffix.lower() not in TEXT_EXT:
            continue
        text = data.decode("utf-8", errors="ignore")
        low = text.lower()
        lines = text.split("\n")
        for w in words:
            i = low.find(w.lower())
            while i >= 0:
                n = text.count("\n", 0, i)
                head = text.rfind("\n", 0, i) + 1
                line = lines[n]
                if not covered(line.lower(), i - head, i - head + len(w), [a.lower() for a in allow]):
                    problems.append(f"{path}:{n + 1}: 개인 단어 '{w}'")
                    if hits is not None:
                        hits.append({"path": path, "lineno": n + 1, "word": w, "line": line.strip()[:300],
                                     "context": "\n".join(lines[max(0, n - 2):n + 3])[:800],
                                     "wide": "\n".join(lines[max(0, n - 8):n + 9])[:2400]})
                i = low.find(w.lower(), i + len(w))
        for m in EMAIL_RE.finditer(text):
            if not EMAIL_OK.search(m.group()):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{path}:{line}: 이메일 주소 '{m.group()}'")
    return problems


HOOK = """#!/bin/sh
# POPME: 커밋 전에 개인 파일·문구 검사 (tools/check_private.py). 일부러 건너뛰려면 git commit --no-verify
exec python tools/check_private.py --staged
"""


def review(hits):
    """내 PC에서만: 걸린 줄마다 개인정보 검토자에게 묻는다 → 예외로 남긴 덩어리 목록. AI를 못 부르면 빈 목록 (지금처럼 막음)."""
    if os.environ.get("GITHUB_ACTIONS") or os.environ.get("POPME_NO_AI_REVIEW") or not hits:
        return []
    sys.path.insert(0, str(ROOT))
    try:
        from popme import config, judges
        cfg = config.load()
    except Exception as e:  # LangGraph가 없거나 설정을 못 읽으면 AI 없이
        print(f"(AI 검토 건너뜀: {e})")
        return []
    added, seen = [], set()
    for h in hits:
        key = (h["word"], h["line"])
        if key in seen:
            continue
        seen.add(key)
        if any(covered(h["line"].lower(), *span, [a.lower() for a in added]) for span in spans(h)):
            continue
        print(f"  … AI가 보는 중: {h['path']}:{h['lineno']} 「{h['word']}」")
        try:
            r = judges.privacy_review(cfg, h["word"], h["path"], h["line"], h["context"], h["wide"])
        except Exception as e:
            print(f"  (AI 검토 실패 — 막아 둠: {e})")
            continue
        if r["allow"]:
            added.append(r["phrase"])
            print(f"  ✓ AI가 공개 고유명사로 보고 허용: 「{r['phrase']}」 ({r['reason']}, 확신 {r['confidence']:.2f})")
        else:
            print(f"  ✗ 막음: {r['verdict']} — {r['reason']}")
    if added:
        with WORDS_FILE.open("a", encoding="utf-8", newline="\n") as f:
            f.write("".join(f"\n!{a}" for a in dict.fromkeys(added)) + "\n")
        print(f"  예외를 {WORDS_FILE.name}에 적었어요. CI도 통과하려면: python tools/check_private.py --sync-secret")
    return added


def spans(h):
    line, w = h["line"].lower(), h["word"].lower()
    i = line.find(w)
    while i >= 0:
        yield (i, i + len(w))
        i = line.find(w, i + 1)


def sync_secret():
    """.private-words.txt(개인 단어 + 예외)를 GitHub Secret PRIVATE_WORDS로 — CI가 같은 목록을 쓰게."""
    if not WORDS_FILE.exists():
        sys.exit(".private-words.txt가 없어요")
    r = subprocess.run(["gh", "secret", "set", "PRIVATE_WORDS"], cwd=ROOT, input=WORDS_FILE.read_bytes(), capture_output=True)
    print("✓ GitHub Secret PRIVATE_WORDS를 고쳤어요" if r.returncode == 0 else f"실패: {r.stderr.decode('utf-8', 'ignore')}")
    sys.exit(r.returncode)


def install_hook():
    hook = ROOT / ".git" / "hooks" / "pre-commit"
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    print(f"설치함: {hook}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "--staged"
    if mode == "--install":
        return install_hook()
    if mode == "--sync-secret":
        return sync_secret()
    words = private_words()
    if not words:
        print("주의: 개인 단어 목록이 없어 단어 검사는 건너뜀 (.private-words.txt 또는 PRIVATE_WORDS)")
    items = targets(mode)
    hits = []
    problems = check(items, words, exceptions(), hits)
    if hits and review(hits):  # 내 PC: AI가 공개 고유명사로 본 것은 예외로 남기고 다시 검사
        problems = check(items, words, exceptions())
    if problems:
        print("✗ 개인 파일·문구가 섞여 있어요. 공개 저장소에 올리면 안 됩니다:")
        for p in problems:
            print("  -", p)
        print("고친 뒤 다시 커밋하세요. (정말 괜찮은 경우만: git commit --no-verify)")
        sys.exit(1)
    print(f"✓ 개인 파일·문구 검사 통과 ({len(items)}개 파일, 단어 {len(words)}개)")


if __name__ == "__main__":
    main()
