"""관심사 질문 틀 시험 세트 (docs/INTERESTS-DESIGN.md 4-6) — 실제 AI로 분야 15개 × 보는 방식을 돌려 질문이 틀대로 나오는지 본다.

질문 틀(popme/interests.py TYPES·SLOTS)이나 지시문(QUESTIONS)을 고칠 때 손으로 돌린다. CI에는 넣지 않는다 (AI 구독을 쓴다).
    python tools/interest_eval.py                 # 15개 × 업계 흐름·대상 위주 (30번 부름, 10~15분)
    python tools/interest_eval.py 축구 반도체      # 고른 분야만
    python tools/interest_eval.py --lens both 와인
결과: 분야마다 유형·칸별 질문·보기 수, 그리고 문제(칸 순서·보기 부족·AI 실패)를 모아 끝에 보여 준다.
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from popme import config, interests  # noqa: E402

FIELDS = ["축구", "야구", "반도체", "K-pop", "부동산", "미국주식", "게임", "와인", "육아", "기후",
          "웹툰", "전기차", "우주", "커피", "여행"]
# 분야마다 기대하는 유형 (여럿이면 어느 것이든 통과 — 와인은 취미로도 산업으로도 볼 수 있다)
EXPECT = {"축구": ("스포츠",), "야구": ("스포츠",), "반도체": ("산업·기업",), "K-pop": ("엔터·문화",),
          "부동산": ("투자·시장",), "미국주식": ("투자·시장",), "게임": ("엔터·문화", "산업·기업", "기술·제품"),
          "와인": ("취미·생활", "산업·기업"), "육아": ("취미·생활",), "웹툰": ("엔터·문화",),
          "전기차": ("기술·제품", "산업·기업"), "커피": ("취미·생활", "산업·기업"), "여행": ("취미·생활",)}


def check(field, lens, out):
    """문제 목록 (빈 목록이면 통과)."""
    problems = []
    if out.get("error"):
        problems.append(f"AI 실패: {out['error']}")
    slots = [q["slot"] for q in out["questions"]]
    if slots != interests.SLOTS[lens]:
        problems.append(f"칸 순서 {slots} (틀: {interests.SLOTS[lens]})")
    for q in out["questions"]:
        n = len(q["options"]) + sum(len(v) for v in q["options_by"].values())
        if n < 3:
            problems.append(f"「{q['slot']}」 보기 {n}개 — 고정 질문이 됐거나 지어낸 보기가 걸러졌음")
    if field in EXPECT and out["type"] not in EXPECT[field]:
        problems.append(f"유형 {out['type']} (기대: {'·'.join(EXPECT[field])})")
    if not out.get("headlines"):
        problems.append("최근 헤드라인 없음 (조사 실패)")
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("fields", nargs="*", default=FIELDS)
    ap.add_argument("--lens", choices=list(interests.LENSES), action="append")
    args = ap.parse_args()
    cfg = config.load()
    lenses = args.lens or ["overview", "target"]
    report = []
    for field in args.fields:
        for lens in lenses:
            t = time.time()
            out = interests.questions(cfg, field, lens)
            took = time.time() - t
            problems = check(field, lens, out)
            print(f"\n== {field} · {interests.LENSES[lens]} — {out['type']}, {took:.0f}초, 헤드라인 {len(out.get('headlines', []))}개")
            for q in out["questions"]:
                print(f"  [{q['slot']}] {q['text']}  {[o['label'] for o in q['options']]}")
                for k, v in q["options_by"].items():
                    print(f"      {k} → {[o['label'] for o in v]}")
            for p in problems:
                print(f"  ! {p}")
            report.append((field, lens, problems))
    bad = [(f, lens_, p) for f, lens_, p in report if p]
    print(f"\n통과 {len(report) - len(bad)}/{len(report)}")
    for f, lens_, p in bad:
        print(f"  {f} · {interests.LENSES[lens_]}: " + " / ".join(p))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
