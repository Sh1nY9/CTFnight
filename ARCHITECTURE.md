# CTFnight 아키텍처

이 문서는 현재 checkout의 코드와 `compose.yaml`이 구현하는 경계를 설명한다. 제품의
운영 절차는 [README.md](README.md), 보안 기준은 [SECURITY.md](SECURITY.md), backend
세부 설정은 [backend/README.md](backend/README.md)를 함께 따른다.

`Alpha`, Python `alpha` namespace, `ALPHA_*` 설정명과 `alpha:*` browser event는 기존
설치와 코드의 호환 식별자다. 사용자 표시 제품명은 `CTFnight`, 새 데이터베이스의 기본
이벤트 slug는 `ctfnight`다.

현재 배포·scanner·CI의 검증 platform은 rootful Linux Docker x86_64이며 Compose의 모든
서비스는 `linux/amd64`로 고정한다. application·edge·cache·one-shot은 UID/GID
`65532:65532`다. PostgreSQL만 고정 Chainguard image의 초기화 계약에 따라 `0:0` entrypoint가
exact capability로 volume을 준비한 뒤 server PID 1을 내장 `70:70`으로 낮춘다. 다른
architecture는 별도 build·CVE·동시성 회귀 검증 없이는 지원 범위로 간주하지 않는다.
rootless Docker와 user namespace remap은 host UID mapping과 volume ownership 계약이 달라
현재 지원하지 않는다.
운영 host에는 Linux `util-linux`의 `flock`가 필요하다. scanner, `make up`, challenge import와
관리자 복구는 `security-reports/` directory inode의 같은 비차단 `flock -n`을 사용한다.
scanner는 전체 gate, `make up`은 최종 preverify→up→postverify, one-off는 preverify→DB
mutation 전 구간을 직렬화해 같은 project의 report·canonical image tag·manifest와 실행을
묶으며 경합 시 즉시 실패한다.

## 범위

CTFnight는 배포당 하나의 Jeopardy형 current event를 운영하는 control plane이다.
공개·접근 코드 가입, 로그인, 참가자 정지·복구, 개인전·팀전, 팀 소유권 이전·멤버 제거,
공지, 문제, exact·regex flag, 고정·동적 점수, 제출 제한, 점수판 동결, 관리자 감사 조회와
challenge-as-code import를 제공한다.

Attack–Defense, 팀별 challenge container, attachment upload, Kubernetes runtime broker와
Docker socket 제어는 포함하지 않는다. 취약한 challenge workload는 이 Compose project와
다른 host·network·domain에 둔다.

## 구성 요소와 요청 흐름

```text
Browser
   |
   | TCP 80/443, UDP 443
   v
custom Caddy edge  ── web(internal) ──> nginx frontend:8080
   |
   └──────── api(internal) ──────────> FastAPI backend:8000
                                            |             |
                                  database(internal)  cache(internal)
                                            |             |
                                       PostgreSQL       Redis
```

| 구성 요소 | 책임 | 영구 상태 |
|---|---|---|
| custom Caddy | 공개 TLS, HTTP→HTTPS, 경로 분기, 보안 header, body/timeout 제한 | `caddy_data` |
| nginx frontend | non-root 8080 정적 SPA, immutable asset cache, HTML `no-store` | 없음 |
| FastAPI backend | 인증, 권한, 대회 규칙, transaction, API | 없음 |
| `db-roles` one-shot | owner credential로 migrator·runtime DB role 구성 | 없음, exited(0) |
| `migrate` one-shot | backend image와 migrator credential로 migration·bootstrap | 없음, exited(0) |
| PostgreSQL | canonical 사용자·이벤트·문제·제출·점수·감사 데이터 | `postgres_data` |
| Redis | rate limit, 짧은 점수판 cache | `redis_data` |

Compose dependency chain은 PostgreSQL healthy → `db-roles` exited(0) → `migrate` exited(0) →
backend healthy 순이다. 나머지 5개 service는 상시 실행한다.

