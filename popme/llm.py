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

from popme.config import DATA_DIR


def provider(cfg) -> str:
    return cfg.get("llm", {}).get("provider", "claude")


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
CODEX_DISABLE = ["shell_tool", "unified_exec", "unified_exec_tty", "code_mode_host", "apps", "plugins",
                 "multi_agent", "browser_use", "browser_use_external", "computer_use", "in_app_browser",
                 "image_generation", "view_image", "hooks", "skill_search", "tool_suggest", "goals", "sleep_tool"]


def _ask_codex(cfg, system, prompt, timeout):
    work = DATA_DIR / "llm-empty"  # 빈 폴더에서 실행 (읽을 파일도 없게)
    work.mkdir(parents=True, exist_ok=True)
    fd, out = tempfile.mkstemp(prefix="codex-", suffix=".txt", dir=DATA_DIR)
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
        r = subprocess.run(cmd, input=text, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, cwd=work, creationflags=subprocess.CREATE_NO_WINDOW)
        answer = Path(out).read_text(encoding="utf-8").strip() if os.path.exists(out) else ""
        if r.returncode != 0 or not answer:
            err = (r.stderr or r.stdout or "").strip()
            if "login" in err.lower() or "auth" in err.lower():
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
    work = DATA_DIR / "llm-empty-gemini"
    (work / ".gemini").mkdir(parents=True, exist_ok=True)
    (work / ".gemini" / "settings.json").write_text(json.dumps(GEMINI_SETTINGS), encoding="utf-8")
    sys_md = work / ".gemini" / "system.md"
    sys_md.write_text(system, encoding="utf-8")
    cmd = [find_gemini(cfg), "-p", "위 입력에 답하세요.", "-o", "json", "-e", "none",
           "--allowed-mcp-server-names", "none", "--approval-mode", "default", "--skip-trust"]
    if cfg.get("llm", {}).get("gemini_model"):
        cmd += ["-m", cfg["llm"]["gemini_model"]]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, cwd=work, env={**os.environ, "GEMINI_SYSTEM_MD": str(sys_md)},
                       creationflags=subprocess.CREATE_NO_WINDOW)
    out = (r.stdout or "").strip()
    try:
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
    except ValueError:
        data = {}
    answer = (data.get("response") or "").strip()
    if r.returncode != 0 or not answer:
        err = json.dumps(data.get("error"), ensure_ascii=False) if data.get("error") else (r.stderr or out)
        if any(k in err.lower() for k in ("login", "auth", "credential")):
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
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                       timeout=timeout, cwd=DATA_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"claude 호출 실패: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout.strip()
