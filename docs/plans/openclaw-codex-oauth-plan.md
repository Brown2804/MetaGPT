# MetaGPT × OpenClaw-style Codex OAuth 적용 계획서

작성일: 2026-03-22
대상 리포지토리: `/home/khj12/.openclaw/workspace/research/MetaGPT`
기준 커밋: `11cdf466d042aece04fc6cfd13b28e1a70341b1f`

---

## 1. 목표

MetaGPT에 **OpenClaw의 OpenAI Codex OAuth 적용 방식**을 이식해,
사용자가 ChatGPT/Codex OAuth로 로그인한 뒤 MetaGPT에서 별도 OpenAI API key 없이
Codex 계열 모델을 사용할 수 있게 한다.

핵심 목표:

1. **Codex OAuth 로그인/토큰 저장/갱신**을 MetaGPT 내부에서 지원
2. `metagpt`의 기존 LLM 추상화(`LLMConfig` → provider registry → provider class)를 최대한 유지
3. 기존 OpenAI API key 경로를 깨지 않고, **Codex OAuth를 별도 provider path**로 추가
4. RAG/embedding 경로는 분리해서 설계해, Codex OAuth의 한계를 전체 시스템 장애로 전파하지 않도록 함

---

## 2. 조사 범위

이번 1차 점검에서 확인한 핵심 파일:

### 설정 / 초기화
- `metagpt/config2.py`
- `metagpt/configs/llm_config.py`
- `metagpt/configs/models_config.py`
- `metagpt/context.py`
- `metagpt/context_mixin.py`
- `metagpt/software_company.py`
- `setup.py`

### LLM provider 계층
- `metagpt/provider/llm_provider_registry.py`
- `metagpt/provider/base_llm.py`
- `metagpt/provider/openai_api.py`
- `metagpt/provider/azure_openai_api.py`
- `metagpt/provider/anthropic_api.py`

### embedding / RAG 경로
- `metagpt/configs/embedding_config.py`
- `metagpt/rag/factories/embedding.py`
- `metagpt/utils/embedding.py`
- `metagpt/learn/text_to_embedding.py`
- `metagpt/ext/stanford_town/utils/utils.py`

### OpenClaw 참고 문서
- `openclaw-upgrade-2026.3.13/docs/concepts/oauth.md`
- `openclaw-upgrade-2026.3.13/docs/providers/openai.md`
- `openclaw-upgrade-2026.3.13/docs/concepts/model-providers.md`

---

## 3. MetaGPT 현재 구조 요약

## 3.1 LLM 설정 진입점

MetaGPT의 기본 설정은 `Config.default()`가 로드한다.

- 기본 파일:
  - repo local: `config/config2.yaml`
  - user local: `~/.metagpt/config2.yaml`
- 주 LLM 설정은 `Config.llm: LLMConfig`
- 추가 role/model별 설정은 `models:` 블록을 `ModelsConfig.default()`로 읽는다

즉, 현재 MetaGPT는 기본적으로 **정적 설정 파일 기반**이다.

## 3.2 provider 생성 흐름

현재 흐름은 아래와 같다.

1. `Config.llm` 또는 `ModelsConfig`에서 `LLMConfig` 생성
2. `Context.llm()` / `Context.llm_with_cost_manager_from_llm_config()` 호출
3. `create_llm_instance(config)` 실행
4. `LLMType`에 매핑된 provider class 생성

즉, **provider registry 중심 구조**라서 새 provider type 추가가 비교적 쉽다.

## 3.3 OpenAI 경로

`metagpt/provider/openai_api.py`는 `AsyncOpenAI(api_key=..., base_url=...)`를 사용한다.
즉 현재 OpenAI 계열 provider는 다음 가정을 깔고 있다.

- 요청은 OpenAI-compatible endpoint로 보낼 수 있다
- 인증은 `api_key` 문자열 하나로 해결된다
- chat/completions 계열 호출이 가능하다

이 구조는 **OpenAI API key 기반**에는 잘 맞지만,
**Codex OAuth처럼 access token + refresh token + accountId + expiry**가 있는 인증 모델과는 직접 맞물리지 않는다.

---

## 4. OpenClaw 방식에서 가져와야 할 핵심 요소

