"""AI CLI 호출 (popme/llm.py): 시간이 넘으면 손자 프로세스까지 끝내기, Gemini 시스템 프롬프트 파일은 호출마다 따로."""
import sys
import time

import pytest

from popme import llm


@pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe → 손자 프로세스 구조는 Windows")
def test_timeout_kills_grandchild(tmp_path):
    """.cmd가 띄운 손자 프로세스가 출력을 쥐고 있어도 시간 안에 끝난다 (예전엔 손자가 끝날 때까지 멈췄다)."""
    slow = tmp_path / "slow.cmd"
    slow.write_text(f'@"{sys.executable}" -c "import time; time.sleep(60)"\r\n', encoding="utf-8")
    t = time.time()
    with pytest.raises(llm.subprocess.TimeoutExpired):
        llm._run([str(slow)], "", 2, str(tmp_path))
    assert time.time() - t < 20


def test_gemini_system_file_per_call(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "DATA_DIR", tmp_path)
    monkeypatch.setattr(llm, "find_gemini", lambda cfg: "gemini")
    seen = []

    def fake_run(cmd, input, timeout, cwd, env=None):
        path = env["GEMINI_SYSTEM_MD"]
        seen.append((path, open(path, encoding="utf-8").read()))
        return llm.subprocess.CompletedProcess(cmd, 0, '{"response": "ok"}', "")
    monkeypatch.setattr(llm, "_run", fake_run)
    assert llm.ask({"llm": {"provider": "gemini"}}, "지시 A", "p") == "ok"
    assert llm.ask({"llm": {"provider": "gemini"}}, "지시 B", "p") == "ok"
    (a, ta), (b, tb) = seen
    assert a != b and (ta, tb) == ("지시 A", "지시 B")
    assert not list((tmp_path / "llm-empty-gemini" / ".gemini").glob("system-*.md"))


def test_login_and_limit_words():
    assert llm.is_login("Invalid API key · Please run /login") and llm.is_login("401 Unauthorized")
    assert not llm.is_login("KeyError: 'author'") and not llm.is_login("id 14012")
