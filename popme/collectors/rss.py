"""RSS/Atom 수집 — 로그인 없이 공식 변경내역·블로그·커뮤니티 새 글을 가져온다."""
import gzip
import html
import logging
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

log = logging.getLogger(__name__)
ATOM = "{http://www.w3.org/2005/Atom}"
# 일반 개발 커뮤니티(GeekNews·HN 인기)에서 AI 관련 글만 브리핑에 넘길 때 (피드 topic = "ai", popme/briefing.py).
# 제목 + 요약 앞부분에서 찾는다. 「개발」「모델」「API」처럼 넓은 말은 넣지 않는다 — 커뮤니티 글 대부분에 걸린다.
# 짧은 영문 약어는 앞뒤가 영문자가 아닐 때만 (html의 ml, said의 ai는 안 걸리고 「AI가」는 걸리게)
AI_TOPIC = re.compile(
    r"(?<![A-Za-z])(AI|A\.I\.|AGI|LLMs?|GPT|ML|RAG|MCP|SLM|xAI)(?![A-Za-z])|Muse Spark|"
    r"ChatGPT|OpenAI|Anthropic|Claude|Gemini|DeepMind|Llama|Mistral|Qwen|DeepSeek|Grok|Copilot|Codex|Cursor|"
    r"Hugging ?Face|agent|language model|reasoning model|open.?weights?|inference|fine.?tun|prompt|embedding|"
    r"vector (db|database|search)|transformer|neural|diffusion|machine learning|deep learning|"
    r"인공지능|에이전트|에이젼트|언어 ?모델|생성형|프롬프트|바이브 ?코딩|머신 ?러닝|딥 ?러닝|클로드|제미나이|코덱스|챗GPT|"
    r"오픈AI|앤트로픽|하네스|추론 ?모델|임베딩|벡터 ?(DB|검색)|파인 ?튜닝|멀티모달", re.I)


# 데이터 사이언스 (topic = "data"). 짧은 약어는 대소문자 구분 — re.I로 묶으면 BI가 bi-에 걸린다.
# 「회귀·실험·예측·인과」는 뒤에 붙는 말과 함께만 (회귀 테스트·Xbox Labs 실험 같은 오탐 방지). 2026-10 직군 조사에서 실제 피드로 다듬음
DATA_ACR = re.compile(r"(?<![A-Za-z])(A/?B ?[Tt]est\w*|SQL|ETL|ELT|KPI|EDA|MLOps|OLAP|CUPED|SHAP|ARIMA|dbt|BI)(?![A-Za-z])")
DATA_TOPIC = re.compile(
    r"data ?scien|data analy|analytics engineer|product analytics|statistic|causal|experimentation|online experiment|"
    r"controlled experiment|bayesian|regression|forecasting|time.?series|anomaly detection|recommender|recommendation|"
    r"learning to rank|uplift|propensity|survival analysis|hypothesis test|p.?values?|confidence interval|sample size|"
    r"variance reduction|treatment effect|pandas|polars|duckdb|jupyter|marimo|scikit|sklearn|xgboost|lightgbm|statsmodels|"
    r"pymc|tabular|apache spark|pyspark|spark (sql|connect)|snowflake|bigquery|databricks|redshift|data warehouse|lakehouse|"
    r"iceberg|parquet|dashboard|semantic layer|feature store|mlflow|tableau|looker|power ?bi|"
    r"데이터 ?(분석|과학|사이언|엔지니어|웨어하우스|파이프라인|품질|플랫폼|레이크)|통계|분석가|(A/?B|AB) ?(테스트|실험)|"
    r"실험 ?(설계|플랫폼|분석|결과)|인과 ?(추론|효과|관계)|추천 ?(시스템|모델|알고리즘)|개인화|예측 ?(모델|모형)|수요 ?예측|"
    r"회귀 ?(분석|모형|모델)|베이지안|시계열|지표|대시보드|이상 ?탐지|코호트|리텐션|퍼널|피처 ?(스토어|엔지니어링)|"
    r"분석 ?(에이전트|리포트|플랫폼)", re.I)