OpenClaw의 OAuth 문서 기준으로, MetaGPT에 필요한 핵심만 뽑으면 다음과 같다.

### 4.1 Token sink

OpenClaw는 OAuth credential을 한 군데(`auth-profiles.json`)로 모아 관리한다.
이 방식의 장점:

- 토큰 갱신 위치가 단일화됨
- 다중 계정 지원이 쉬움
- CLI/앱 간 refresh token 충돌을 줄일 수 있음

MetaGPT도 동일하게 **중앙 auth store**를 가져가는 게 맞다.

### 4.2 PKCE 기반 로그인

OpenClaw는 Codex OAuth에서 다음 흐름을 사용한다.

1. PKCE verifier/challenge 생성
2. authorize URL 오픈
3. localhost callback 시도 (`127.0.0.1:1455/auth/callback`)
4. 실패 시 manual paste fallback
5. token exchange
6. `{access, refresh, expires, accountId}` 저장

MetaGPT도 **같은 흐름**을 가져와야 한다.

### 4.3 자동 refresh + 파일 락

OpenClaw는 만료 시 자동 refresh를 수행하고,
동시 refresh 충돌 방지를 위해 file lock을 둔다.

MetaGPT도 장기 실행/다중 role/다중 action 구조라서
**provider 호출 시 토큰 만료 검사 + refresh under lock**이 필요하다.

### 4.4 다중 프로필

OpenClaw는 provider 내 여러 profile을 지원한다.
MetaGPT도 추후 아래 시나리오가 필요해질 수 있다.

- 개인 계정 / 팀 계정 분리
- 특정 role만 다른 Codex 계정 사용
- quota 소진 시 profile rotation

초기 1차 구현에서는 단일 profile만 지원해도 되지만,
데이터 구조는 **멀티 profile 확장 가능**하게 잡아야 한다.

---

## 5. 가장 중요한 설계 판단

## 5.1 “OpenAI provider에 억지로 끼우기”는 비추천

겉으로 보기엔 `api_key` 대신 access token을 넣으면 될 것 같지만,
Codex OAuth는 단순 API key 모델이 아니다.

문제점:

- refresh token 관리 필요
- token expiry 처리 필요
- provider별 entitlement 차이 가능
- transport/endpoint semantics가 일반 OpenAI API와 다를 수 있음

따라서 **기존 `LLMType.OPENAI` 경로를 건드리기보다, 별도 provider를 추가**하는 게 안전하다.

## 5.2 권장 방향: `openai_codex` 별도 provider 추가

권장안:

- `LLMType.OPENAI_CODEX = "openai_codex"` 추가
- `CodexOAuthLLM` provider 추가
- auth는 전용 auth manager가 해결
- MetaGPT의 role/action 쪽은 기존 `LLMConfig` 흐름 유지

이렇게 하면:

- 기존 OpenAI API key 사용자 안 깨짐
- role별 모델 선택(`models:`) 유지 가능
- 이후 `openai_codex/gpt-5.4` 같은 named config도 쉽게 확장 가능

---

## 6. 권장 아키텍처

## 6.1 새 모듈 구성

권장 신규 디렉터리:

```text
metagpt/auth/
  __init__.py
  models.py              # Auth profile / token schema
  store.py               # auth-profiles.json read/write
  lock.py                # file lock helper
  pkce.py                # verifier/challenge/state
  codex_oauth.py         # login / refresh / token exchange
  resolver.py            # profile -> valid access token
```

권장 신규 provider:

```text
metagpt/provider/codex_oauth_api.py
```

권장 CLI 확장:

```text
metagpt/management/auth_cli.py
```

---

## 6.2 저장소 설계

권장 저장 파일:

```text
~/.metagpt/auth/auth-profiles.json
```

초기 스키마 초안:

```json
{
  "profiles": {
    "openai-codex:default": {
      "provider": "openai-codex",
      "mode": "oauth",
      "account_id": "...",
      "access_token": "...",
      "refresh_token": "...",
      "expires_at": "2026-03-22T12:34:56Z",
      "meta": {
        "source": "metagpt",
        "label": "default"
      }
    }
  },
  "order": {
    "openai-codex": ["openai-codex:default"]
  }
}
```

추가로 import-only fallback:

