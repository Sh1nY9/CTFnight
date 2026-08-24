# CTFnight Backend

FastAPI, SQLAlchemy 2, PostgreSQL과 Redis로 구현한 CTFnight control plane이다. 공개·접근
코드 가입, 참가자 moderation과 팀 소유권·멤버 관리를 포함하며 challenge workload나 Docker
socket을 직접 다루지 않는다. 전체 배포는
[상위 README](../README.md), 신뢰 경계는 [ARCHITECTURE.md](../ARCHITECTURE.md),
운영 보안 정책은 [SECURITY.md](../SECURITY.md)를 따른다.

Python package 이름 `alpha-ctf-backend`, `alpha` namespace와 `ALPHA_*` 설정명은 기존
코드·설치와의 내부 호환 계약이다.

상위 project의 운영 security gate에는 Linux `util-linux`의 `flock`가 필요하다.
scanner, `make up`, challenge import와 관리자 복구는 `security-reports/` directory inode의
같은 `flock -n`을 사용한다. scanner는 전체 gate, `make up`은 최종 preverify부터
up·postverify까지, one-off는 preverify부터 DB mutation 종료까지 잠금을 유지하며, 이미
잠겼으면 기다리지 않고 실패한다.

## 컨테이너 실행 계약

canonical Compose는 database 권한을 다음 순서로 분리한다.

1. PostgreSQL이 owner credential로 기동하고 healthy가 된다.
2. one-shot `db-roles`가 PostgreSQL image를 재사용해 migrator·runtime role을 구성하고 exit 0한다.
3. one-shot `migrate`가 backend image와 migrator credential로 `alembic upgrade head`,
   `python -m alpha.cli bootstrap`을 실행하고 exit 0한다.
4. backend `entrypoint.sh`는 runtime credential로
   `uvicorn alpha.main:app --host 0.0.0.0 --port 8000`만 실행한다.

상시 5개 service와 one-shot 2개를 합친 canonical graph는 7개다. `db-roles`→PostgreSQL,
`migrate`→backend alias를 사용하므로 고유한 검사 image artifact는 5개다.

새 DB의 bootstrap은 이름 `CTFnight`, slug `ctfnight`, 상태 `draft`인 current event를
만든다. 관리자 설정을 검토하기 전에는 외부 가입과 문제 공개가 열리지 않는다.
production에서 관리자가 하나도 없으면 관리자 email과 file-backed 초기 비밀번호가
필수다. 초기 관리자는 비밀번호를 변경하기 전까지 관리자 API를 사용할 수 없다.

## 배포 모드와 설정

canonical Compose에서는 `scripts/generate-env.sh`가 주소에 맞는 설정을 만들고
`scripts/validate-env.sh`가 기동 전에 검사한다.

| 주소 | `ALPHA_ENVIRONMENT` | cookie |
|---|---|---|
| `http://localhost`, `http://127.0.0.1` | `development` | 개발용 이름, Secure 없음; Caddy host `127.0.0.1` bind |
| DNS 이름 또는 HTTPS origin | `production` | Secure `__Host-*`; Caddy host `0.0.0.0` bind |

production은 공개 평문 HTTP, SQLite, `memory://` Redis, wildcard trusted host,
비-HTTPS allowed origin, Secure가 아닌 cookie를 거부한다. API 문서
`/api/docs`, `/api/redoc`, `/api/openapi.json`은 development/test에서만 열린다.

주요 비밀이 아닌 설정은 다음과 같다.

