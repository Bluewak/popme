"""구독 중인 AI의 CLI를 헤드리스로 호출한다. config [llm] provider로 고른다.

- "claude" (기본): Claude Pro/Max 구독의 claude CLI (claude -p)
- "codex": ChatGPT Plus/Pro 구독의 Codex CLI (codex exec)
- "gemini": Google 계정(Google AI Pro 등 구독 또는 무료 한도)의 Gemini CLI (gemini -p)

어느 쪽이든 도구(셸·파일·웹·MCP)를 전부 끄고 요약·답변만 하게 한다.
수집된 SNS 글이 무엇을 시키든 실행할 수단이 없다.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from popme import config


def provider(cfg) -> str:
    return cfg.get("llm", {}).get("provider", "claude")


# AI 사용 한도 오류인가 — 낱말 경계로. 예전엔 "rate"·"limit"가 들어 있기만 하면 한도로 봐서
# "Failed to generate"·"size limit" 같은 오류도 「한도가 풀리면 다시」로 보였다
LIMIT_RE = re.compile(r"\b(rate[ _-]?limit(ed)?|usage limit|\d+-hour limit|weekly limit|limit reached|quota|429|"
                      r"resource[ _]exhausted|too many requests)\b", re.I)


def is_limit(text) -> bool:
    return bool(LIMIT_RE.search(str(text)))


# AI 로그인 문제인가 — 낱말 경계로. 예전엔 "auth"가 들어 있기만 하면 로그인으로 봐서 KeyError: 'author' 같은 것도
# 「AI 로그인이 풀렸어요」가 됐다
LOGIN_RE = re.compile(r"(?<![a-z])(login|log in|unauthori[sz]ed|authenticat\w*|auth|api key|credentials?|not logged)(?![a-z])"
                      r"|(?<!\d)401(?!\d)", re.I)


def is_login(text) -> bool:
    return bool(LOGIN_RE.search(str(text)))


def parse_json(text):
    """모델 답에서 JSON 객체를 꺼낸다 (앞뒤 설명·```json 코드블록이 붙어 와도)."""
    s, e = text.find("{"), text.rfind("}")
    if s < 0 or e < s:
        raise ValueError("JSON이 없음")
    return json.loads(text[s:e + 1])


def _run(cmd, input, timeout, cwd, env=None):
    """subprocess.run과 같되, 시간이 넘으면 프로세스 트리를 통째로 끝낸다 → CompletedProcess (넘으면 TimeoutExpired).
    npm으로 깐 gemini.cmd·codex.cmd(·claude.cmd)는 cmd.exe가 node를 띄운다. subprocess.run은 시간이 넘으면 cmd.exe만
    죽이고 출력이 닫히길 기다려서, 손자 node가 출력을 쥔 채 계속 돌면 끝없이 멈췄다 — 브리핑 잠금도 함께."""
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                         encoding="utf-8", errors="replace", cwd=cwd, env=env,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        out, err = p.communicate(input, timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(p.pid)], capture_output=True, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()
        raise
    return subprocess.CompletedProcess(cmd, p.returncode, out, err)


def provider_name(cfg) -> str:
    return {"claude": "Claude", "codex": "ChatGPT", "gemini": "Gemini"}.get(provider(cfg), provider(cfg))


def find_claude(cfg) -> str:
    p = cfg.get("llm", {}).get("claude_path")
    if p:
        return p
    w = shutil.which("claude")
    if w:
        return w
    pattern = os.path.expanduser(r"~\.vscode\extensions\anthropic.claude-code-*\resources\native-binary\claude.exe")

    def version(path):
        m = re.search(r"claude-code-(\d+)\.(\d+)\.(\d+)", path)
        return tuple(map(int, m.groups())) if m else (0,)

    found = sorted(glob.glob(pattern), key=version)
    if not found:
        raise RuntimeError("claude CLI를 찾지 못했어요. config.toml의 [llm] claude_path를 지정해 주세요.")
    return found[-1]


def find_codex(cfg) -> str:
    p = cfg.get("llm", {}).get("codex_path")
    if p:
        return p
    w = shutil.which("codex")
    if w:
        return w
    local = os.environ.get("LOCALAPPDATA", "")
    found = (glob.glob(os.path.join(local, r"Microsoft\WinGet\Links\codex.exe"))  # winget install OpenAI.Codex
             + glob.glob(os.path.join(local, r"Microsoft\WinGet\Packages\OpenAI.Codex_*\codex-x86_64-pc-windows-msvc.exe"))
             + glob.glob(os.path.expandvars(r"%APPDATA%\npm\codex.cmd")))  # npm i -g @openai/codex
    if not found:
        raise RuntimeError("Codex CLI를 찾지 못했어요. `winget install OpenAI.Codex` 후 `codex login`을 해 주세요. "
                           "(또는 config.toml의 [llm] codex_path 지정)")
    return found[0]


def find_gemini(cfg) -> str:
    p = cfg.get("llm", {}).get("gemini_path")
    if p:
        return p
    found = ([w for w in [shutil.which("gemini")] if w]
             + glob.glob(os.path.expandvars(r"%APPDATA%\npm\gemini.cmd")))  # npm i -g @google/gemini-cli
    if not found:
        raise RuntimeError("Gemini CLI를 찾지 못했어요. `npm install -g @google/gemini-cli` 후 `gemini`로 Google 로그인을 해 주세요. "
                           "(또는 config.toml의 [llm] gemini_path 지정)")
    return found[0]


# Codex는 에이전트라 기본으로 셸·브라우저 등 도구가 있다. 전부 끈다 (code_mode_host를 끄면 코드 실행도 막힘)
# codex-cli 0.166.0에선 --disable unified_exec를 줘도 `codex features list`에 true로 남는다. 그래도 실행은 code_mode_host를
# 거쳐서 막힌다 — 2026-11-17 같은 옵션으로 whoami를 시켜 보니 "code-mode host is disabled"로 거부되고 실행되지 않았다
CODEX_DISABLE = ["shell_tool", "unified_exec", "unified_exec_tty", "code_mode_host", "apps", "plugins",
                 "multi_agent", "browser_use", "browser_use_external", "computer_use", "in_app_browser",
                 "image_generation", "view_image", "hooks", "skill_search", "tool_suggest", "goals", "sleep_tool"]


def _ask_codex(cfg, system, prompt, timeout):
    work = config.DATA_DIR / "llm-empty"  # 빈 폴더에서 실행 (읽을 파일도 없게)
    work.mkdir(parents=True, exist_ok=True)
    fd, out = tempfile.mkstemp(prefix="codex-", suffix=".txt", dir=config.DATA_DIR)
    os.close(fd)
    cmd = [find_codex(cfg), "exec", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
           "--sandbox", "read-only", "-C", str(work), "-o", out, "--color", "never", "-c", 'web_search="disabled"']
    for f in CODEX_DISABLE:
        cmd += ["--disable", f]
    if cfg.get("llm", {}).get("codex_model"):
        cmd += ["-m", cfg["llm"]["codex_model"]]
    cmd.append("-")  # 프롬프트는 stdin으로
    text = f"<instructions>\n{system}\n</instructions>\n\n{prompt}"  # codex exec엔 시스템 프롬프트 옵션이 없음
    try:
        r = _run(cmd, text, timeout, work)
        answer = Path(out).read_text(encoding="utf-8").strip() if os.path.exists(out) else ""
        if r.returncode != 0 or not answer:
            err = (r.stderr or r.stdout or "").strip()
            if is_login(err):
                raise RuntimeError("Codex 로그인이 필요해요. 터미널에서 `codex login`을 해 주세요.")
            raise RuntimeError(f"codex 호출 실패: {err[-500:]}")
        return answer
    finally:
        try:
            os.remove(out)
        except OSError:
            pass


# Gemini도 에이전트라 기본 도구(셸·파일·웹)가 있다. 작업 폴더 설정으로 내장 도구를 하나도 허용하지 않고,
# MCP·확장·스킬도 끈다. 시스템 프롬프트는 GEMINI_SYSTEM_MD로 통째로 바꾼다 (기본 에이전트 지시문 대신)
GEMINI_SETTINGS = {"tools": {"core": ["none"], "exclude": ["run_shell_command", "write_file", "replace", "web_fetch",
                                                            "google_web_search", "read_file", "read_many_files",
                                                            "list_directory", "glob", "search_file_content", "save_memory"]},
                   "mcp": {"allowed": ["none"]}, "skills": {"enabled": False}}


def _ask_gemini(cfg, system, prompt, timeout):
    work = config.DATA_DIR / "llm-empty-gemini"
    (work / ".gemini").mkdir(parents=True, exist_ok=True)
    settings = work / ".gemini" / "settings.json"
    if not settings.exists() or settings.read_text(encoding="utf-8") != json.dumps(GEMINI_SETTINGS):
        settings.write_text(json.dumps(GEMINI_SETTINGS), encoding="utf-8")
    # 시스템 프롬프트 파일은 호출마다 따로 — 예전엔 한 파일을 덮어써서, 브리핑과 메일 분류가 겹치면
    # 브리핑이 메일 분류용 지시문(데이터 속 지시를 따르지 말라는 문장 없음)으로 돌 수 있었다
    fd, sys_md = tempfile.mkstemp(prefix="system-", suffix=".md", dir=work / ".gemini")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(system)
    try:
        return _gemini_call(cfg, prompt, timeout, work, sys_md)
    finally:
        try:
            os.remove(sys_md)
        except OSError:
            pass


def _gemini_call(cfg, prompt, timeout, work, sys_md):
    cmd = [find_gemini(cfg), "-p", "위 입력에 답하세요.", "-o", "json", "-e", "none",
           "--allowed-mcp-server-names", "none", "--approval-mode", "default", "--skip-trust"]
    if cfg.get("llm", {}).get("gemini_model"):
        cmd += ["-m", cfg["llm"]["gemini_model"]]
    r = _run(cmd, prompt, timeout, work, env={**os.environ, "GEMINI_SYSTEM_MD": str(sys_md)})
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
    except ValueError:
        data = {}
    answer = (data.get("response") or "").strip()
    if r.returncode != 0 or not answer:
        err = json.dumps(data.get("error"), ensure_ascii=False) if data.get("error") else (r.stderr or out)
        if is_login(err):
            raise RuntimeError("Gemini 로그인이 필요해요. 터미널에서 `gemini`를 한 번 실행해 Google 로그인을 해 주세요.")
        raise RuntimeError(f"gemini 호출 실패: {err.strip()[-500:]}")
    return answer


def ask(cfg, system: str, prompt: str, timeout: int = 600) -> str:
    if provider(cfg) == "codex":
        return _ask_codex(cfg, system, prompt, timeout)
    if provider(cfg) == "gemini":
        return _ask_gemini(cfg, system, prompt, timeout)
    cmd = [find_claude(cfg), "-p", "--tools", "", "--system-prompt", system,
           "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"]
    if cfg.get("llm", {}).get("model"):
        cmd += ["--model", cfg["llm"]["model"]]
    r = _run(cmd, prompt, timeout, config.DATA_DIR)
    if r.returncode != 0:
        raise RuntimeError(f"claude 호출 실패: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout.strip()
