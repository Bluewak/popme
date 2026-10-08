"""구독 중인 AI의 CLI를 헤드리스로 호출한다. config [llm] provider로 고른다.

- "claude" (기본): Claude Pro/Max 구독의 claude CLI (claude -p)
- "codex": ChatGPT Plus/Pro 구독의 Codex CLI (codex exec)

어느 쪽이든 도구(셸·파일·웹·MCP)를 전부 끄고 요약·답변만 하게 한다.
수집된 SNS 글이 무엇을 시키든 실행할 수단이 없다.
"""
import glob
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
    return {"claude": "Claude", "codex": "ChatGPT"}.get(provider(cfg), provider(cfg))


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


def ask(cfg, system: str, prompt: str, timeout: int = 600) -> str:
    if provider(cfg) == "codex":
        return _ask_codex(cfg, system, prompt, timeout)
    cmd = [find_claude(cfg), "-p", "--tools", "", "--system-prompt", system,
           "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"]
    if cfg.get("llm", {}).get("model"):
        cmd += ["--model", cfg["llm"]["model"]]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                       timeout=timeout, cwd=DATA_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"claude 호출 실패: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout.strip()