| 설정 | 설명 |
|---|---|
| `ALPHA_ENVIRONMENT` | `development`, `test`, `production` |
| `ALPHA_COOKIE_SECURE` | production에서는 반드시 true |
| `ALPHA_ALLOWED_ORIGINS` | credentialed browser 요청의 정확한 origin 목록 |
| `ALPHA_TRUSTED_HOSTS` | Host allowlist; production wildcard 금지 |
| `ALPHA_FORWARDED_ALLOW_IPS` | 신뢰할 proxy 주소; Compose에서는 Caddy `172.31.250.2` |
| `ALPHA_SESSION_TTL_HOURS` | session 수명, 1~2160시간 |
| `ALPHA_SESSION_CLEANUP_BATCH_SIZE` | 새 session 생성 시 만료 행 정리 상한, 1~1000 |
| `ALPHA_CSRF_TTL_SECONDS` | CSRF token TTL, 60~86400초 |
| `ALPHA_MAX_REQUEST_BODY_BYTES` | ASGI request body 상한, 1 KiB~10 MiB |
| `ALPHA_MAX_FLAG_LENGTH` | flag 정의·제출 길이, 16~4096 |
| `ALPHA_REGEX_TIMEOUT_SECONDS` | regex flag 실행 timeout |
| `ALPHA_SUBMISSION_*` | team·source IP·challenge 제출 rate limit |
| `ALPHA_TEAM_MUTATION_*` | 팀 변경 session·source IP rate limit; 기본 20/시간·100/시간 |
| `ALPHA_AUTH_*` | identity 20회·source IP 200회/60초 인증 rate limit 기본값 |
| `ALPHA_REGISTRATION_GLOBAL_*` | 전체 등록 1,000회/60초 Redis budget 기본값 |
| `ALPHA_SCOREBOARD_CACHE_SECONDS` | 점수판 Redis cache TTL |
| `ALPHA_ADMIN_EMAIL`, `ALPHA_ADMIN_USERNAME` | bootstrap 관리자 식별자 |
| `ALPHA_SEED_DEMO` | 명시적으로 true일 때만 demo data 생성 |

`GET /api/v1/meta`의 `limits`는 현재 server가 강제하는
`max_flag_length`, `max_request_body_bytes`,
`max_submissions_per_team_challenge=1000`,
`max_submissions_per_team_event=10000`, `max_members_per_team=100`,
`max_participant_users=100000`,
`max_active_sessions_per_user=10`, `max_public_scoreboard_entries=1000`을 공개한다. client
표시는 이 값을 참고할 수 있지만 최종 권한과 cap 검사는 backend transaction이다.

## file-backed 비밀값

운영 비밀값은 `.env`에 두지 않는다. 상위 `.secrets/` 파일을 Compose가 서비스별
`/run/secrets/*`로 mount한다. production backend는 runtime 세 경로만 받고,
`migrate` one-shot이 bootstrap admin과 migrator DB 경로를 추가로 받는다.

| 설정 | 기본 Compose mount | 용도 |
|---|---|---|
| `ALPHA_SECRET_KEY_FILE` | `/run/secrets/alpha_secret_key` | session·CSRF·invite·등록 코드·flag HMAC root key |
| `ALPHA_DATABASE_PASSWORD_FILE` | `/run/secrets/postgres_runtime_password` | backend CRUD 인증 |
| `ALPHA_REDIS_PASSWORD_FILE` | `/run/secrets/redis_password` | Redis 인증 |
| `ALPHA_ADMIN_PASSWORD_FILE` | migrate의 `/run/secrets/admin_password` | bootstrap·복구 비밀번호 |

reader는 `O_NOFOLLOW`를 사용하고 일반 파일, UTF-8 한 줄, 최대 16 KiB를 확인한다.
관리자 bootstrap을 마친 뒤에도 migrate용 admin file은 남기되 내용을 비우고 `.env`의
`ALPHA_ADMIN_BOOTSTRAPPED=true` marker와 함께 상위 validator로 확인한다. backend에는
이 파일과 관련 환경 변수가 mount되지 않는다. marker는 Compose service 환경이나 build
argument로도 전달되지 않는 운영 복구 상태다. deployment source digest는 정확히 한 개의
`true`/`false` marker 값만 `<operational-marker>`로 정규화하므로 복구 중 marker 전환은
허용하지만, 그 밖의 `.env` drift는 그대로 거부한다.

모든 Compose service는 UID/GID `65532:65532`다. rootful Linux Docker host의 POSIX ACL로
secret owner에게 `rw-`, UID 65532에게 `r--`만 허용하며 `acl` package의 `setfacl`·`getfacl`이
필수다. 수동 교체나 backup 복구 뒤에는 상위 `make secret-acl`을 실행한다. rootless Docker의
subordinate UID mapping은 이 계약과 달라 현재 지원하지 않으며 별도 read·volume 검증이
필요하다.

root key를 바꾸면 기존 session·invite·등록 접근 코드·exact flag HMAC을 더 이상 검증할 수 없다. DB를
복구하거나 replica를 교체할 때 같은 시점의 secret file을 함께 사용해야 한다.