모든 API prefix는 `/api/v1`이다. Caddy는 `/api/*`만 backend로 보내고 나머지는 frontend로
보낸다. backend와 frontend는 host port를 publish하지 않는다.

## 네트워크와 신뢰 경계

Compose는 용도가 다른 network를 합치지 않는다.

| network | 속성 | 연결 서비스 |
|---|---|---|
| `public` | 기본 gateway와 외부 egress | Caddy |
| `api` | `internal: true`, 고정 Caddy/backend 주소 | Caddy, backend |
| `web` | `internal: true` | Caddy, frontend |
| `database` | `internal: true` | backend, PostgreSQL, `db-roles`, `migrate` |
| `cache` | `internal: true` | backend, Redis |

Caddy만 `public`에 연결되어 ACME·OCSP와 공개 client를 처리한다. frontend는 backend나
저장소에 직접 접근할 수 없고 Caddy도 PostgreSQL·Redis에 직접 접근할 수 없다.
backend가 신뢰하는 forwarded proxy는 `api`의 Caddy 주소 `172.31.250.2`로 고정된다.
production에서는 wildcard `Host`와 비-HTTPS browser origin을 거부한다.

각 container는 `no-new-privileges`, capability 최소화, 제한된 json-file logging을 쓴다.
모든 service root filesystem은 read-only이며 필요한 경로만 bounded tmpfs나 named volume으로
제공한다. PostgreSQL은 PGDATA volume 외에 `/tmp`와 socket용 `/var/run/postgresql`만 tmpfs로
쓸 수 있다. PostgreSQL과 Redis는 host port를 열지 않는다.

## 배포 모드

`scripts/generate-env.sh`와 `scripts/validate-env.sh`가 site address와 모드의 결합을
강제한다.

| site address | 환경 | cookie |
|---|---|---|
| `http://localhost`, `http://127.0.0.1` | `development` | 개발용 이름, Secure 없음, host `127.0.0.1` bind |
| DNS 이름, `https://` origin | `production` | Secure `__Host-*`, host `0.0.0.0` bind |

공개 평문 HTTP와 공개 주소의 development downgrade는 거부한다. 공개 자동 TLS 배포는
host 80/443을 사용한다. production backend는 PostgreSQL·Redis, 정확한 origin/host
allowlist와 file-backed secret을 요구한다.

## 비밀값 경계

`.env`는 mode 0600의 비밀이 아닌 Compose 설정이다. 실제 비밀은 owner-only mode 0700
`.secrets/`의 다음 파일에만 둔다.

- `alpha_secret_key`: session·CSRF·invite·등록 접근 코드·exact flag HMAC root
- `postgres_owner_password`: initdb·role provisioning·backup owner 인증
- `postgres_migrator_password`: migration/bootstrap 인증
- `postgres_runtime_password`: backend CRUD runtime 인증
- `redis_password`: Redis 인증
- `admin_password`: 최초 bootstrap 또는 일회성 복구

Compose는 필요한 서비스에만 이를 `/run/secrets/*`로 read-only mount한다. backend는
root key, runtime DB, Redis 세 파일만 요구하고, `migrate` one-shot만 migrator DB와
admin 파일을 추가로 받는다. PostgreSQL owner secret은 postgres와 `db-roles`에만 간다. reader는
최종 symlink가 아닌 일반 파일, UTF-8 한 줄, 최대 16 KiB만 허용한다. 운영 전 검증기는
디렉터리·파일 권한과 placeholder도 확인하며 비밀값을 `.env`에 직접 둔 구성을 거부한다.

PostgreSQL entrypoint는 초기 root와 exact capability로 owner secret을 읽고 volume을 UID 70에
맞춘 뒤 권한을 낮춘다. 실행 중 DB PID 1은 UID 70이며 owner secret을 읽을 수 없다. 별도의
`db-roles` one-shot은 UID 65532로 세 DB credential을 읽고 최소 권한 role을 구성한다.