- `~/.codex/auth.json` 이 있으면 읽어서 MetaGPT auth store로 import 가능하게 설계

---

## 6.3 LLMConfig 확장안

현재 `LLMConfig`는 정적 `api_key/base_url/model` 중심이다.
Codex OAuth를 자연스럽게 넣으려면 아래 필드를 추가하는 안이 적절하다.

```python
class LLMConfig(...):
    auth_mode: Literal["api_key", "oauth_profile"] = "api_key"
    auth_profile: Optional[str] = None
```

예시:

```yaml
llm:
  api_type: "openai_codex"
  model: "gpt-5.4"
  auth_mode: "oauth_profile"
  auth_profile: "openai-codex:default"
```

장점:

- API key provider / OAuth provider를 같은 `LLMConfig` 틀 안에서 다룸
- role별 override, `models:` named config와 잘 맞음
- secret을 YAML 파일에 넣지 않아도 됨

---

## 7. provider 설계

## 7.1 1차 구현 목표

1차 구현에서는 **MetaGPT가 Codex OAuth로 chat/completion 계열을 수행하는 것**까지만 목표로 잡는다.

범위 포함:
- text chat
- stream
- role/action 경로
- `models:` 기반 role-specific config

범위 제외(1차):
- embedding provider로 Codex 직접 사용
- RAG 전체 자동 통합
- quota-aware multi-account rotation
- websocket warmup / advanced transport tuning

## 7.2 `CodexOAuthLLM` 책임

`CodexOAuthLLM`은 다음만 책임진다.

- `LLMConfig` 해석
- auth profile에서 유효 access token 확보
- 필요 시 refresh
- Codex backend 호출
- MetaGPT `BaseLLM` 인터페이스에 맞게 응답 반환

중요: provider 내부에서 auth 파일을 직접 읽지 말고,
항상 **auth resolver**를 통해 access token을 받게 해야 한다.

---

## 8. endpoint/transport 전략

여기서 구현 난이도가 갈린다.

### 전략 A — 네이티브 Codex provider (권장 최종형)

MetaGPT 내부에서 Codex OAuth backend를 직접 호출한다.

장점:
- OpenClaw 방식과 가장 유사
- 외부 프록시 의존 없음
- 사용자 입장에서는 `metagpt auth login` 후 바로 사용 가능

단점:
- Python 쪽에서 Codex backend protocol을 따로 구현해야 함
- refresh + streaming + 오류 변환을 직접 처리해야 함

### 전략 B — 내부 로컬 브리지(provider-owned local bridge)

MetaGPT가 내부적으로 로컬 OpenAI-compatible bridge를 띄우고,
기존 `OpenAILLM`를 거의 그대로 재사용한다.

장점:
- `OpenAILLM` 재사용폭이 큼
- chat/completions path 변경이 적음

단점:
- 구조가 두 겹이 됨
- 디버깅 포인트 증가
- “네이티브 OAuth provider” 느낌이 약함

### 권장 판단

**계획서 기준 권장안은 전략 A**다.
다만 구현 난이도/리스크가 크면 **전략 B를 Phase 0 브릿지**로 먼저 두고,
안정화 후 전략 A로 내부화해도 된다.

---

## 9. Embedding / RAG 전략

이 부분이 매우 중요하다.

MetaGPT는 embedding을 별도 경로로 다룬다.

핵심 파일:
- `metagpt/rag/factories/embedding.py`
- `metagpt/utils/embedding.py`
- `metagpt/learn/text_to_embedding.py`
- `metagpt/ext/stanford_town/utils/utils.py`

현재 구조상 RAG는 기본적으로 다음을 기대한다.

- `embedding.api_type`가 있거나
- 없으면 `llm.api_type in [openai, azure]`일 때 `llm.api_key/base_url` fallback

즉, `llm.api_type = openai_codex`가 되면
**기존 fallback 규칙이 깨질 수 있다.**

### 권장 원칙

Codex OAuth는 **LLM 전용**으로 보고,
embedding은 반드시 별도 설정으로 분리한다.

예시:

```yaml
llm:
  api_type: "openai_codex"
  model: "gpt-5.4"
  auth_mode: "oauth_profile"
  auth_profile: "openai-codex:default"

embedding:
  api_type: "ollama"
  base_url: "http://127.0.0.1:11434"
  model: "mxbai-embed-large"
  dimensions: 1024
```