## PostgreSQL과 Redis

기본 Compose는 `database`, `cache`라는 별도 `internal: true` network에서 local
PostgreSQL·Redis에 연결한다. local 평문 transport 예외는 production에서 host 이름이
정확히 `postgres`, `redis`인 경우에만 허용된다.

component 방식의 database 설정은 host, port, name, user와 password file을 모두
제공해야 한다. 외부 PostgreSQL에서는 `ALPHA_DATABASE_TLS=true`가 필수이며 backend가
`sslmode=verify-full` URL을 만든다. 외부 Redis에서는 host와 password file에 더해
`ALPHA_REDIS_TLS=true`가 필수이며 `rediss://`를 사용한다.

기본 Compose의 backend에는 외부 egress가 없고 local store `depends_on`과 local
PostgreSQL backup을 사용한다. 외부 managed store는 canonical Compose·validator·security
graph의 reviewed code change로 egress, CA trust·hostname 검증, dependency health와
공급자별 backup/restore를 함께 바꿔야 한다. 임의 Compose override는 지원하지 않는다.

Redis는 rate limit과 짧은 cache에만 사용한다. Redis 장애 때 제출 rate limit을 우회하지
않고 요청을 실패시킨다. canonical 이벤트·점수·감사 데이터는 PostgreSQL에 있다.

SQLite는 development/test 전용이지만 foreign key를 선택적으로 완화하지 않는다. 모든
runtime DB-API connection에서 `PRAGMA foreign_keys=ON`을 설정하고 값이 `1`인지 즉시 확인하며,
활성화하지 못하면 연결을 fail-closed한다. Alembic online migration connection도 같은
검사를 사용하고 migration 전후 각각 `PRAGMA foreign_key_check`를 실행해 violation이 있으면
실패한다.

## 인증과 CSRF

비밀번호는 Argon2id로 저장한다. session 원문은 browser cookie에만 두고 DB에는
domain-separated HMAC, 만료 시각과 `credential_version`을 저장한다.

Argon2 hash/verify는 process-wide nonblocking 2-slot semaphore로 제한한다. 두 slot이
포화되면 request worker를 대기시키지 않고 즉시 HTTP 503
`password_service_busy`와 `Retry-After: 1`을 반환한다. 가입·로그인·비밀번호 변경은
필요한 DB snapshot을 읽은 뒤 Argon2 전에 transaction을 rollback한다. 계산 성공 후
event 또는 사용자 행을 다시 lock하고 최신 상태·password hash·session을 재검증하므로
비싼 계산 동안 DB connection이나 row lock을 점유하지 않는다.

production의 session, CSRF, anonymous browser cookie 이름은 각각
`__Host-alpha_session`, `__Host-alpha_csrf`, `__Host-alpha_browser`다. 모두 Secure,
HttpOnly, SameSite=Lax, Path=/이며 Domain 속성이 없다.

`GET /api/v1/auth/csrf`는 HttpOnly CSRF cookie를 설정하고 동일 token을 JSON으로
반환한다. client는 모든 POST·PUT·DELETE 요청에 `X-CSRF-Token`을 보낸다. middleware는
cookie/header의 constant-time equality, 서명, TTL과 context를 검사한다. context는
현재 session token 또는 익명 browser token의 domain-separated HMAC이므로 다른 session에
token을 재사용할 수 없다. 로그인·비밀번호 변경·로그아웃은 context를 회전한다.

User와 Session의 `credential_version`이 일치해야 인증된다. 비밀번호 변경과 CLI 복구는
version을 증가시키고 session 행을 삭제하므로 경합 중 남은 이전 session도 거부된다.
만료 session은 로그인마다 전체 scan하지 않고 index와 제한된 batch,
PostgreSQL `SKIP LOCKED`를 이용해 정리한다.

`PUT /api/v1/admin/users/{user_id}/status`는 participant만 정지·재활성화한다. 정지에는 500자
이내 사유가 필요하고 User row lock 아래 `credential_version`을 증가시킨 뒤 모든 session을
삭제한다. 같은 상태 요청은 새 audit/outbox를 만들지 않는 idempotent 동작이고, 재활성화도
이전 session을 복원하지 않는다. 제출·비밀번호 변경·팀 mutation은 최종 쓰기 전에 User를
잠그고 active, version과 필요한 session을 재검증한다. 정지가 먼저 commit되면 초기
`require_user` snapshot으로 진행하던 요청도 401 `invalid_session`으로 거부되고, 요청이 User
lock을 먼저 얻으면 그 transaction이 정지보다 먼저 직렬화된다.