Compose file-source secret은 host access control을 유지한다. host owner에게 `rw-`, 고정
container UID 65532에게 `r--`, group·other에는 아무 권한도 주지 않는 exact POSIX ACL로
container read를 허용한다. `scripts/set-secret-acl.sh`와 `make secret-acl`이 기존 ACL을
제거해 이 계약을 재적용하고 `validate-env.sh`가 `getfacl -cpn` 결과를 검사한다. host에는
`acl` package의 `setfacl`·`getfacl`과 ACL 지원 filesystem이 필요하다. rootless UID mapping은
이 숫자 계약과 다르므로 별도 설계와 end-to-end 검증 없이는 사용할 수 없다.

application root key를 바꾸면 기존 session, invite, 등록 접근 코드와 exact flag HMAC을 검증할 수 없다.
DB 복구에는 같은 시점의 secret file 세트가 반드시 함께 있어야 한다.

## 외부 저장소

번들 PostgreSQL·Redis의 평문 transport 예외는 격리된 internal network에서 이름이
정확히 `postgres`, `redis`인 서비스에만 적용된다. production backend는 그 밖의
PostgreSQL host에 `ALPHA_DATABASE_TLS=true`를 요구해 `sslmode=verify-full`을 만들고,
외부 Redis에는 `ALPHA_REDIS_TLS=true`를 요구해 `rediss://`를 만든다.

기본 `compose.yaml`은 local store, internal-only backend, local `depends_on`, local
PostgreSQL backup을 전제로 한다. 외부 저장소 전환은 단순 환경값 변경이 아니다.
canonical Compose, validator와 security graph의 reviewed code change에서 backend
egress, CA trust, hostname 검증, dependency health와 외부 서비스용 backup/restore를
함께 설계해야 한다. 임의 Compose override는 지원하지 않는다.

## 인증, session과 CSRF

비밀번호는 Argon2id로 저장한다. 로그인 시 발급한 opaque session 원문은 cookie에만 있고
DB에는 domain-separated HMAC과 만료 시각, 사용자의 `credential_version`만 저장한다.

Argon2 hash/verify는 backend process 전체에서 nonblocking 2-slot semaphore로 제한한다.
두 slot이 모두 사용 중이면 worker thread를 기다리게 하지 않고 즉시 HTTP 503
`password_service_busy`, `Retry-After: 1`을 반환한다. 인증·가입·비밀번호 변경은 필요한
DB snapshot을 얻은 뒤 transaction을 rollback해 connection을 반납하고 Argon2를 계산한다.
성공 후 행 lock을 다시 얻어 사용자·event 상태와 최신 password hash를 재검증한다.

production cookie는 다음과 같다.

| 이름 | 속성 | 역할 |
|---|---|---|
| `__Host-alpha_session` | Secure, HttpOnly, SameSite=Lax, Path=/, Domain 없음 | 인증 session |
| `__Host-alpha_csrf` | Secure, HttpOnly, SameSite=Lax, Path=/, Domain 없음 | double-submit 값 |
| `__Host-alpha_browser` | Secure, HttpOnly, SameSite=Lax, Path=/, Domain 없음 | 익명 CSRF context |

CSRF cookie를 JavaScript로 읽지 않는다. `GET /api/v1/auth/csrf`가 같은 token을 JSON으로
전달하고 frontend는 상태 변경 요청의 `X-CSRF-Token` header로 보낸다. middleware는
cookie/header의 constant-time equality, 서명과 TTL을 확인한다. token은
domain-separated HMAC으로 현재 session token 또는 익명 browser token에 결합된다.
로그인, 비밀번호 변경과 로그아웃은 이 context를 회전한다.

비밀번호 변경과 CLI 복구는 user의 `credential_version`을 증가시키고 알려진 session을
삭제한다. 인증 시 session에 기록된 version도 비교하므로 DELETE와 경합해 남은 이전
session까지 거부한다. migration `20260824_0002`가 users와 sessions에 이 필드를 함께
추가했으므로 이 revision은 rolling이 아닌 maintenance/all-at-once 배포 대상이다.

