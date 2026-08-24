# CTFnight 보안 정책

이 문서는 현재 checkout을 인터넷에 공개할 때 지켜야 하는 최소 보안 기준이다. 배포
절차는 [README.md](README.md), 시스템 경계는 [ARCHITECTURE.md](ARCHITECTURE.md),
backend 구현은 [backend/README.md](backend/README.md)를 함께 따른다.

## 지원 범위와 취약점 제보

보안 수정은 현재 유지되는 source revision과 그 revision이 고정한 image·lock에 적용한다.
검증된 runtime platform은 rootful Linux Docker x86_64(`linux/amd64`)이며 Compose의 모든
서비스를 이 platform에 고정한다. application·edge·cache·one-shot은 UID/GID `65532:65532`다.
PostgreSQL만 고정 image의 공식 초기화 계약에 따라 exact capability를 가진 `0:0` entrypoint가
volume을 준비하고 server PID 1을 내장 `70:70`으로 낮춘다. CI가 실행 UID와 secret 비가독성을
확인한다. 다른 architecture의 동작이나 CVE 상태는 별도 검증 전에는 지원하지 않는다.
rootless Docker와 user namespace remap은 UID 65532·70 및 초기 root의 host mapping과 volume
ownership이 달라 현재 지원·검증하지 않는다.
오래된 fork, 임의로 바꾼 base image, 공개 edge를 우회한 backend/frontend port와 별도
challenge workload는 이 기준의 보호 범위가 아니다.

운영 host에는 Linux `util-linux`의 `flock`가 필수다. `security-scan.sh`, `make up`, challenge
import와 관리자 복구는 `security-reports/` directory inode의 같은 비차단 `flock -n`을
사용한다. scanner는 전체 gate, `make up`은 최종 preverify→up→postverify, one-off는
preverify→DB mutation 전 구간을 직렬화한다. 같은 project에서 canonical image retag,
manifest 폐기 또는 다른 deployment 작업이 경합하면 대기하지 않고 실패한다.

취약점을 발견하면 exploit detail, token, 사용자 정보, flag나 운영 주소를 공개 issue에
올리지 말고 maintainer와 합의한 비공개 채널로 다음 정보를 전달한다.

- 영향을 받는 source revision·image digest와 배포 모드
- 재현에 필요한 최소 요청과 예상/실제 결과
- 계정·데이터 영향과 이미 수행한 완화
- secret을 제거한 log, request ID와 시각

maintainer는 접수 확인, 재현, 영향 분류, 수정·회귀 test, image 재빌드와 운영자 통지를
순서대로 수행한다. 명시적으로 허가받지 않은 production data 접근이나 서비스 방해를
취약점 검증 명목으로 수행하지 않는다.

## 위협 모델

다음 주체는 기본적으로 신뢰하지 않는다.

- 인터넷의 익명·가입 사용자와 제출·Markdown·team name 등 사용자 입력
- browser cache, extension과 외부 link
- challenge workload와 참가자에게 제공한 접속 서비스
- registry tag, 새 dependency release와 scanner 외부 database

운영 host root, Docker daemon, age identity, DNS 계정과 CI release 권한은 privileged
trust anchor다. 이 중 하나가 탈취되면 application 내부 통제만으로 안전을 보장할 수 없다.
host patch, MFA, 최소 권한, 관리 접속 제한과 별도 backup identity 보관은 운영자 책임이다.

CTFnight는 일반적인 Internet abuse를 줄이지만 무제한 DDoS 방어 서비스가 아니다. 공개
대회는 upstream firewall/CDN의 호환성과 실제 client IP·TLS·CSRF 경계를 별도 검토한다.

## 공개 배포 기준

`scripts/generate-env.sh`와 `scripts/validate-env.sh`가 다음 조합을 강제한다.

| 주소 | 환경 | 허용 용도 |
|---|---|---|
| `http://localhost`, `http://127.0.0.1` | `development` | host `127.0.0.1`에만 bind하는 로컬 시험 |
| DNS 이름 또는 HTTPS origin | `production` | host `0.0.0.0`에 bind하는 공개 운영 |

공개 평문 HTTP, 공개 주소의 development 설정, wildcard trusted host, 비-HTTPS allowed
origin과 Secure가 아닌 production cookie는 허용하지 않는다. 공개 자동 TLS는 host
80/443을 사용하며 Caddy가 인증서 발급과 HTTP→HTTPS를 처리한다.

development Compose는 `127.0.0.1`, production Compose는 `0.0.0.0`의 IPv4에만 Caddy
port를 bind한다. 별도 IPv6 edge를 검토하지 않았다면 AAAA를 게시하지 않는다. host
firewall에서는 TCP 80·443과 필요 시 UDP 443만 공개한다. PostgreSQL, Redis,
backend 8000과 frontend 8080은 host에 publish하지 않는다. 관리용 Docker socket을
application container에 mount하지 않는다.