# ML 엔지니어링 (topic = "ml") — 서빙·학습·GPU. LLM은 대문자만 (Simon Willison의 llm-gemini 같은 플러그인 이름 오탐 방지)
ML_TOPIC = re.compile(
    r"(?<![A-Za-z])((?-i:LLMs?)|GPUs?|TPUs?|NPUs?|CUDA|ROCm|ML|MLOps|MoE|LoRA|QLoRA|RLHF|RLVR|DPO|GRPO|FP8|FP4|NVFP4|MXFP4|"
    r"GGUF|AWQ|GPTQ|vLLM|SGLang|TensorRT|TRT-LLM|NCCL|SFT|VLM|H100|H200|B200|B300|GB200|GB300|MI3\d\d|HBM|MLX|ONNX)(?![A-Za-z])|"
    r"PyTorch|JAX|Triton|Hugging ?Face|llama\.cpp|Ollama|Unsloth|DeepSpeed|Megatron|FlashAttention|FlashInfer|"
    r"inference|serving|quantiz|distill|fine.?tun|pre.?train|post.?train|reinforcement learning|KV.?cache|speculative decod|"
    r"embedding|open.?weights?|model weights|interpretab|Qwen|DeepSeek|Llama|Mistral|Gemma|Nemotron|Kimi|GLM-?\d|"
    r"Blackwell|Rubin|Trainium|Inferentia|Nvidia|machine learning|deep learning|neural|transformer|diffusion|leaderboard|"
    r"양자화|증류|파인 ?튜닝|사전 ?학습|사후 ?학습|강화 ?학습|추가 ?학습|학습 데이터|데이터셋|벤치마크|리더보드|"
    r"오픈 ?웨이트|가중치|엔비디아|그래픽 ?카드|딥 ?러닝|머신 ?러닝|매개변수|파라미터|임베딩|추론 ?(속도|비용|엔진|서버|최적화)|"
    r"서빙|모델 ?(공개|출시)|언어 ?모델|멀티모달|온디바이스|신경망|해석 ?가능", re.I)
# 피드 topic 값 → 판별 규칙들 (하나라도 걸리면 통과). "ai+data"처럼 +로 묶는다
TOPICS = {"ai": [AI_TOPIC], "data": [DATA_TOPIC, DATA_ACR], "ml": [ML_TOPIC]}


def _text(item):
    return f"{item.get('title') or ''} {(item.get('summary') or '')[:300]}"


def is_ai(item):
    return bool(AI_TOPIC.search(_text(item)))


def matches(item, topic):
    """피드의 topic("ai", "data", "ml", "ai+data" …)에 맞는 글인가. topic이 없으면 늘 참, 모르는 이름은 무시."""
    names = [t for t in (topic or "").split("+") if t.strip()]
    pats = [p for t in names for p in TOPICS.get(t.strip(), [])]
    if not pats:
        return True
    text = _text(item)
    return any(p.search(text) for p in pats)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) POPME/0.1"


def _clean(s, limit):
    s = re.sub(r"<[^>]+>", " ", html.unescape(s or ""))
    s = re.sub(r"\s+", " ", s).strip()
    return s[:limit]


def _date(s):
    if not s:
        return None
    try:
        d = parsedate_to_datetime(s)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc).isoformat()


def fetch(feed):
    req = urllib.request.Request(feed["url"], headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read()
    if data[:2] == b"\x1f\x8b":  # 묻지 않아도 gzip으로 주는 곳이 있다 (Google DeepMind 블로그 등) — urllib은 안 풀어 줌
        data = gzip.decompress(data)
    root = ET.fromstring(data)
    limit = int(feed.get("max_chars", 500))
    base = {"source": feed["name"], "grp": feed.get("group", "")}
    skip = re.compile(feed["skip"], re.I) if feed.get("skip") else None  # 예: 매일 나오는 nightly 릴리스
    items = []
    for it in root.iter("item"):  # RSS 2.0
        link = (it.findtext("link") or "").strip()
        if link:
            items.append({**base, "url": link, "title": _clean(it.findtext("title"), 200),
                          "summary": _clean(it.findtext("description"), limit),
                          "published": _date(it.findtext("pubDate"))})
    for e in root.iter(f"{ATOM}entry"):  # Atom
        link_el = e.find(f"{ATOM}link[@rel='alternate']")
        if link_el is None:  # Element는 자식이 없으면 falsy라 `or`로 이으면 안 됨
            link_el = e.find(f"{ATOM}link")
        link = link_el.get("href") if link_el is not None else ""
        if link:
            body = e.findtext(f"{ATOM}content") or e.findtext(f"{ATOM}summary")
            items.append({**base, "url": link, "title": _clean(e.findtext(f"{ATOM}title"), 200),
                          "summary": _clean(body, limit),
                          "published": _date(e.findtext(f"{ATOM}published") or e.findtext(f"{ATOM}updated"))})
    return [i for i in items if not (skip and skip.search(i["title"]))]


def collect(cfg, progress=lambda s: None):
    out, errors = [], []
    for feed in cfg.get("rss", []):
        progress(f"RSS: {feed['name']}")
        try:
            out += fetch(feed)
        except Exception as e:
            log.warning("RSS 실패 %s: %s", feed["name"], e)
            errors.append(f"{feed['name']}: {e}")
    return out, errors