새 session은 해당 사용자의 최신 active session 9개만 남긴 뒤 insert하므로 사용자당
active session은 10개다. 성공한 새 로그인은 가장 오래된 active token부터 폐기한다.
비밀번호 변경은 회전하는 session token 기반 identity key 외에 stable user-ID 기반 scope도
별도로 검사한다. 두 scope 모두 기본 identity 20회/60초, source IP 200회/60초이며 Redis
오류는 503 `rate_limit_unavailable`, 상한은 429 `authentication_rate_limited`로
fail-closed한다.

등록은 위 identity·IP key와 별도로 `auth:register:global` 1,000회/60초 budget을 모든
client가 공유한다. Argon2 뒤에는 current Event를 exclusive lock하고 등록 상태·중복 identity·
participant count를 다시 검사한 후 insert한다. participant role User 100,000명에 도달하거나
concurrent N+1 요청이면 409 `participant_capacity_reached`이며 User·Session·audit·outbox를
추가하지 않는다.

Event의 `registration_access_mode`는 기본 `open` 또는 `code`다. `code`이면 관리자
`POST /api/v1/admin/registration-codes`가 만든 24-byte 무작위 접근 코드가 필요하다. DB에는
`registration-access` domain HMAC, label, `max_uses`(1~10,000 또는 `null` 무제한),
`use_count`, 선택적 `expires_at`, active/revoked 상태만 저장한다. 평문은 생성 응답의
`access_code`에 한 번만 포함되고 GET 목록·audit·outbox에는 없다. DELETE는 코드를 폐기하며
다시 활성화하지 않는다.

등록은 rate limit 뒤 Argon2 전에 code를 확인하고, Argon2 뒤 Event→RegistrationCode lock을
잡아 mode·만료·사용량·폐기를 다시 확인한다. 코드 사용량, User·Session·audit·outbox는 한
transaction이므로 실패한 등록은 사용량을 남기지 않고 concurrent 사용도 상한을 넘지 않는다.
누락·미등록·만료·소진·폐기 사유는 모두 403 `registration_access_denied`와 같은 message로
응답한다. 운영자는 최소 하나의 코드를 생성한 뒤 mode를 `code`로 바꾼다.

인증 rate tier는 scope별 identity 뒤 IP를 검사하고, 등록만 마지막에 global key를 검사한다.
팀 변경은 session→IP, 제출은 team→IP→challenge 순이다. 각 경로는 첫 deny에서 즉시 멈추므로
좁은 identity·session·team bucket이 거부한 요청은 뒤의 공유 IP와 global·challenge budget을
소비하지 않는다. 어느 tier의 store 오류도 503 `rate_limit_unavailable`이다.

`auth.login`, `auth.logout`, `auth.password_changed`의 새 반복 occurrence는 User lock 아래
사용자·action별 최신 AuditEvent에 병합된다. metadata의 `occurrences`, `first_seen_at`,
`last_seen_at`이 전체 횟수와 구간을 보존한다. `user.password_changed` Outbox는 같은 user의
`delivered_at IS NULL`인 최신 행에 `occurrences`를 누적하고, 이미 전달됐거나 다른 중요한
기록은 덮어쓰지 않는다.

## 요청·응답 방어

- 모든 `/api/*` 응답은 `Cache-Control: no-store, private`와 browser security header를
  포함한다.
- CORS는 exact origin과 credentialed method/header만 허용한다.
- Trusted Host 검사는 backend endpoint 전에 수행한다.
- 전역 ASGI middleware가 Content-Length와 chunked body를 endpoint 실행 전에 제한한다.
- error 응답은 machine-readable code, 사용자용 message와 request ID만 반환한다.
- 운영 log와 API에는 password, token, flag 원문 또는 내부 exception 원문을 넣지 않는다.
- 관리자 endpoint는 session role과 초기 비밀번호 변경 여부를 server에서 검사한다.

## 제출과 점수 transaction