관리자는 participant User만 사유와 함께 정지하거나 다시 활성화할 수 있다. 정지는 User row
lock 아래 `active=false`, `credential_version` 증가와 전체 SessionToken 삭제를 함께 commit하고,
재활성화는 폐기한 session을 복원하지 않는다. 제출·비밀번호 변경·팀 작업은 최종 쓰기 전에
User를 잠근 뒤 active, version과 필요한 session을 다시 검사한다. 정지가 lock을 먼저 얻으면
진행 중 요청의 초기 인증 snapshot은 효력을 잃고, 요청이 먼저 얻으면 해당 transaction이
정지보다 앞서 직렬화된다.

새 session을 만드는 경로는 만료 행을 bounded `SKIP LOCKED` batch로 지운 뒤 사용자별 최신
active session 9개만 유지하고 하나를 추가한다. 따라서 성공한 새 로그인 뒤 active session은
최대 10개이며 오래된 token부터 폐기된다. 비밀번호 변경은 session-derived rate key와 함께
stable user-ID identity key를 별도 scope로 검사하므로 session/CSRF 회전으로 rate budget을
초기화할 수 없다. 어느 Redis rate 검사든 store 오류는 write를 허용하지 않는다.

반복 인증 감사는 User row lock 아래 사용자·action별 최신 집계행으로 병합한다.
`auth.login`, `auth.logout`, `auth.password_changed`의 새 occurrence는 별도 행을 늘리지 않고
`occurrences`, `first_seen_at`, `last_seen_at`을 갱신한다. upgrade 전에 존재한 과거 행은
삭제하지 않는다. `user.password_changed` OutboxEvent는 같은 user의 미전달 행만 찾아
`occurrences`를 올리며, 전달된 과거 행은 덮어쓰지 않는다.

frontend route guard는 비로그인·비관리자 이동과 초기 비밀번호 미변경 관리자를
사용자 경험 차원에서 차단한다. 최종 권한 판단은 모든 backend endpoint에서 다시 한다.

## 데이터와 transaction 불변식

- 비밀번호, session 원문, exact flag 원문을 DB·API·log에 저장하거나 반환하지 않는다.
- exact flag와 제출값, IP, invite, 등록 접근 코드는 서로 다른 domain의 HMAC으로 저장한다.
- 등록 접근 코드 원문은 관리자 생성 응답에 한 번만 반환하며 목록·audit·outbox에는 넣지 않는다.
- regex flag는 입력 길이와 실행 timeout을 제한한다.
- 정답 Submission, Solve, ScoreEvent, AuditEvent와 OutboxEvent는 한 transaction이다.
- `(team_id, challenge_id)` unique constraint가 중복 solve의 최종 방어선이다.
- 제출의 PostgreSQL lock 순서는 `Event → User → Membership → Team → Challenge`다.
- Redis 제출 rate 검사는 Event shared lock을 잡은 뒤, 대기 가능한 actor row lock보다 먼저
  수행한다. 통과하면 User, Membership과 Team을 순서대로 다시 잠그고 Membership이 여전히
  처음 확인한 팀에 속하는지 재검증한다. 정답 후보만 Challenge를 잠근 뒤 최신 flag로 다시
  검증한다.
- Redis tier는 인증 `identity → IP → registration global`, 팀 변경 `session → IP`, 제출
  `team → IP → challenge` 순으로 fail-fast한다. 첫 좁은 bucket deny는 뒤의 공유 budget을
  소비하지 않는다.
- 제출·팀 변경 transaction은 Event shared lock, 등록 후반 capacity 검사와 상태 전환은
  exclusive lock을 사용한다. `code` 가입은 Argon2 전 사전 검사 뒤 Event와 RegistrationCode를
  다시 잠그며 코드 사용량과 User·Session·audit·outbox를 한 transaction으로 commit한다.
