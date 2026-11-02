"""설정 창 [저장] 대신: 사람이 아무것도 안 바꾸고 저장을 누른 것과 같은 경로 (setup_window.Api.save와 같은 함수들)."""
import tomllib

from popme import config, setup_window as sw

cur = sw.current()
ch = {k: cur[k] for k in ("provider", "plan", "tools", "source", "ics", "holidays", "use_windows_location", "character", "place")}
ch.update(briefing_after="23:59", mail=None)
sw.write(sw.apply(sw.base_text(), sw.validate(ch)))
saved = tomllib.loads(config.CONFIG_PATH.read_text(encoding="utf-8"))
print("설정 창 캐릭터 목록:", [c["file"] for c in cur["characters"]])
print("config.toml [character] file =", saved["character"]["file"])
assert saved["character"]["file"] == "mine.toml", "옮긴 내 캐릭터가 설정 창에서 선택돼 있어야 한다"