- exact flag는 domain-separated HMAC으로만 저장·비교한다.
- regex flag는 입력 길이 제한과 실행 timeout을 적용한다.
- 제출, solve, score event, audit/outbox는 하나의 DB transaction이다.
- 오답 Submission은 저장하되 `submission.incorrect` OutboxEvent는 만들지 않는다.
- `(team_id, challenge_id)` unique constraint가 중복 solve를 최종 차단한다.
- lock 순서는 `Event → User → Membership → Team → Challenge`다.
- Redis rate 검사는 `team → IP → challenge` 순으로 Event shared lock 뒤, 대기 가능한
  User/Membership/Team row lock보다 먼저 수행한다. 통과한 뒤 Membership을 다시 잠가 같은
  팀인지 재검증한다.
- 정답 후보만 Challenge row를 잠근 뒤 최신 flag로 다시 검증한다.
- 오답 commit은 공개 점수판 cache를 유지하고 정답 commit만 `live`, `frozen`, `final`
  cache를 무효화한다.
- 동적 solve의 ScoreEvent는 solve 시점 점수를 기록하고 과거 행을 bulk rewrite하지 않는다.
  공개 점수판은 현재 phase에 보이는 solve 수로 문제 점수를 다시 계산한다.
- frozen 뒤의 공개 점수판은 `freeze_at` 이후 solve를 숨기고 ended에서 최종 결과를 연다.

공개 점수판은 전체 Solve ORM 행을 Python으로 읽지 않고 SQL에서 문제별 solve count와 팀별
점수·solve 수·마지막 solve 시각을 aggregate한다. 정렬된 hard top 1,000개만 `entries`로
반환하고 `total_entries`, `truncated`로 전체 항목 수와 잘림을 표시한다.

cold build는 event·phase별 45초 Redis lease로 single-flight한다. lease는 무작위 owner token이
일치할 때만 해제된다. cache의 `{generation, payload}` envelope와 build 전후 generation
검사는 정답 solve 또는 점수판 관련 admin mutation과 경합한 stale 결과의 게시를 막는다.
cache·lease 조작 오류는 503 `scoreboard_unavailable`, cold cache lease loser는 503
`scoreboard_busy`와 `Retry-After: 1`, build 중 generation 변경은 503 `scoreboard_changed`로
fail-closed한다.

`limits.py`의 다음 lifetime·응답 hard cap은 환경 설정으로 완화할 수 없다.

| 범위 | 상한 | 오류 |
|---|---:|---|
| 팀·문제 Submission(`max_attempts=0`) | 1,000 | 409 `submission_storage_limit_reached` |
| 팀·이벤트 전체 Submission | 10,000 | 409 `submission_storage_limit_reached` |
| 팀 구성원 | 100 | 409 `team_capacity_reached` |
| 사용자·이벤트 팀 구성 변경 | 100 | 409 `team_mutation_limit_reached` |
| 전체 participant User | 100,000 | 409 `participant_capacity_reached` |
| 사용자별 active SessionToken | 10 | 새 session에서 oldest active token 폐기 |
| 공개 점수판 `entries` | 1,000 | `total_entries`와 `truncated=true`로 잘림 표시 |

문제 `max_attempts`가 1~1,000이면 그 설정 상한에 409 `attempt_limit_reached`를 반환한다.
0은 문제 설정상 별도 제한이 없다는 뜻이지만 시스템 1,000 cap을 해제하지 않는다. submission
count는 Team row lock 아래에서 idempotency 재확인 후 강제하므로 concurrent 요청도 상한을
넘지 않는다. 기존 idempotency key replay는 count와 cap 검사 전에 원래 결과를 반환하고 새
행을 만들지 않는다.

팀 가입도 Team row lock 아래 현재 Membership 수를 세므로 concurrent 가입이 100명 상한을
넘지 않는다.

팀 생성·가입·초대 회전·소유권 이전·멤버 제거·탈퇴는 Redis에서 기본 session 20회/시간 뒤
source IP 100회/시간을 검사한다. Redis 오류에는 503 `rate_limit_unavailable`, 단기 상한에는
429 `team_mutation_rate_limited`로 write를 허용하지 않는다. User row lock 아래 보존된 팀 변경
AuditEvent를 세어 100회 lifetime cap의 concurrent 우회도 막는다.