- 누락·미등록·만료·소진·폐기 등록 코드는 모두 403 `registration_access_denied`로 거부한다.
- 팀 소유권 이전·멤버 제거는 actor·target User, Membership과 Team을 잠근 뒤 owner 권한과
  target 상태를 재검증한다. Submission 이력이 있는 사용자는 탈퇴·제거할 수 없고, 제거 시
  invite를 같은 transaction에서 회전해 이전 코드 재가입을 막는다.
- Redis rate limiter 오류 시 제출을 우회 허용하지 않고 실패시킨다.
- 오답 제출은 공개 점수판 cache를 유지하고, 정답 commit만 `live`, `frozen`, `final` cache를
  무효화한다.
- `archived` event의 이벤트 설정, 문제와 공지는 읽기 전용이다.

OutboxEvent는 현재 외부 message delivery가 아니라 같은 transaction의 내부 journal과
관리자 조회용이다. worker, retry와 delivery SLA는 구현되어 있지 않다.

### 영구 저장 상한과 보존 경계

`backend/src/alpha/limits.py`의 public mutation hard cap은 운영 설정으로 완화할 수 없다.

- 팀·문제별 Submission은 1,000개, 팀·이벤트 전체는 10,000개가 최대다.
- 문제 `max_attempts` 1~1,000은 더 낮은 문제별 상한이고, 0도 시스템 hard cap까지라는
  뜻이지 무한 저장을 뜻하지 않는다.
- 팀 구성원은 100명이 최대다. 가입은 Team row lock 아래 현재 Membership 수를 세어
  concurrent N+1을 직렬화하고, 상한에서는 409 `team_capacity_reached`를 반환한다.
  `/api/v1/meta`는 `limits.max_members_per_team=100`을 공개한다.
- 사용자·이벤트별 팀 생성·가입·초대 회전·소유권 이전·멤버 제거·탈퇴 audit 합계는 100회가
  최대다. User row lock과 보존된 AuditEvent count로 concurrent 우회를 막는다.
- 팀 변경 전 Redis는 기본 session 20회/시간, source IP 100회/시간을 검사하며 저장소 오류는
  fail-closed HTTP 503이다.
- participant role User는 database 전체 100,000명이 최대다. 등록 후반에 Event row를
  exclusive lock하고 상태·중복·participant count를 다시 검사해 concurrent N+1을 막는다.
- 등록은 identity 20회/60초, source IP 200회/60초와 global 1,000회/60초 Redis budget을
  함께 사용한다. global key는 identity·IP가 달라도 공유되고 Redis 오류는 fail-closed다.
- Event의 `registration_access_mode`는 `open|code`이며 `code`일 때 24-byte 무작위 코드의
  `registration-access` HMAC과 1~10,000회 또는 무제한 사용량, 만료·폐기 상태를 검사한다.
- 사용자별 active SessionToken은 10개가 최대이며 새 session에서 가장 오래된 active 행부터
  지운다.

제출 cap은 Team row lock 안에서 idempotency를 다시 확인하고 count한 뒤 insert하므로 같은
팀의 concurrent 오답도 상한을 넘지 않는다. 오답 Submission은 HMAC과 최소 metadata만
저장하고 OutboxEvent를 추가하지 않는다. 정답 solve·audit·outbox, 중요한 팀 변경
audit/outbox와 관리자 변경 기록은 보존한다. login/logout/password-change audit의
사용자·action별 집계와 미전달 password-change Outbox 병합은 공개 반복 경로의 행 수를
고정하기 위한 것이며, 전체 audit/outbox retention이나 자동 pruning을 구현한 것은 아니다.
현재 event의 PostgreSQL volume과 암호화 backup을 운영자가 모니터링하고, archived snapshot
뒤 다음 event를 새 database에서 시작하는 것이 장기 보존 경계다.

## 이벤트와 점수

```text
draft → registration → live → frozen → ended → archived
```

