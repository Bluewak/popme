"""공개 저장소에 개인 파일·문구가 섞이지 않았는지 검사한다. pre-commit 훅과 GitHub Actions가 같이 쓴다.

사용:
  python tools/check_private.py --staged   # 지금 커밋하려는 내용 (pre-commit 훅)
  python tools/check_private.py --all      # 저장소의 모든 파일 (CI)
  python tools/check_private.py --install  # 이 PC의 저장소에 pre-commit 훅 설치

검사 내용:
  1. 올리면 안 되는 파일: 개인 설정·캐릭터·이미지·DB·로그·묶음 zip
  2. 개인 이메일 주소 (GitHub noreply·예시 주소는 허용)
  3. 개인 단어 — 목록 자체가 개인 정보라 코드에 적지 않는다.
     내 PC는 저장소 루트의 .private-words.txt(올라가지 않음), GitHub Actions는 Secret PRIVATE_WORDS에서 읽는다.
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
    "config.toml", "PLAN.md", ".private-words.txt", "*.db", "*.log", "*.zip",
    "assets/backup/*", "docs/bubble_mockups.png",
    "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico",  # 이미지는 아래 허용 목록에 있는 것만
]
# 캐릭터 파일은 공개 예시(character.example.toml)만. 그 밖의 *.toml 캐릭터는 개인 것
ALLOWED_TOML = {"config.example.toml", "character.example.toml"}
ALLOWED_IMAGES = set()  # 공개해도 되는 이미지를 추가할 때 여기에 경로를 적는다
SKIP_CONTENT = {".gitignore"}  # 올리지 않을 파일 이름을 적는 곳이라 단어 검사에서 뺀다
TEXT_EXT = {".py", ".md", ".toml", ".html", ".ps1", ".txt", ".json", ".yml", ".yaml", ".js", ".css", ".cfg", ""}
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
EMAIL_OK = re.compile(r"(users\.noreply\.github\.com|@example\.(com|org)|noreply@)", re.I)
MAX_BYTES = 5 * 1024 * 1024


def private_words():
    env = os.environ.get("PRIVATE_WORDS")
    if env:
        lines = env.splitlines()
    else:
        f = ROOT / ".private-words.txt"
        lines = f.read_text(encoding="utf-8").splitlines() if f.exists() else []
    return [w.strip() for w in lines if w.strip() and not w.strip().startswith("#")]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout


def targets(mode):
    """(경로, 내용 bytes) 목록. --staged는 실제로 커밋될 내용(스테이징된 버전)을 읽는다."""
    if mode == "--staged":
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").decode("utf-8").split("\0")
        return [(n, git("show", f":{n}")) for n in names if n]
    names = git("ls-files", "-z").decode("utf-8").split("\0")
    return [(n, (ROOT / n).read_bytes()) for n in names if n and (ROOT / n).is_file()]


def check(items, words):
    problems = []
    for path, data in items:
        name = path.rsplit("/", 1)[-1]
        if path not in ALLOWED_IMAGES and any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p)
                                              for p in BLOCKED_FILES):
            problems.append(f"{path}: 올리면 안 되는 파일")
            continue
        if path.endswith(".toml") and name not in ALLOWED_TOML:
            problems.append(f"{path}: 개인 설정·캐릭터 파일일 수 있음 (공개는 *.example.toml만)")
            continue
        if len(data) > MAX_BYTES:
            problems.append(f"{path}: {len(data) // 1024 // 1024}MB — 너무 큼")
        if name in SKIP_CONTENT or Path(path).suffix.lower() not in TEXT_EXT:
            continue
        text = data.decode("utf-8", errors="ignore")
        low = text.lower()
        for w in words:
            i = low.find(w.lower())
            if i >= 0:
                line = text.count("\n", 0, i) + 1
                problems.append(f"{path}:{line}: 개인 단어 '{w}'")
        for m in EMAIL_RE.finditer(text):
            if not EMAIL_OK.search(m.group()):
                line = text.count("\n", 0, m.start()) + 1
                problems.append(f"{path}:{line}: 이메일 주소 '{m.group()}'")
    return problems


HOOK = """#!/bin/sh
# POPME: 커밋 전에 개인 파일·문구 검사 (tools/check_private.py). 일부러 건너뛰려면 git commit --no-verify
exec python tools/check_private.py --staged
"""


def install_hook():
    hook = ROOT / ".git" / "hooks" / "pre-commit"
    hook.write_text(HOOK, encoding="utf-8", newline="\n")
    print(f"설치함: {hook}")


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    mode = sys.argv[1] if len(sys.argv) > 1 else "--staged"
    if mode == "--install":
        return install_hook()
    words = private_words()
    if not words:
        print("주의: 개인 단어 목록이 없어 단어 검사는 건너뜀 (.private-words.txt 또는 PRIVATE_WORDS)")
    items = targets(mode)
    problems = check(items, words)
    if problems:
        print("✗ 개인 파일·문구가 섞여 있어요. 공개 저장소에 올리면 안 됩니다:")
        for p in problems:
            print("  -", p)
        print("고친 뒤 다시 커밋하세요. (정말 괜찮은 경우만: git commit --no-verify)")
        sys.exit(1)
    print(f"✓ 개인 파일·문구 검사 통과 ({len(items)}개 파일, 단어 {len(words)}개)")


if __name__ == "__main__":
    main()