`POST /api/v1/teams/transfer-owner`와 `/remove-member`는 등록 기간의 팀전에서 owner만 같은
팀의 일반 member를 대상으로 실행한다. 새 owner는 active participant여야 한다. actor·target
User와 Membership, Team을 고정 순서로 잠그고 권한·session version을 다시 확인한다. 해당
팀에서 Submission을 만든 사용자는 오답만 있어도 leave/remove가 거부되어 team hopping으로
시도 제한을 초기화할 수 없다. 제거 성공 시 Membership 삭제와 invite HMAC 회전을 한
transaction으로 commit하며, 새 `invite_code` 평문은 응답에 한 번만 포함되고 audit/outbox에는
저장하지 않는다.

Outbox는 현재 transaction journal과 관리자 조회용이다. 외부 delivery worker, retry와
delivery SLA는 구현되어 있지 않다. 인증 audit 집계와 미전달 password Outbox 병합은 공개
반복 경로가 매번 새 행을 만들지 않게 하지만, 정답 solve·중요 팀 변경·관리자 audit/outbox와
이미 전달된 기록은 보존한다. 자동 전체 audit/outbox pruning은 없고 이 cap들도 전체 event
disk quota가 아니므로 운영자는 PostgreSQL volume과 backup을 감시하고 archived event 뒤
새 database를 사용한다.

## 보안·저장 상한 migration

`20260824_0002`는 users와 sessions에 non-null `credential_version`을 함께 추가하고
기존 행을 0으로 유지한다. 새 코드의 session 검증과 하나의 보안 계약이므로 구·신
backend revision을 rolling으로 혼합하지 않는다.

`20260824_0003`은 기존 challenge의 `max_attempts > 1000`을 1,000으로 정규화하고
PostgreSQL CHECK와 SQLite INSERT/UPDATE trigger로 동등한 `max_attempts <= 1000` 상한을
추가한다. 새 submission runtime과 함께 배포해야 하므로 rolling으로 혼합하지 않는다.

`20260824_0004`는 events에 기본 `open`인 `registration_access_mode`와
`registration_codes` table을 추가한다. PostgreSQL CHECK 또는 SQLite trigger가 mode를
`open|code`로 제한하고 code table의 HMAC 길이, label, 사용량·상한, 폐기 상태도 DB 제약으로
검증한다. 새 등록 runtime과 같은 maintenance/all-at-once release로 배포한다.

운영에서는 upgrade 직전 age 암호화 백업과 image를 준비한 뒤 maintenance/all-at-once로
배포한다.

```sh
set -eu
app_root=$(git rev-parse --show-toplevel)
cd "$app_root"
make backup
make build
./scripts/compose.sh stop caddy frontend backend
make up
make smoke
```

schema 적용 뒤 code만 이전 revision으로 되돌리지 않는다. 실패 시 이전 source/image와
upgrade 직전 암호화 backup을 함께 복구한다.

`scripts/compose.sh`의 직접 호출은 위의 `stop`·`run`이나 진단용 `config`·`exec` 같은 보조
작업에만 사용한다. image build는 전체 scan을 수행하는 `make build`, service start는 유효한
deployment manifest를 검증하고 필요할 때 전체 gate를 다시 실행하는 `make up` 또는
`make wait`로 수행한다. `make up`은 같은 directory-inode lock 안에서 final preverify,
`up --no-build --remove-orphans`, postverify를 연속 수행한다. postverify는 project의 orphan을
포함한 모든 container의 Compose service label을 확인해 canonical 7개가 정확히 하나씩
존재하는지 검사하고 unknown orphan·duplicate·누락을 거부한다.

## 관리자 비밀번호 복구

복구값을 command argument, shell history나 `.env`에 넣지 않는다. 전용 helper는 먼저
canonical env와 2시간 이내 deployment manifest의 source·rendered graph·canonical tag→exact
image ID binding을 검증하고 Caddy/backend를 멈춘 뒤 bootstrap marker를 잠시 `false`로
바꾼다. 보안 편집기가 쓴 admin secret에 exact ACL을 재적용하고 env를 검증한 다음, 같은
artifact binding을 `migrate` 실행 직전에 다시 검증한다. 그 뒤에만 admin secret과 migrator
credential을 가진 `migrate` service로 `--pull never` one-off CLI를 실행한다. 같은
directory-inode lock은 첫 preverify부터 DB mutation과 canonical secret·marker 복구가 끝날
때까지 유지한다. 성공·실패·signal 모두에서 secret을 비우고 marker를 `true`로 복원하며,
backend에는 admin secret을 mount하지 않는다.