또는:

```yaml
embedding:
  api_type: "openai"
  api_key: "<separate real api key>"
  base_url: "https://api.openai.com/v1"
  model: "text-embedding-3-large"
  dimensions: 3072
```

### 추가 수정 필요

1. `RAGEmbeddingFactory._resolve_embedding_type()`가 `openai_codex`를 fallback으로 보지 않게 명시
2. `utils/embedding.py` / `learn/text_to_embedding.py` / `ext/stanford_town/utils/utils.py`처럼
   **직접 OpenAI embedding을 가정하는 코드 경로**를 식별하고 guard를 추가
3. `openai_codex` 사용 시 embedding 미설정이면, RAG feature entrypoint에서 친절한 오류 메시지 제공

---

## 10. 구현 단계 제안

## Phase 0 — 설계 안정화

- [ ] `LLMType.OPENAI_CODEX` 추가 여부 최종 결정
- [ ] auth storage path / schema 확정
- [ ] native provider vs internal bridge 최종 결정
- [ ] embedding 분리 정책 확정

산출물:
- 이 계획서
- config schema draft

## Phase 1 — 인증 기반 구축

- [ ] `metagpt/auth/` 추가
- [ ] PKCE helper 구현
- [ ] login callback/manual paste flow 구현
- [ ] token exchange / refresh 구현
- [ ] file lock 기반 refresh 보호
- [ ] `~/.codex/auth.json` import helper 구현
- [ ] `metagpt auth login/status/import/logout` CLI 추가

성공 기준:
- OAuth 로그인 완료 후 `auth-profiles.json`에 profile 저장
- 만료 임박/만료 상태에서 refresh 성공

## Phase 2 — LLM provider 통합

- [ ] `LLMType.OPENAI_CODEX` 추가
- [ ] `CodexOAuthLLM` provider 추가
- [ ] registry 등록
- [ ] `LLMConfig.auth_mode`, `auth_profile` 추가
- [ ] `Config`/`ModelsConfig` load path 검증
- [ ] 기본 text completion / streaming 동작 검증

성공 기준:
- `metagpt "Create a 2048 game"`가 Codex OAuth profile로 기본 수행 가능
- role/action level override에서도 동작

## Phase 3 — Embedding/RAG 안전화

- [ ] `openai_codex` + RAG 시 explicit embedding required 정책 구현
- [ ] embedding fallback 로직 보정
- [ ] legacy direct OpenAI embedding 호출 코드 정리 또는 차단

성공 기준:
- Codex OAuth만 설정한 환경에서 RAG 관련 에러가 예측 가능하게 발생
- 별도 embedding config를 주면 RAG 정상 동작

## Phase 4 — UX/운영 개선

- [ ] multi-profile 지원
- [ ] preferred profile order
- [ ] quota/429 대응 전략
- [ ] better diagnostics (`metagpt auth doctor`)
- [ ] optional bridge transport fallback

---

## 11. 변경 후보 파일 목록

### 설정/모델
- `metagpt/configs/llm_config.py`
- `metagpt/config2.py`
- `metagpt/configs/models_config.py`
- `config/config2.example.yaml`
- `README.md`
- `docs/FAQ-EN.md`

### 인증 신규
- `metagpt/auth/*` (신규)
- `metagpt/management/auth_cli.py` (신규)

### provider 신규/수정
- `metagpt/provider/llm_provider_registry.py`
- `metagpt/provider/openai_api.py` (필요 시 공통화)
- `metagpt/provider/codex_oauth_api.py` (신규)

### embedding/RAG
- `metagpt/rag/factories/embedding.py`
- `metagpt/utils/embedding.py`
- `metagpt/learn/text_to_embedding.py`
- `metagpt/ext/stanford_town/utils/utils.py`

### CLI
- `metagpt/software_company.py`
- `setup.py`

### 테스트
- `tests/metagpt/provider/`
- `tests/metagpt/test_config.py`
- `tests/metagpt/rag/factories/`
- 신규: `tests/metagpt/auth/`

---

## 12. 주요 리스크

## 12.1 가장 큰 리스크: backend protocol 차이