frontend의 Vite `dev`와 `preview` script도 기본적으로 `127.0.0.1`에만 bind한다. 명시적
LAN override는 production edge의 TLS·보안 경계를 제공하지 않으며 신뢰된 격리망과 host
firewall에서만 일시적으로 사용해야 한다. 자세한 opt-in 경고는
[frontend/README.md](frontend/README.md#lan-접속-opt-in)를 따른다.

## 비밀값 관리

`.env`는 mode 0600의 비밀이 아닌 설정 파일이다. 운영 비밀은 owner-only mode 0700
`.secrets/` 아래 다음 일반 파일에만 둔다.

| 파일 | 보호 대상 |
|---|---|
| `.secrets/alpha_secret_key` | session·CSRF·invite·등록 접근 코드·flag HMAC root |
| `.secrets/postgres_owner_password` | initdb·role provisioning·backup owner credential |
| `.secrets/postgres_migrator_password` | migration/bootstrap credential |
| `.secrets/postgres_runtime_password` | backend CRUD-only credential |
| `.secrets/redis_password` | Redis credential |
| `.secrets/admin_password` | 초기 bootstrap 또는 일회성 복구 |

production backend는 root key, runtime DB, Redis 세 경로만 받고 admin·owner·migrator
credential은 받지 않는다. `migrate` one-shot만 admin과 migrator 경로를 받는다. reader는 최종 symlink를
따르지 않고 UTF-8 한 줄, 일반 파일, 16 KiB 상한을 검사한다. `make validate`는 더 엄격한
directory/file mode, placeholder, 줄바꿈과 비밀값의 `.env` 유입을 검사한다.

host filesystem은 POSIX ACL을 지원해야 하고 운영 host에는 `acl` package의 `setfacl`과
`getfacl`이 있어야 한다. 각 secret은 host owner `rw-`, named user 65532 `r--`, group·other
없음의 exact ACL을 사용한다. 수동 편집·교체·복구 뒤에는 `make secret-acl`로 기존 ACL을
제거하고 이 계약을 재적용한 뒤 검증한다. target은 여섯 고정 파일, basename과 symlink·hard
link도 확인하지만 credential 자체를 회전하지는 않는다.

PostgreSQL의 초기 root entrypoint에는 owner secret과 named volume 준비에 필요한
`CHOWN`, `DAC_OVERRIDE`, `FOWNER`, `SETGID`, `SETUID`만 다시 부여한다. 준비가 끝나면 PID 1은
UID/GID 70으로 실행되고 owner secret을 읽을 수 없다. 지속 실행되는 DB process에 root나
owner credential 접근을 허용하는 예외가 아니다.

초기 관리자가 비밀번호를 바꾸면 `admin_password` 파일을 삭제하지 말고 비운 뒤
`ALPHA_ADMIN_BOOTSTRAPPED=true`로 표시하고 backend를 재생성한다. 일회성 복구 절차는
[backend/README.md](backend/README.md#관리자-비밀번호-복구)를 따른다.

`ALPHA_ADMIN_BOOTSTRAPPED`는 Compose service 환경이나 build argument로 전달하지 않는
운영 복구 marker다. deployment source digest는 이 key가 정확히 한 번 존재하고 값이
`true` 또는 `false`인지 검증한 뒤 그 값만 `<operational-marker>`로 정규화한다. 이 좁은
예외는 복구 중 marker 전환만 허용하며, 다른 `.env` line·값·공백·줄바꿈 drift는 그대로
manifest 불일치로 거부한다.

비밀값을 chat, issue, command argument, shell history, image layer, log 또는 CI artifact에
복사하지 않는다. secret 유출이 의심되면 먼저 공개 traffic을 차단하고 영향 범위를
확인한다. application root key를 회전하면 모든 session·invite·등록 접근 코드·exact flag HMAC이
무효가 되므로 DB와 flag source를 포함한 계획된 incident migration이 필요하다. DB만
복구하면서 다른 시점의 root key를 사용하지 않는다.

## 인증 cookie와 CSRF

production의 browser cookie는 다음 조건을 갖는다.

| 이름 | 보안 속성 |
|---|---|
| `__Host-alpha_session` | Secure, HttpOnly, SameSite=Lax, Path=/, Domain 없음 |
| `__Host-alpha_csrf` | Secure, HttpOnly, SameSite=Lax, Path=/, Domain 없음 |
| `__Host-alpha_browser` | Secure, HttpOnly, SameSite=Lax, Path=/, Domain 없음 |

session은 opaque 원문을 cookie에만 두고 DB에는 domain-separated HMAC만 저장한다.
CSRF cookie도 HttpOnly이므로 frontend가 cookie를 읽지 않는다. CSRF endpoint가 token을
JSON으로 주고 client는 상태 변경 요청의 `X-CSRF-Token` header로 되돌려 보낸다.
backend는 cookie/header의 constant-time equality, 서명, TTL과 active session 또는
anonymous browser context 결합을 모두 검사한다. 로그인, 로그아웃과 비밀번호 변경은
context를 회전한다.

route guard는 사용자 경험을 위한 1차 방어다. 인증, 관리자 role과 초기 비밀번호 변경
여부의 최종 판단은 backend가 한다. API와 frontend fetch는 `no-store`이며 Caddy도 API에
`Cache-Control: no-store, private`를 덧붙인다.

비밀번호 변경과 CLI 복구는 User의 `credential_version`을 증가시키고 session을 삭제한다.
관리자의 참가자 정지도 같은 User row lock 아래 version을 증가시키고 모든 SessionToken을
삭제한다. 관리자 계정은 이 moderation endpoint로 변경할 수 없고, 정지에는 audit에 남는
사유가 필요하다. 재활성화는 기존 session을 복원하지 않는다. 인증 시 Session의 version도
비교하므로 concurrent DELETE에서 남은 이전 token도 사용할 수 없다.

제출·비밀번호 변경·팀 mutation은 최종 쓰기 전에 User row를 잠그고 최신 active 상태,
`credential_version`과 필요한 session을 다시 검증한다. 정지가 먼저 commit되면 초기 인증
snapshot을 가진 진행 중 요청도 HTTP 401로 거부된다. 요청이 User lock을 먼저 얻은 경우에는
그 transaction이 정지보다 먼저 직렬화되며, 정지는 이어서 session을 폐기한다.

새 session 생성은 사용자별 최신 active session 9개를 남기고 새 행을 추가해 최대 10개로
제한한다. 가장 오래된 active token은 성공한 새 로그인에서 폐기된다. 비밀번호 변경은
회전하는 session-derived identity와 별도로 stable user-ID rate key를 검사하므로 성공 후
cookie 회전으로 인증 budget을 우회할 수 없다. 두 scope 모두 identity→IP Redis 검사를
fail-closed로 적용한다.

## network와 저장소 TLS

Compose network는 다음처럼 분리한다.

- `public`: Caddy만 연결하고 외부 gateway/egress 제공
- `api`: Caddy와 backend만 연결하는 `internal: true`
- `web`: Caddy와 frontend만 연결하는 `internal: true`
- `database`: backend, PostgreSQL과 one-shot `db-roles`·`migrate`만 연결하는 `internal: true`
- `cache`: backend와 Redis만 연결하는 `internal: true`

번들 저장소의 평문 transport는 이 격리망에서 이름이 정확히 `postgres`, `redis`인
서비스에만 허용한다. 외부 PostgreSQL은 `ALPHA_DATABASE_TLS=true`와
`sslmode=verify-full`, 외부 Redis는 `ALPHA_REDIS_TLS=true`와 `rediss://`가 필수다.

기본 backend는 외부 egress가 없으므로 managed store를 쓰려면 최소 egress, 신뢰할 CA,
hostname 검증, local `depends_on` 대체와 공급자별 backup/restore를 함께 설계한다. 이
변경은 canonical Compose·validator·security graph의 reviewed code change여야 하며
runtime override는 지원하지 않는다. TLS 검증을 끄거나 internal network를 하나로 합쳐
문제를 우회하지 않는다.

## 입력·browser 방어

- API는 Host, CORS, CSRF, role과 request body 상한을 endpoint 전에 검사한다.
- password, session, flag 원문과 내부 exception을 response·log에 넣지 않는다.
- exact flag와 제출, IP, invite, 등록 접근 코드는 서로 다른 HMAC domain을 사용한다.
- regex flag에는 길이 제한과 실행 timeout을 적용한다.
- Argon2는 process-wide nonblocking 2-slot으로 제한한다. 포화 시 대기 queue를 만들지
  않고 HTTP 503 `password_service_busy`, `Retry-After: 1`로 fail-fast한다.
- 정상 Argon2 계산 전 DB transaction을 rollback하고, 성공 후 행 lock과 최신 credential을
  재검증해 password 작업 동안 connection·lock을 점유하지 않는다.
- React Markdown renderer는 raw HTML을 실행하지 않고 위험한 URL protocol을 제거한다.
- strict CSP는 script, style, image, font와 connection을 same-origin 중심으로 제한한다.
- 팀 이름의 control/format 문자를 UI와 backend 양쪽에서 거부한다.
- 관리자 CSV는 formula injection 문자를 중화하고 현재 불러온 keyset page 범위만 export한다.
- 관리자 제출 조회는 200개씩 명시적으로 더 불러오며 이전 요청을 abort해 무한 적재와
  stale response 경합을 피한다.

영구 저장 남용은 `limits.py`의 설정 불가능 hard cap으로 추가 제한한다. 팀·문제별 제출은
1,000개, 팀·이벤트별 제출은 10,000개이며 도달하면 HTTP 409
`submission_storage_limit_reached`다. 관리자가 설정한 1~1,000의 `max_attempts`는 그 설정
상한에서 `attempt_limit_reached`를 반환한다. 값 0도 시스템 1,000 cap은 해제하지 않는다.
기존 idempotency key의 replay는 원래 결과를 반환하며 새 Submission을 만들지 않는다.
`20260824_0003` migration이 이전의 더 큰 설정을 1,000으로 정규화하고 PostgreSQL CHECK와
SQLite INSERT/UPDATE trigger로 동등한 DB 상한을 추가한다.

팀은 구성원 100명이 hard cap이다. 가입은 Team row lock 아래 Membership 수를 확인해
동시 N+1을 막고, 상한에서는 HTTP 409 `team_capacity_reached`를 반환한다.
`GET /api/v1/meta`의 `limits.max_members_per_team=100`은 client가 표시할 수 있는 계약이며
최종 권한 검사는 backend transaction이다.

제출 lock 순서는 `Event → User → Membership → Team → Challenge`다. Redis rate 검사는
Event shared lock 뒤, 대기 가능한 actor row lock보다 먼저 수행한다. 통과한 요청은 User,
Membership과 Team을 다시 잠그고 Membership의 team이 처음 확인한 값과 같은지 재검증한다.
정답 후보만 Challenge를 잠근 뒤 최신 flag로 다시 검증한다. 오답 commit은 공개 점수판
cache를 무효화하지 않고 정답 commit만 `live`, `frozen`, `final` cache를 삭제한다.

동적 solve의 `ScoreEvent`는 해당 solve 시점의 값을 보존하고 이후 solve 때문에 과거 행을
bulk rewrite하지 않는다. 공개 점수판은 현재 phase에 보이는 문제별 solve 수로 동적 점수를
계산하므로 저장된 이력값과 현재 표시값이 다를 수 있다.

점수판은 전체 Solve Python 적재 대신 SQL aggregate를 사용하고 공개 응답을 hard top
1,000개로 제한한다. `total_entries`와 `truncated`가 전체 순위 항목 수와 잘림을 나타내며
`/api/v1/meta`는 `limits.max_public_scoreboard_entries=1000`을 공개한다. cold build는
event·phase별 token-owned Redis lease 하나를 45초 사용한다. cache의 generation envelope는
정답 solve와 점수판 관련 admin mutation 중 집계가 경합하면 stale publish를 거부한다.
점수판 cache·lease 조작 장애와 cache가 아직 없는 lease loser는 HTTP 503으로 fail-closed한다.

계층형 rate limit은 인증 `identity → IP → registration global`, 팀 변경 `session → IP`,
제출 `team → IP → challenge` 순으로 fail-fast한다. 첫 좁은 bucket deny 뒤에는 공유 IP와
global·challenge key를 호출하지 않으므로 공격자가 그 budget을 대신 소모할 수 없다. 어느
tier의 Redis 오류도 503 `rate_limit_unavailable`이다.

팀 생성·가입·초대 회전·소유권 이전·멤버 제거·탈퇴는 기본 session 20회/시간과 source IP
100회/시간 Redis limit을 먼저 적용하고, Redis 오류에는 HTTP 503으로 fail-closed한다.
별도로 사용자·이벤트 평생 합계 100회를 User row lock과 AuditEvent count로 강제하며 도달하면 HTTP 409
`team_mutation_limit_reached`다. 관련 audit 행을 임의 삭제하면 이 불변식을 훼손하므로
운영 DB 수동 삭제는 지원하지 않는다.

소유권 이전과 멤버 제거는 등록 기간의 팀전에서만 가능하고, actor·target User와 Membership,
Team을 정해진 순서로 잠근 뒤 소유자 권한과 target 상태를 다시 확인한다. 활성 participant
멤버만 새 소유자가 될 수 있다. 해당 팀에서 정답·오답을 포함한 Submission 이력이 있는
사용자는 탈퇴하거나 제거할 수 없어 team hopping으로 시도 횟수와 제출 책임을 초기화하지
못한다. 제거가 성공하면 Membership 삭제와 함께 invite HMAC을 회전하고 새 평문은 응답에 한
번만 반환한다. 이전 invite와 제거된 사용자의 즉시 재가입은 무효화되고 평문은 audit/outbox에
남지 않는다.

participant 계정은 전체 database에서 100,000명이 hard cap이다. 등록은 Argon2 뒤 Event
exclusive row lock 아래 등록 상태·identity 중복·participant count를 다시 검사하므로
동시 N+1 insert는 HTTP 409 `participant_capacity_reached`로 거부된다. 그 전단의 Redis는
기존 identity 20회/60초와 source IP 200회/60초에 global 1,000회/60초 budget을 더한다.
global key는 분산 identity·IP도 합산하며 제한 시 429 `authentication_rate_limited`, store
오류 시 503 `rate_limit_unavailable`로 fail-closed한다.

이벤트의 `registration_access_mode`는 DB 제약으로 `open` 또는 `code`만 허용한다. `code`
모드의 등록 코드는 24-byte 무작위 원문을 생성하고 DB에는 `registration-access` domain HMAC,
label, 사용량 상한(1~10,000 또는 무제한), 사용 횟수, 선택적 만료와 폐기 상태만 저장한다.
원문은 관리자 생성 응답에서만 한 번 반환되고 이후 목록·audit·outbox에는 포함되지 않는다.
누락·미등록·만료·소진·폐기 원인은 모두 동일한 403 `registration_access_denied` 응답으로
노출을 줄인다.

rate limit 뒤 Argon2 전에 코드를 사전 검사하고, Argon2 뒤에는 Event→RegistrationCode 순서로
행을 잠가 mode, 유효성, 사용량을 다시 확인한다. 코드 사용량과 User·Session·audit·outbox는
하나의 transaction으로 commit되어 실패한 등록이 사용량만 소비하지 않고 concurrent 가입이
`max_uses`를 넘지 않는다. 코드를 먼저 발급한 뒤 `code` mode로 전환하며, 폐기는 되돌릴 수
없으므로 새 코드를 발급한다.

오답 Submission 자체는 HMAC과 최소 metadata로 보존하지만 오답별 OutboxEvent는 생성하지
않는다. 정답 solve·audit·outbox, 중요한 팀 변경 audit/outbox와 관리자 기록은 보존한다.
반복 `auth.login`, `auth.logout`, `auth.password_changed` audit은 사용자·action별 최신 집계
행에 `occurrences`, `first_seen_at`, `last_seen_at`을 갱신한다. password-change Outbox는 같은
사용자의 미전달 행만 병합하고 전달된 기록은 보존한다. 이 coalescing과 public hard cap은
반복 경로의 신규 행 증가를 제한하지만 전체 event storage quota나 audit/outbox 자동
retention을 구현한 것은 아니다. 중요한 다른 기록은 보존하므로 운영자는 PostgreSQL
volume·backup 증가량을 감시하고, 종료 event를 age archive로 보존한 뒤 다음 event는 빈
database에서 시작한다.

문제 설명·공지 Markdown과 접속 정보에는 secret을 넣지 않는다. challenge 접속 URL은
별도 untrusted workload로 취급하고 application origin의 cookie를 공유하지 않는다.

## Edge 보안

canonical edge는 `deploy/Caddyfile`을 사용하는 custom Caddy다. admin API와 runtime
config persistence를 끄고 다음을 적용한다.

- 자동 TLS, HSTS 1년과 HTTP→HTTPS
- 최대 2 MiB body, 32 KiB header와 명시적 timeout
- strict CSP, frame·MIME sniffing 차단, Referrer/Permissions Policy
- COOP, CORP와 cross-domain policy 차단
- API no-store와 비압축, frontend asset에만 zstd/gzip
- public readiness 404 차단; 저장소 I/O가 없는 liveness만 공개
- JSON access log

frontend의 non-root nginx도 8080, read-only root, 필요한 tmpfs, `/healthz`, strict CSP,
HTML no-store와 hashed asset immutable cache를 사용한다. standalone 방어가 있어도
production에서는 Caddy를 우회해 nginx를 직접 공개하지 않는다.

`/api/v1/health/live`는 공개 process liveness이며 DB·Redis I/O를 하지 않는다.
`/api/v1/health/ready`는 Caddy가 internal `api` network에서 backend active health에만
사용한다. 공개 경로는 404여야 하며 `make smoke`가 이 경계를 회귀 검사한다.

## image provenance와 digest

현재 build identity의 source-of-truth는 Dockerfile의 고정 builder/runtime와 검증된
`.env`로 render한 Compose의 실제 운영 image reference다. security gate는 이 운영
reference를 검사해야 하며 template의 기본값만 검사하는 것으로 대체할 수 없다. 모든
image 변경은 digest·SBOM·scan 결과를 함께 review한다.

운영 `.env`가 있으면 gate가 먼저 `validate-env.sh`로 검증하고 그 PostgreSQL·Redis
reference를 사용한다. `.env`가 없는 clean source/CI에서만 `.env.example`을 fallback으로
사용한다. 운영 image reference는 `.env`의 reviewed change로만 바꾼다.

Makefile과 backup은 `scripts/compose.sh` wrapper만 사용한다. wrapper는 `.env` 검증 후
`--project-directory`, `--env-file`, `-f`로 canonical graph를 고정하고 graph, project,
profile과 runtime interpolation 환경 override를 거부한다. gate도
`COMPOSE_FILE`·`COMPOSE_ENV_FILES`를 거부하고 JSON render가 정확히 backend, caddy,
db-roles, frontend, migrate, postgres, redis 7개 service인지 확인한다.
세 application service의 local build context, 모든 서비스의 `linux/amd64`와 render된
PostgreSQL·Redis digest가 scan 입력과 일치해야 한다. graph 확장은 gate를 포함한 reviewed
code change 없이는 실패한다.
운영자가 wrapper를 직접 호출할 수 있는 범위는 `config`, `stop`, `exec`, `run` 같은 보조
작업뿐이다. `build`와 `up`은 직접 호출하지 않는다. image build는 전체 scan을 수행하는
`make build`, service start는 유효한 private manifest를 검증하고 필요할 때 전체 gate를
다시 실행하는 `make up` 또는 `make wait`만 사용한다.

| 구성 요소 | 고정 입력 |
|---|---|
| backend builder/runtime | `python:3.12.14-alpine3.24@sha256:d09d15e60962ca365d1cd544a48773bac9d33f2fb1b00f2aa0deec78ade7dc31` |
| frontend builder | `cgr.dev/chainguard/node:latest-dev@sha256:63476ddf30fd0f79863ee0c8e1b15841ccdf25deac29051cbf166eabd3d80e6e` |
| frontend runtime | `cgr.dev/chainguard/nginx:latest@sha256:b75e46f5101f5248c274ed1153b4fe9d9d3c25b2f4c22c0634d6c7394b25283d` |
| Caddy builder | `cgr.dev/chainguard/go:latest-dev@sha256:017584bf9b817a44d8c8a9eb1ce1472753053e7accc6adfbfa81d4b077a27338` |
| Caddy runtime | `cgr.dev/chainguard/static:latest@sha256:f68e3a8244c7d0f4cd56635aaff8e6a533cf6cc3850d8fb339567a5782d6a0b0` |
| PostgreSQL | `cgr.dev/chainguard/postgres:latest@sha256:41a02d9c35a8dc6cac36188a0a201528ea8d686bb238af595867252821f609b9` |
| Redis | `cgr.dev/chainguard/redis:latest@sha256:f639a439f5ab4f14486d6c6404f388b9dc076a25f85c6939630f2f805dfad969` |

Caddy는 prebuilt image가 아니다. `deploy/caddy/Dockerfile`이 full source commit
`0cf03d32f7d99cf160d5375e8a40fbe3d910d515`을 Chainguard Go builder에서 컴파일하고
Chainguard static runtime에 binary만 복사한다. build는
`golang.org/x/crypto/openpgp`가 compiled dependency graph에 도달 가능해지면 실패한다.

## CVE gate, SBOM과 VEX

배포 후보는 다음 gate를 통과해야 한다.

```sh
set -eu
cd "$(git rev-parse --show-toplevel)"
make check-locks
SECURITY_REQUIRE_DOCKER=1 make security
```

`scripts/security-scan.sh`는 다음을 수행한다.

1. `pyproject.toml`과 backend runtime/test/build hash lock의 직접 의존성 동기화를 먼저
   확인하고 runtime, test, build, bootstrap와 security tooling lock을 `pip-audit`
   strict mode로 검사한다.
2. frontend lock을 최저 severity인 `npm audit --audit-level=info`로 검사한다.
3. checksum으로 고정한 Trivy 0.74.0으로 filesystem vulnerability, misconfiguration와
   secret을 검사한다.
4. backend/frontend/custom Caddy의 builder·runtime과 render된 실제 운영 PostgreSQL·Redis image를
   `UNKNOWN`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` 전 severity로 검사한다.
5. 각 image의 CycloneDX SBOM과 JSON report를 `security-reports/`에 만든다.

허용 근거가 없는 finding이나 감사 도구 오류는 gate를 실패시킨다. CI는 pull request,
main push, 매일 03:17 UTC와 수동 실행에서 `SECURITY_REQUIRE_DOCKER=1`을 사용한다.
따라서 Docker daemon이나 scanner를 사용할 수 없는 상태도 성공으로 처리하지 않는다.
report와 SBOM artifact는 현재 workflow에서 14일 보존한다.

`security/ctfnight.openvex.json`은 `GO-2026-5932`가
`golang.org/x/crypto/openpgp`에만 적용되고 custom Caddy compiled graph에는 그 package가
없다는 build-time 검사를 근거로 Caddy finding 하나를 `not_affected`로 기술한다. VEX는
다음 조건을 모두 만족할 때만 유지한다.

- vulnerability·package·product identity가 정확히 일치한다.
- 재현 가능한 reachability 또는 configuration 근거가 있다.
- 근거를 CI가 계속 검사하고 우회되면 build가 실패한다.
- 새 source commit이나 dependency graph에서 결론을 다시 검토한다.

Trivy 실행 전 Python exact allowlist가 statement 수를 정확히 1개로 제한하고
`GO-2026-5932`, `pkg:golang/golang.org/x/crypto@v0.54.0`, `not_affected`,
`vulnerable_code_not_in_execute_path`와 openpgp impact 근거를 완전 일치로 검사한다.
statement·product·필드를 추가하거나 waiver를 넓히면 gate가 즉시 실패한다. VEX를 전체
image의 blanket waiver나 severity downgrade 수단으로 사용하지 않는다.

`make build`는 canonical application image를 한 번만 build하고, builder와 5개 고유 service
image artifact의 exact ID를 scan해 JSON report·CycloneDX SBOM을 만든 뒤 mode 0600
deployment manifest를 원자적으로 게시한다. manifest는 source와 canonical `.env`, 7-service
rendered Compose graph, canonical reference/tag와 검사한 5개 exact ID를 함께 묶는다.
`db-roles`는 PostgreSQL, `migrate`는 backend artifact를 alias로 재사용한다.

전체 gate는 입력 snapshot 직후 기존 manifest를 unlink하고 parent directory를 `fsync`해
이전 승인을 내구성 있게 폐기한다. 모든 검사가 성공한 때만 새 manifest를 게시한다. 따라서
최신 advisory로 새 scan이 실패하면 아직 2시간 이내였던 구 manifest도 재사용할 수 없다.

scanner가 아니라 `make up`도 같은 directory-inode lock을 최종 preverify부터 postverify가
끝날 때까지 보유해 그 사이의 canonical retag·manifest 폐기를 차단한다.

`make up`과 `make wait`는 manifest가 2시간 이내이고 위 binding이 현재 상태와 완전히
일치할 때만 scan 증거를 재사용한다. manifest가 없거나 stale·tampered·drift 상태면 전체
gate를 fail-closed로 다시 실행하며 Docker·scanner 부재나 finding을 무시하지 않는다. start
직전 exact ID를 재검증하고 `up --no-build --remove-orphans`만 실행한다. poststart에는
Compose project의 orphan까지 포함한 전체 container와 service label을 열거해 canonical 7개
집합과 정확히 일대일인지 확인하고 unknown orphan·duplicate·누락을 거부한다. 이어 상시 5개
service의 running, one-shot `db-roles`·`migrate`의 exited(0), 두 alias의 exact image ID까지
검증한다. prestart 또는 poststart 검증 실패는 배포 성공으로 처리하지 않는다.

운영 one-off DB mutation도 artifact 검증을 우회하지 않는다. `make import-challenge`는
실행 직전에 2시간 freshness, source, rendered graph, canonical tag와 exact image ID binding을
`verify-prestart`로 검사하고 실패하면 import를 시작하지 않는다. 관리자 비밀번호 복구
helper는 공개 service를 멈추기 전 한 번 검사하고, marker 전환·보안 편집기 입력·ACL과 env
검증을 마친 뒤 `migrate` one-off 실행 직전에 다시 검사한다. 두 절차 모두 manifest
누락·stale·tamper·drift 또는 tag 재지정을 fail-closed로 거부한다. 두 작업은 같은
directory-inode lock을 preverify부터 one-off 종료까지 보유하고 Compose run에 `--pull never`를
강제한다. `scripts/recover-admin-password.sh` 자체도 deployment source fingerprint 입력이므로
scan 이후 script 변조는 다음 preverify에서 manifest 불일치로 거부된다.

스캔 통과는 해당 시점의 source, image와 vulnerability database에 대한 증거일 뿐이다.
미래 zero-day가 절대 0개라고 보장하는 것은 불가능하다. 매일 03:17 UTC gate는 정기 탐지
지연을 주 단위에서 하루 수준으로 낮추는 운영 보완일 뿐 취약점 부재 보장이 아니다. 운영자는
scanner와 advisory database를 최신으로 유지하고 dependency·digest 갱신, 배포 직전 전체
scan, artifact 검토와 fail-closed 정책을 계속 운영해야 한다.

## age 암호화 backup

`scripts/backup.sh`는 `ALPHA_BACKUP_AGE_RECIPIENT`의 public recipient와 `age`가 없으면
실패한다. age identity는 application server, Git, `.env`, backup archive와 CI artifact에
두지 않고 별도 보안 매체에 보관한다.

script는 `/dev/shm` tmpfs에서만 PostgreSQL custom dump, `.env`, 여섯 secret,
Compose/Caddy 설정, image 목록, source revision·dirty 상태와 내부 checksum을 조립한다.
영구 backup 디렉터리에 게시되는 파일은 다음 두 개뿐이다.

```text
ctfnight-YYYYMMDDTHHMMSSZ.tar.gz.age
ctfnight-YYYYMMDDTHHMMSSZ.tar.gz.age.sha256
```

복구할 때 외부 `.sha256`을 먼저 확인한 뒤 `age --decrypt --output`을 독립 명령으로
완료한다. 복호화 성공 전에는 tar에 연결하지 않는다. tmpfs의 평문 tar를 `tar -tzf`로
검사하고 추출한 뒤 내부 `SHA256SUMS`와 manifest/source/image를 순서대로 검증한다.
성공·실패·signal 모두에서 EXIT trap이 정확히 제한한 tmpfs archive와 작업 디렉터리를
제거해야 한다. 새 host에는 첫 기동 전에 archive의 `.env`와 `.secrets`를 복구한 뒤
`make secret-acl`로 UID 65532 read ACL을 다시 적용한다. 검증 가능한 전체 절차는
[README.md](README.md#복구)를 따른다.

backup은 생성 사실만으로 충분하지 않다. 대회 전과 중요한 upgrade 전 격리 환경에서
복구를 연습하고 관리자 로그인, 문제, 제출 감사와 최종 점수판을 확인한다.

## 보안 migration과 배포

production database는 PostgreSQL만 지원한다. development/test SQLite도 매 runtime·online
migration connection에서 `PRAGMA foreign_keys=ON`을 설정하고 즉시 `1`인지 확인하며, 활성화할
수 없으면 연결을 fail-closed한다. Alembic online migration은 실행 전과 후에
`PRAGMA foreign_key_check`를 수행하고 violation이 하나라도 있으면 실패한다.

`20260824_0002`는 User와 Session에 `credential_version`을 함께 추가한다. session
generation 검사와 schema가 동시에 도입되므로 rolling 중 구·신 backend를 혼합하지
않고 maintenance/all-at-once로 배포한다.

`20260824_0003`은 기존 `max_attempts > 1000`을 1,000으로 낮추고 PostgreSQL CHECK와 SQLite
INSERT/UPDATE trigger로 동등한 DB upper bound를 추가한다. submission runtime hard cap과
함께 같은 maintenance/all-at-once 절차로 배포한다.

`20260824_0004`는 Event의 `registration_access_mode`와 `registration_codes` table을 추가한다.
PostgreSQL CHECK와 SQLite trigger가 `open|code` mode를 제한하고, code table은 HMAC 길이,
label, 사용량·상한과 폐기 상태를 DB 제약으로 방어한다. 등록 코드 runtime과 같은
maintenance/all-at-once 절차로 배포한다.

1. 배포 image를 미리 빌드하고 전체 security gate를 통과시킨다.
2. upgrade 직전 age backup과 checksum/identity를 검증한다.
3. Caddy와 모든 frontend/backend replica를 정지한다.
4. `make up`의 `db-roles`와 `migrate` one-shot이 역할 수렴과 `alembic upgrade head`를 완료한다.
5. 같은 revision의 전체 backend/frontend를 올린 뒤 Caddy를 연다.
6. health, 로그인, 등록 코드 사용·소진·폐기와 참가자 정지 뒤 이전 session 거부를 회귀 확인한다.

schema 적용 후 code만 rollback하지 않는다. 실패하면 이전 source/image와 upgrade 직전
backup을 함께 복구한다. 명령은 [README.md](README.md#업그레이드와-migration)에 있다.

## 사고 대응 체크리스트

1. 공개 traffic 또는 영향 서비스만 먼저 격리하고 container·volume을 즉시 삭제하지 않는다.
2. 시각, source revision, image digest, request ID, audit/outbox와 최소 log를 보존한다.
3. token·password·root key·DB 중 실제 노출 범위를 구분한다.
4. password 사건은 CLI reset과 `credential_version`으로 session을 폐기한다.
5. root key 사건은 session·invite·등록 접근 코드·exact flag 전체 재발급 계획과 maintenance를 세운다.
6. 취약 image는 digest/lock을 갱신하고 all-severity gate, 회귀 test와 새 SBOM을 통과시킨다.
7. VEX를 추가할 경우 exploitability 근거와 자동 검사를 함께 review한다.
8. 검증된 age backup으로 복구할 때 source/image/secret 시점을 맞춘다.
9. 재개 전 `make validate`, Compose render, 전체 security gate, health와 `make smoke`를
   수행한다.
10. 원인·영향·조치·남은 위험을 기록하고 같은 공격 경로의 회귀 test를 추가한다.