```sh
set -eu
app_root=$(git rev-parse --show-toplevel)
cd "$app_root"
./scripts/recover-admin-password.sh admin@example.com
make smoke
```

복구는 대상 사용자의 `credential_version`을 증가시키고 모든 기존 session을 폐기하며,
다음 로그인에서 다시 비밀번호 변경을 요구한다. helper를 사용하지 않는 수동 절차도 반드시
시작 전과 `migrate` 실행 직전에 동일한 `verify-prestart`를 수행하고
`marker false → secret 작성/ACL → env 검증·재검증 → migrate CLI → secret 비움/marker true
→ ACL/up` 순서를 지켜야 한다. `ALPHA_ADMIN_BOOTSTRAPPED=true`에서 nonempty secret을 둔
상태는 validator가 의도적으로 거부한다.

## challenge-as-code

상위 Make target은 private YAML을 장기 실행 backend에 mount하지 않고 one-off
container의 표준 입력으로 전달한다. 실행 직전 `verify-prestart`가 2시간 freshness, 현재
source·rendered graph·canonical tag와 검사한 exact image ID binding을 확인하며, manifest가
없거나 stale·tampered·drift 상태이면 DB mutation을 시작하지 않는다. 같은 directory-inode
lock을 preverify부터 one-off 종료까지 유지하고 Compose run에 `--pull never`를 강제해 검증
뒤 같은 canonical 운영 경로의 retag·manifest 폐기나 registry pull로 image가 바뀌는 경로를
차단한다.

```sh
set -eu
cd "$(git rev-parse --show-toplevel)"
make import-challenge CHALLENGE=challenges/welcome/challenge.yaml
```

형식은 [challenge schema](../templates/challenge.schema.json)와
[template 설명](../templates/README.md)을 따른다. exact flag 원문은 import transaction
안에서 HMAC으로 바뀐 뒤 폐기된다. `FLAG{...}`는 문서 예시일 뿐 engine은 특정 접두사를
강제하지 않는다.

직접 CLI를 사용하는 개발 환경에서는 파일 또는 표준 입력을 선택할 수 있다.

```sh
set -eu
python -m alpha.cli import-challenge /path/to/challenge.yaml
python -m alpha.cli import-challenge - < /path/to/private-challenge.yaml
```

## lock 기반 로컬 개발

Python 3.12 virtual environment에 검토된 hash lock만 설치한다.

```sh
cd "$(git rev-parse --show-toplevel)/backend"
python3.12 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-bootstrap.lock
.venv/bin/python -m pip install --require-hashes -r requirements-build.lock
.venv/bin/python -m pip install --require-hashes -r requirements-test.lock
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
```

SQLite와 `memory://`는 development/test 전용이다.

```sh
.venv/bin/alembic upgrade head
.venv/bin/python -m alpha.cli bootstrap
.venv/bin/uvicorn alpha.main:app --reload
```

## 검증

```sh
cd "$(git rev-parse --show-toplevel)"
python3 scripts/check-backend-locks.py

cd backend
.venv/bin/pytest --cov=alpha
.venv/bin/ruff format --check src tests alembic
.venv/bin/ruff check src tests alembic
.venv/bin/alembic check
```

실제 PostgreSQL lock·migration 회귀 검사는 temporary schema를 만들고 지울 권한이 있는
격리된 test database에서만 실행한다.

```sh
ALPHA_TEST_POSTGRES_URL='postgresql+psycopg://test-user@localhost/test-db' \
  .venv/bin/pytest tests/test_migrations.py tests/test_postgresql_concurrency.py
```

변수를 지정하지 않으면 PostgreSQL 전용 test는 skip된다. 운영 database나 참가자
database를 test URL로 사용하지 않는다.

liveness `/api/v1/health/live`는 저장소 I/O 없이 공개된다. PostgreSQL·Redis를 실제
조회하는 readiness `/api/v1/health/ready`는 backend direct endpoint이며 Caddy의 active
health 전용이다. Caddy를 통한 public readiness 요청은 의도적으로 404를 반환한다.
`make smoke`는 public live를 기다린 뒤 이 404 경계도 확인한다.