MetaGPT는 현재 OpenAI Python SDK + chat completions 가정을 많이 깔고 있다.
Codex OAuth backend가 OpenAI public API와 완전히 같지 않다면,
단순 `api_key/base_url` 치환만으로는 부족할 수 있다.

대응:
- provider를 별도 분리
- phase별 검증
- 필요 시 bridge fallback 준비

## 12.2 embedding 혼선

Codex OAuth가 chat path는 되더라도 embedding path는 안정적이지 않을 수 있다.

대응:
- embedding 별도 provider를 강제
- fallback 규칙 수정

## 12.3 레거시 direct OpenAI 호출 누락

`ext/stanford_town/utils/utils.py`처럼 registry/provider abstraction을 우회하는 코드가 존재한다.
이런 경로는 나중에 통합 실패 포인트가 된다.

대응:
- 1차 릴리즈 범위를 core path로 한정
- 우회 경로는 “unsupported until migrated”로 명시

## 12.4 토큰 보안

현재 MetaGPT는 YAML에 `api_key`를 두는 패턴이 강하다.
OAuth 토큰을 동일한 방식으로 두면 보안이 크게 나빠진다.

대응:
- OAuth 토큰은 YAML 금지
- auth store 단일화
- 최소한 파일 권한 제한 + 민감 필드 masking

---

## 13. 검증 계획

## 13.1 인증 검증

- `metagpt auth login --provider openai-codex`
- `metagpt auth status`
- expired token fixture로 refresh 테스트
- lock 경합 테스트 (동시 refresh)

## 13.2 provider 검증

- 단일 prompt
- stream on/off
- tool/function call이 필요한 action 경로
- role별 `models:` override 경로

## 13.3 실제 워크플로 검증

- `metagpt "Create a 2048 game"`
- incremental mode
- `Action.llm_name_or_type` override path
- AFLOW/SPO에서 `ModelsConfig.default().get()` 경유하는 경로

## 13.4 RAG 검증

- `llm=openai_codex`, `embedding=ollama`
- `llm=openai_codex`, `embedding=openai(api key)`
- embedding 미설정 시 expected error

---

## 14. 추천 구현 순서 (실행 우선순위)

1. **Auth subsystem 먼저**
2. `LLMType.OPENAI_CODEX` + `CodexOAuthLLM` 추가
3. `software_company.py`에 auth CLI 묶기
4. 예제 config/README 업데이트
5. RAG/embedding 분리 정책 반영
6. 레거시 direct OpenAI 경로 정리

이 순서가 좋은 이유:
- 인증과 provider가 먼저 안정돼야 실제 호출을 검증할 수 있음
- embedding/RAG는 그 다음 분리해도 core path를 막지 않음

---

## 15. 현재 시점 결론

현 구조를 봤을 때 MetaGPT는

- provider registry가 분리돼 있고
- `base_url`/`api_key`/`model` 기반 OpenAI-compatible 경로를 이미 지원하며
- `models:`를 통한 role별 override도 있어서

**Codex OAuth 통합을 넣기 좋은 편**이다.

다만 단순한 `base_url` 바꿔치기 수준이 아니라,
**OpenClaw처럼 인증을 1급 구성요소로 올리는 작업**이 필요하다.

즉, 가장 현실적인 방향은:

- MetaGPT에 **전용 auth store + PKCE login + refresh manager**를 추가하고
- `openai_codex`를 **별도 provider**로 도입하며
- embedding은 별도 provider로 분리하는 것.

이 방향이 가장 안전하고, 이후 유지보수도 쉽다.

---

## 16. 다음 단계 제안

다음 작업은 아래 순서로 진행하는 것이 적절하다.

1. **설계 결정 고정**
   - native provider vs bridge fallback
   - auth file schema
   - config field 추가안 확정
2. **스캐폴딩 구현**
   - `metagpt/auth/`
   - `LLMType.OPENAI_CODEX`
   - `metagpt auth ...` CLI
3. **provider 연결**
4. **README + example config + tests**

원하면 다음 턴에서 바로

- 이 계획서를 바탕으로
- **구현 착수용 TODO를 더 잘게 쪼개고**
- **Phase 1 스캐폴딩 코드부터 실제로 패치** 들어갈 수 있다.
