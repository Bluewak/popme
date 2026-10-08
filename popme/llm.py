"""Claude Pro 구독의 claude CLI를 헤드리스(-p)로 호출한다.

도구는 전부 끄고(--tools ""), MCP도 끄고, 시스템 프롬프트를 짧게 바꿔서
요약·답변만 하게 한다. 수집된 SNS 글이 무엇을 시키든 실행할 수단이 없다.
"""
import glob
import os
import re
import shutil
import subprocess

from popme.config import DATA_DIR


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


def ask(cfg, system: str, prompt: str, timeout: int = 600) -> str:
    cmd = [find_claude(cfg), "-p", "--tools", "", "--system-prompt", system,
           "--strict-mcp-config", "--no-session-persistence", "--output-format", "text"]
    if cfg.get("llm", {}).get("model"):
        cmd += ["--model", cfg["llm"]["model"]]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                       timeout=timeout, cwd=DATA_DIR, creationflags=subprocess.CREATE_NO_WINDOW)
    if r.returncode != 0:
        raise RuntimeError(f"claude 호출 실패: {(r.stderr or r.stdout).strip()[:500]}")
    return r.stdout.strip()