상태는 한 단계씩만 바뀐다. `start_at`, `end_at`, `freeze_at`은 자동 상태 전환기가 아니라
추가 server gate다. 시작 전에는 문제 내용·접속 정보·제출을 닫고 종료 뒤에는 제출을
거부한다. frozen 공개 점수판은 `freeze_at` 이후 solve를 숨기고 ended에서 최종 결과를
공개한다. 동결이 시작된 뒤 freeze 기준과 점수 산식은 잠긴다.

동적 문제에서 solve 수가 `s`, 초기점수 `I`, 최소점수 `M`, decay `D`이면 다음 값을
사용한다.

```text
max(M, round(I - (I - M) * s² / D²))
```

solve transaction은 해당 solve 시점의 `s`로 계산한 값을 solve `ScoreEvent.points`와
`Submission.awarded_points`에 기록한다. 이후 solve가 추가되어도 과거 `ScoreEvent`를 bulk
rewrite하지 않는다. 공개 점수판은 solve 이력의 저장 점수를 합산하지 않고, 현재 phase에서
보이는 문제별 solve 수로 모든 해당 solve의 동적 점수를 계산한다. 따라서 이력의 solve 시점
점수와 현재 공개 점수판 값은 의도적으로 다를 수 있다.

점수판 builder는 전체 Solve 객체를 Python으로 materialize하지 않는다. PostgreSQL이 문제별
solve count와 팀별 solve·award 기여를 aggregate하고 정렬하며, Python은 hard top 1,000행만
응답으로 만든다. 응답의 `total_entries`는 전체 순위 항목 수, `truncated`는 1,000개 초과
여부이고 `/api/v1/meta`의 `limits.max_public_scoreboard_entries=1000`이 client 계약이다.

Redis cache는 `{generation, payload}` envelope다. event·phase별 45초 single-flight lease는
무작위 token 소유자만 해제할 수 있다. 정답 solve와 점수판 관련 admin mutation은 generation을
증가시키며 builder는 집계 전후 값을 비교해 경합한 stale payload를 게시하지 않는다. cache나
lease 조작 실패는 HTTP 503이고, cold cache에서 lease를 얻지 못한 loser도 기다리거나 DB를
중복 집계하지 않고 HTTP 503 `scoreboard_busy`, `Retry-After: 1`을 반환한다.

동일 점수는 총점 내림차순, solve 수 내림차순, 마지막 solve 시각 오름차순, 팀 이름
순으로 정렬한다. 다음 이벤트는 archived DB를 재사용하지 않고 새 Compose project와
빈 database로 시작한다.

## API와 frontend 경계

backend error는 `error.code`, 사용자용 `error.message`, `request_id`만 포함하며 내부
예외와 secret을 숨긴다. API 응답과 frontend fetch는 `no-store`를 사용한다. Caddy는
API 응답을 압축하지 않아 인증·CSRF 응답의 TLS 압축 side channel을 줄인다.

frontend Markdown renderer는 raw HTML을 실행하지 않고 위험한 link/image protocol을
제거한다. React route guard와 별개로 backend의 인증·role 검사가 최종 권한 경계다.
팀 이름의 control/format 문자는 frontend에서도 조기에 거부하지만 backend validation이
최종 권한이다.

관리자 제출 감사는 한 번에 전체 이력을 적재하지 않는다. backend의 `(created_at, id)`
keyset cursor를 이용해 200개씩 사용자가 명시적으로 “더 불러오기”를 선택한다. filter
변경·새로고침·unmount 시 이전 요청을 `AbortController`로 중단하고 CSV에는 현재 성공적으로
불러온 범위만 포함한다고 화면과 export 버튼에서 명확히 한다. spreadsheet formula 문자는
CSV cell 앞에 안전한 접두사를 둔다.

## Edge와 정적 frontend

`deploy/Caddyfile`은 다음 방어를 공개 경계에 적용한다.

