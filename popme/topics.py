"""판별 규칙: 이 글이 그 관심사 이야기인가.

피드의 topic("ai", "data", "ml", "ent", "rag", "ai+data"처럼 +로 묶음)에 맞는 글만 브리핑에 넘길 때 쓴다
(popme/briefing.py rss_items). 제목 + 요약 앞부분에서 찾는다. 규칙은 2026-10 직군별 조사에서 실제 피드에 돌려 오탐을 고친 것.
"""
import re

# 일반 개발 커뮤니티(GeekNews·HN 인기)에서 AI 관련 글만 브리핑에 넘길 때 (topic = "ai").
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
# 엔터프라이즈 도입·FDE (topic = "ent") — 단종·가격·리전·규제·보안·국내 클라우드/LLM. 짧은 약어는 대소문자 구분
ENT_ACR = re.compile(r"(?<![A-Za-z])(SSO|SAML|SCIM|RBAC|ABAC|DLP|PII|VPC|SLA|SOC ?2|ISO ?(27001|42001)|HIPAA|GDPR|FedRAMP|CSAP|"
                     r"ISMS(-P)?|PTU|RAG|MCP|OCR|PoC|CVE-\d{4}-\d+)(?![A-Za-z])")
ENT_TOPIC = re.compile(
    r"enterprise|on.?prem|air.?gap|self.?host|private (link|endpoint|connectivity|deployment)|data residency|sovereign|"
    r"complian|audit log|governance|guardrail|prompt injection|jailbreak|red.?team|exfiltrat|"
    r"deprecat|retire|sunset|end of life|price (cut|increase)|pricing|rate limit|provisioned throughput|batch api|outage|"
    r"Bedrock|Azure OpenAI|Foundry|Vertex AI|Gemini Enterprise|AgentCore|Copilot Studio|Agent 365|ChatGPT (Enterprise|Business|Work)|"
    r"Claude (Enterprise|for Work)|connector|retrieval|eval(s|uation)?\b|LLM.as.a?.?judge|document (AI|intelligence|parsing|understanding)|"
    r"EU AI Act|AI Act|"
    r"엔터프라이즈|기업용|사내|도입 ?(사례|기업)|구축형|온프레미스|망 ?분리|폐쇄망|개인 ?정보|가명 ?정보|비식별|데이터 ?(주권|거주)|소버린|"
    r"공공 ?(부문|기관|클라우드|AI)|금융 ?(권|사|AI|보안)|보안 ?(인증|취약점|사고|권고|업데이트)|(정보|데이터|기밀|개인정보|키) ?유출|해킹|"
    r"컴플라이언스|규제|가이드라인|AI ?기본법|고영향|인공지능 ?기본법|개인정보위|금융위|금감원|레드 ?팀|프롬프트 ?인젝션|가드레일|단종|"
    r"지원 ?종료|API ?(요금|가격)|요금제|가격 ?(인상|인하)|장애|"
    r"하이퍼 ?클로바|HyperCLOVA|EXAONE|엑사원|Solar (Pro|Open)|업스테이지|Upstage|Kanana|카나나|믿:음|Mi:dm|에이닷|A\.X|"
    r"네이버 ?클라우드|NAVER Cloud|KT ?클라우드|NHN ?클라우드|독자 ?(AI|파운데이션)|국가대표 ?AI", re.I)
# LLM 앱·RAG (topic = "rag") — 검색 스택·문서 파싱·평가·에이전트 보안. 일반 'agent'·'AI'는 넣지 않는다 (그건 "ai")
RAG_ACR = re.compile(r"(?<![A-Za-z])(RAG|GraphRAG|BM25|OCR|MCP|HWPX?|ColBERT|ColPali|FAISS|LLM-as-a?-judge)(?![A-Za-z])")
RAG_TOPIC = re.compile(
    r"retriev|re-?rank|embedding|vector ?(search|database|db|store|index)|semantic search|hybrid search|full.?text search|"
    r"late interaction|chunking|knowledge graph|knowledge base|"
    r"langchain|langgraph|langsmith|llama.?index|dspy|haystack|pgvector|qdrant|milvus|weaviate|pinecone|chromadb|elasticsearch|"
    r"opensearch|vespa|docling|unstructured\.io|document (parsing|ai|intelligence|extraction|understanding)|pdf pars|table extraction|"
    r"ragas|langfuse|llm eval|agent eval|evals?\b.{0,20}(llm|agent|rag)|(llm|agent|rag).{0,20}evals?\b|(LLM|agent|AI) observability|"
    r"guardrail|prompt injection|structured output|json schema|function calling|tool (calling|use)|model context protocol|"
    r"context engineering|agent memory|semantic cach|text.?to.?sql|hallucinat|grounding|"
    r"검색 ?증강|리랭(커|킹)|재순위|임베딩|벡터 ?(DB|검색|데이터베이스|스토어|저장소)|하이브리드 ?검색|시맨틱 ?검색|의미 ?검색|청킹|"
    r"문서 ?(파싱|파서|인식|추출|이해|AI|검색)|한글 ?문서|한컴|아래아한글|표 ?(추출|인식)|지식 ?(그래프|베이스)|"
    r"프롬프트 ?인젝션|가드레일|구조화(된)? ?출력|함수 ?호출|(툴|도구) ?호출|에이전트 ?(메모리|평가|설계|아키텍처|프레임워크|구축)|"
    r"(LLM|에이전트|RAG) ?평가|평가 ?(지표|데이터셋)|환각|할루시네이션|컨텍스트 ?엔지니어링|사내 ?(문서|지식|데이터)|챗봇|"
    r"랭체인|랭그래프|라마 ?인덱스", re.I)

# topic 이름 → 판별 규칙들 (하나라도 걸리면 통과)
TOPICS = {"ai": [AI_TOPIC], "data": [DATA_TOPIC, DATA_ACR], "ml": [ML_TOPIC], "ent": [ENT_TOPIC, ENT_ACR],
          "rag": [RAG_TOPIC, RAG_ACR]}


def _text(item):
    return f"{item.get('title') or ''} {(item.get('summary') or '')[:300]}"


def is_ai(item):
    return bool(AI_TOPIC.search(_text(item)))


def matches(item, topic):
    """topic("ai", "data", "ml", "ent", "rag", "ai+data" …)에 맞는 글인가. topic이 없으면 늘 참, 모르는 이름은 무시."""
    names = [t for t in (topic or "").split("+") if t.strip()]
    pats = [p for t in names for p in TOPICS.get(t.strip(), [])]
    if not pats:
        return True
    text = _text(item)
    return any(p.search(text) for p in pats)
