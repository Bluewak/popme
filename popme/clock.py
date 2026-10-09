"""시간 기준: PC(Windows)의 시간대를 따른다.

Windows '표준 시간대 자동 설정'이 켜져 있으면 위치에 맞춰 시간대가 바뀌고 앱도 따라간다
(한국에선 한국 시간, 해외에선 현지 시간). 날씨 예보만은 예보 장소의 현지 날짜로 받는다 (weather.py).
Windows의 파이썬은 시작할 때 시간대를 읽으므로, 실행 중에 시간대가 바뀌면 앱을 다시 켜야 반영된다.
DB에는 언제나 UTC로 저장하고, 보여줄 때·'오늘'을 셀 때만 이 시간대를 쓴다.
"""
from datetime import datetime


def tz():
    """지금 PC 시간대 (테스트는 이 함수를 바꿔 시간대를 고정한다)."""
    return datetime.now().astimezone().tzinfo


def now():
    return datetime.now(tz())


def today():
    return now().date()