- DNS 배포의 자동 TLS와 HTTP→HTTPS
- 최대 2 MiB request body와 header/body/upstream timeout
- HSTS 1년, strict CSP, X-Content-Type-Options, X-Frame-Options
- Referrer/Permissions Policy, COOP, CORP, cross-domain policy 차단
- API `Cache-Control: no-store, private`
- JSON access log와 Caddy admin endpoint 비활성화
- public `/api/v1/health/ready` 404 차단과 backend direct active health에서만 readiness 사용

`frontend/nginx.conf`는 non-root 8080에서 `/healthz`, hashed asset immutable cache,
SPA fallback과 HTML `no-store`를 제공한다. standalone 사용에서도 Caddy와 같은 방향의
엄격한 browser security header를 적용하지만 공개 배포의 canonical edge는 Caddy다.

public `/api/v1/health/live`는 저장소 I/O 없이 process 생존만 알린다. PostgreSQL·Redis를
실제로 조회하는 `/api/v1/health/ready`는 Caddy가 `api` network에서 backend active
health에만 사용하며 인터넷 요청에는 404를 반환한다.

## 이미지와 공급망

Docker build input은 tag만 신뢰하지 않는다.

- backend builder/runtime: digest로 고정한 official Python 3.12 Alpine
- frontend builder/runtime: digest로 고정한 Chainguard Node와 nginx
- PostgreSQL·Redis: 검증된 `.env`로 render한 실제 Compose 운영 reference의 Chainguard image
- Caddy: digest 고정 Chainguard Go builder와 static runtime 사이에서 full upstream
  source commit을 직접 빌드한 custom binary

custom Caddy build는 dependency graph에 유지보수 중단 `golang.org/x/crypto/openpgp`가
도달 가능해지면 실패한다. `security/ctfnight.openvex.json`의 VEX는 이 graph 검사를
근거로 특정 Caddy finding 하나만 `not_affected`로 기술하며 전체 제품의 예외 목록이 아니다.
gate의 Python exact allowlist는 statement가 정확히 하나인지와 vulnerability, PURL,
status, justification, openpgp impact 근거를 Trivy 전에 검사해 waiver 확대를 거부한다.

`SECURITY_REQUIRE_DOCKER=1 make security`는 hash-locked Python 환경, npm audit,
Trivy filesystem과 모든 builder/runtime·database/cache image를 검사한다. npm은
`info`부터, Trivy는 `UNKNOWN`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` 전 severity를
포함하며 허용 근거 없는 finding에 실패한다. 각 image의 CycloneDX SBOM도 생성한다.
CI와 `make build`는 Docker나 scanner를 사용할 수 없는 상태를 통과로 간주하지 않는다.
`make build`는 canonical application image를 한 번만 build하고 exact image ID를 scan해
SBOM을 만든 뒤, source·canonical `.env`·7-service rendered graph·canonical reference와
5개 고유 image ID를 묶은 mode 0600 deployment manifest를 게시한다. `db-roles`는 PostgreSQL,
`migrate`는 backend image ID를 alias로 재사용한다.

`make up`은 같은 directory-inode lock을 최종 preverify부터 up·postverify 종료까지 보유해
그 사이의 canonical retag·manifest 폐기를 거부한다. `make up`과 `make wait`는 2시간 이내
manifest의 source/env/render/reference-tag→exact-ID
binding이 현재 상태와 완전히 일치할 때만 기존 scan을 재사용한다. manifest 부재,
stale·tamper·drift는 전체 build/scan을 fail-closed로 다시 실행한다. prestart ID 검증 뒤
Compose를 `up --no-build --remove-orphans`로 재생성한다. poststart는 project의 orphan을
포함한 모든 container와 Compose service label을 열거해 canonical 7개와 정확히 일대일인지
검사하고 unknown orphan·duplicate·누락을 거부한다. 이어 상시 5개 service의 running 상태,
one-shot `db-roles`·`migrate`의 exited(0), 각 alias와 5개 고유 image ID를 다시 대조한다.
Docker·scanner 부재, finding, 상태 또는 어느 ID·집합 불일치도 배포 성공으로 처리하지 않는다.

`make import-challenge`와 관리자 복구 helper도 같은 lock을 preverify부터 one-off DB mutation
종료까지 유지하고 `--pull never`를 강제한다. 따라서 검증 뒤 같은 canonical 운영 경로의
retag·manifest 폐기나 registry pull로 실행 image가 바뀌는 경로가 없다.

Makefile과 backup은 `.env`를 검증하고 graph·profile·interpolation override를 거부하는
`scripts/compose.sh` wrapper만 사용한다. wrapper는 project directory, env file,
`compose.yaml`을 고정한다. gate는 backend lock 동기화와 JSON render의 정확한
7개 service, backend/frontend/Caddy local build context, 모든 service의 `linux/amd64`와
PostgreSQL·Redis reference 및 one-shot alias를 검사한다.
`COMPOSE_FILE`, `COMPOSE_ENV_FILES`나 command-line `COMPOSE`로 graph를 바꾸는 것은
허용하지 않는다.
운영자의 wrapper 직접 호출은 `config`, `stop`, `exec`, `run` 같은 보조 작업으로 제한하고,
image build는 전체 scan을 수행하는 `make build`, service start는 유효한 manifest binding을
검증하고 필요할 때 전체 gate를 다시 실행하는 `make up`, `make wait`만 사용한다.

이 결과는 해당 source, image와 scanner DB 시점의 증거다. 미래 zero-day가 절대 0이라고
보장할 수 없다. 매일 03:17 UTC gate는 정기 탐지 지연을 주 단위에서 하루 수준으로 낮추는
운영 보완이며 취약점 부재 보장이 아니다. 운영자는 최신 scanner DB, 배포 직전 재검사와
fail-closed gate를 계속 유지해야 한다.

## 백업, 복구와 upgrade

`scripts/backup.sh`는 age public recipient를 필수로 요구한다. PostgreSQL custom dump,
`.env`, 여섯 secret, Compose/Caddy 설정, image 목록과 source 상태를 `/dev/shm` tmpfs에서
조립해 age로 암호화하며, 영구 위치에는 `.tar.gz.age`와 `.sha256`만 게시한다. Redis
cache와 재발급 가능한 Caddy 데이터는 제외한다.

복구는 공개 서비스를 정지하고 archive 외부 SHA-256, 복호화 후 내부 `SHA256SUMS`,
manifest/source/image를 검증한 뒤 PostgreSQL을 교체하는 maintenance 작업이다. 복호화는
pipe로 바로 추출하지 않는다. `age --output`의 독립 성공, tmpfs tar 목록 검사, 추출과
내부 checksum을 순서대로 수행하고 EXIT trap으로 평문 archive·작업본을 제거한다. 새
서버에는 첫 기동 전에 같은 `.env`와 `.secrets`를 복구하고 `make secret-acl`로 host owner와
UID 65532 전용 ACL을 다시 만든다. 자세한 명령은
[README.md](README.md#복구)를 따른다.

schema release는 image와 source를 함께 배포한다. 특히 `credential_version` migration은
모든 frontend/backend를 정지하고 일회성 migration을 적용한 뒤 같은 revision의 전체
stack을 올리는 all-at-once 절차를 사용한다. schema를 적용한 뒤 code만 rollback하지
않으며 실패하면 이전 source/image와 upgrade 직전 age backup을 함께 복구한다.

`20260824_0003`도 기존 challenge의 `max_attempts > 1000`을 1,000으로 정규화하고 PostgreSQL
CHECK와 SQLite INSERT/UPDATE trigger로 동등한 DB upper bound를 추가하므로 submission
hard-cap runtime과 함께 같은 all-at-once 절차로 배포한다.

`20260824_0004`는 Event에 기본값 `open`인 `registration_access_mode`를 추가하고
`registration_codes` table과 HMAC·label·사용량·폐기 상태 제약 및 index를 만든다. PostgreSQL은
mode CHECK, SQLite는 동등한 trigger를 사용한다. code 가입 runtime과 schema를 같은
all-at-once release로 배포한다.
