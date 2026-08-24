# CTFnight

CTFnight는 FastAPI, React, PostgreSQL, Redis와 Caddy를 하나의 Docker Compose 배포로
묶은 Jeopardy형 CTF 플랫폼이다. 참가자·팀·문제·점수판과 관리자 운영 화면을 제공하며,
문제 컨테이너나 Docker socket은 웹 애플리케이션에 연결하지 않는다.

이 디렉터리가 애플리케이션과 배포의 canonical root다. `Alpha`, Python의 `alpha`
namespace와 `ALPHA_*` 설정명은 내부 호환 식별자로 유지하고, 사용자 표시 제품명과 새
설치의 기본 이벤트 slug는 `CTFnight`, `ctfnight`다.

운영 전에 다음 문서를 함께 읽는다.

- [ARCHITECTURE.md](ARCHITECTURE.md): 구성 요소, 데이터·네트워크 경계, 구현 불변식
- [SECURITY.md](SECURITY.md): 운영 보안 기준, CVE gate, VEX와 사고 대응
- [backend/README.md](backend/README.md): backend 설정, 인증·CSRF와 개발 명령
- [frontend/README.md](frontend/README.md): frontend 개발·빌드 계약
- [templates/README.md](templates/README.md): challenge-as-code 형식

## 제공 범위와 제외 범위

현재 배포는 공개 또는 접근 코드 기반 가입, 로그인, 참가자 정지·복구, 개인전·팀전,
팀 소유권 이전·멤버 제거, 공지, 정적·정규식 flag, 고정·동적 점수, 중복 solve 방지,
제출 제한, 점수판 동결, 관리자 감사 조회와 YAML 문제 import를 제공한다. 하나의 배포와
데이터베이스에는 하나의 current event만 둔다.

Attack–Defense, 팀별 문제 컨테이너, attachment upload와 Kubernetes runtime broker는
포함하지 않는다. 취약한 challenge workload는 별도 host·network·domain에 격리해야 한다.

## 요구 사항

- Linux x86_64(`linux/amd64`) host와 rootful Docker Engine 24 이상; 현재 scanner·CI와
  모든 Compose 서비스가 이 조합만 검증
- `gw_priority`를 지원하는 Docker Compose v2.33.1 이상 또는 호환되는 최신 버전
- POSIX ACL을 지원하는 host filesystem과 `setfacl`·`getfacl`을 제공하는 `acl` package
- security gate 직렬화를 위해 `flock`을 제공하는 Linux `util-linux` package
- `make`, `openssl`, `curl`, `python3`, `tar`, Node.js와 npm
- 암호화 백업을 위한 `age`
- `make build`, `make up`, `make wait`의 fail-closed image gate를 위한 Docker daemon
- 공개 배포에는 서버 IPv4를 가리키는 DNS A와 TCP 80·443, 필요 시 UDP 443

공개 Compose는 `0.0.0.0`의 IPv4 80·443에 bind한다. 외부 IPv6 proxy를 별도로 검토하지
않았다면 AAAA를 게시하지 않는다. 공개 자동 TLS port를 다른 reverse proxy가 점유하면
안 된다.

application·edge·cache와 one-shot container는 UID/GID `65532:65532`로 고정된다. 유일한
예외인 고정 Chainguard PostgreSQL image는 공식 entrypoint 계약대로 exact capability를 가진
UID/GID `0:0`에서 named volume을 준비한 뒤 server PID 1을 내장 `postgres` UID/GID `70:70`으로
즉시 낮춘다. CI는 실행 중 PID 1의 real/effective/saved/filesystem UID가 모두 70인지 확인한다.
rootless Docker나 user namespace remap에서는 UID 65532·70과 초기 root의 host mapping 및
volume ownership 계약이 달라 현재 지원·검증하지 않는다.

`scripts/security-scan.sh`, `make up`, `make import-challenge`와 관리자 복구 helper는 모두
`security-reports/` directory inode의 같은 비차단 `flock -n`을 사용한다. scanner는 전체
gate, `make up`은 최종 preverify부터 up·postverify까지, 두 one-off 작업은 preverify부터
DB mutation 종료까지 잠금을 유지한다. 다른 canonical 작업이 report를 폐기하거나 image
tag를 바꾸려 하면 기다리지 않고 즉시 실패한다.

## 배포 모드

`generate-env.sh`가 주소에서 모드를 결정한다. 공개 배포를 development로 낮추거나
loopback HTTP를 production으로 올리는 수동 조합은 검증 단계에서 거부된다.

| 주소 | 모드 | 쿠키 | 용도 |
|---|---|---|---|
| `http://localhost` 또는 `http://127.0.0.1` | `development` | Secure 없음, 개발용 이름 | host `127.0.0.1`에만 bind |
| DNS 이름 또는 `https://` origin | `production` | Secure `__Host-*` | host `0.0.0.0`에 bind |

공개 평문 HTTP 주소는 허용하지 않는다. 로컬 HTTP 포트만 `ALPHA_HTTP_PORT`로 바꿀 수
있으며, 공개 자동 TLS는 80/443을 유지한다.

## 빠른 시작

### 로컬 Compose

```sh
set -eu
git clone https://github.com/Sh1nY9/CTFnight.git
cd CTFnight
make init
make up
make smoke
```

`make init`은 기존 `.env`나 `.secrets/`를 덮어쓰지 않는다. 기본 주소는
`http://localhost`이며 Caddy port도 host loopback에서만 접근할 수 있다. `make up`은 먼저
mode 0600 deployment manifest가 2시간 이내이고 현재 source, `.env`, rendered Compose
graph와 canonical image reference가 검사된 exact image ID에 완전히 묶여 있는지 검증한다.
유효하면 그 증거를 재사용하고, 없거나 stale·drift 상태면 전체 Docker security gate를
fail-closed로 다시 실행한다.

상태와 로그는 다음으로 확인한다.

```sh
make ps
make logs
```

### 공개 서버

새 checkout에서 DNS와 방화벽을 준비한 뒤 실행한다.

```sh
set -eu
git clone https://github.com/Sh1nY9/CTFnight.git
cd CTFnight
ALPHA_SITE_ADDRESS=ctf.example.com \
ALPHA_ADMIN_EMAIL=operator@example.com \
ALPHA_ADMIN_USERNAME=operator \
make init

make config
make up
make smoke
```

Caddy는 DNS 이름에 대해 인증서를 자동 발급하고 HTTP를 HTTPS로 전환한다. 인증서와
ACME 계정은 `caddy_data` volume에 보존된다. edge는 HSTS, strict CSP, COOP/CORP,
2 MiB 요청 본문 제한과 API `no-store`를 적용한다.

공개 liveness는 I/O 없는 `/api/v1/health/live`다. PostgreSQL·Redis를 조회하는
`/api/v1/health/ready`는 Caddy의 internal active health 전용이며 public 요청에는 404를
반환한다. `make smoke`는 live 대기와 public readiness 차단을 모두 확인한다.

시작 순서는 다음과 같다.

1. PostgreSQL과 Redis healthcheck가 통과한다.
2. `db-roles` one-shot이 owner로 접속해 `alpha_migrator`와 `alpha_app`을 수렴시킨다.
3. `migrate` one-shot이 migrator로 `alembic upgrade head`와 bootstrap을 실행한다.
4. runtime credential만 가진 backend와 frontend가 healthy가 되면 Caddy가 요청을 받는다.

기본 이벤트는 `draft`다. 초기 관리자 비밀번호를 바꾸고 일정·문제·공지를 검토한 뒤
`registration`, `live` 순서로 한 단계씩 전환한다.

## 운영 비밀값

`.env`는 mode 0600의 비밀이 아닌 배포 설정 파일이다. 비밀값은 owner-only mode 0700인
`.secrets/` 아래 여섯 파일에만 저장되고 Compose가 `/run/secrets/*`로 mount한다. 각 파일의
POSIX ACL은 host owner `rw-`, container UID 65532 `r--`, group·other 없음으로 고정한다.
`make init`은 이 ACL을 만들고 `make validate`는 `getfacl -cpn` 결과를 완전 일치로 검사한다.
PostgreSQL owner secret은 초기 root entrypoint가 `DAC_OVERRIDE`로 읽고 UID 70으로 낮춘 뒤에는
읽을 수 없으며, 이후 role provisioning은 UID 65532인 `db-roles` one-shot이 담당한다.

| 파일 | 용도 |
|---|---|
| `.secrets/alpha_secret_key` | session·CSRF·invite·등록 접근 코드·exact flag HMAC root key |
| `.secrets/postgres_owner_password` | initdb·role provisioning·backup owner 인증 |
| `.secrets/postgres_migrator_password` | migration/bootstrap 인증 |
| `.secrets/postgres_runtime_password` | backend CRUD runtime 인증 |
| `.secrets/redis_password` | Redis 인증 |
| `.secrets/admin_password` | 최초 bootstrap 또는 일회성 복구 비밀번호 |

검증기는 `.env`에 직접 기록한 `ALPHA_SECRET_KEY`, `ALPHA_POSTGRES_PASSWORD`,
`ALPHA_REDIS_PASSWORD`, `ALPHA_ADMIN_PASSWORD`를 거부한다. 비밀 파일은 symlink가 아닌
일반 UTF-8 한 줄이어야 한다. 운영 검증기는 권한과 형식을, backend reader는 파일 종류,
형식과 크기를 각각 방어적으로 검사한다.

```sh
make validate
```

### secret 수동 교체·복구 후 ACL

보안 편집기, 복구 archive 또는 배포 도구가 secret inode를 새로 만들면 named ACL이 사라질
수 있다. 여섯 파일이 모두 제자리에 있는지 확인한 뒤 다음 canonical target으로 directory
ACL과 container read ACL을 다시 적용하고 즉시 검증한다.

```sh
set -eu
cd "$(git rev-parse --show-toplevel)"
make secret-acl
getfacl -cpn -- .secrets .secrets/alpha_secret_key \
  .secrets/postgres_owner_password .secrets/postgres_migrator_password \
  .secrets/postgres_runtime_password .secrets/redis_password .secrets/admin_password
```

`make secret-acl`은 `.secrets` basename, symlink·hard link와 여섯 고정 파일을 검사한 뒤 기존
ACL을 지우고 정확한 UID 65532 read ACL만 설정한다. credential 내용이나 DB 상태를 회전하지
않으므로 각 secret의 실제 교체 절차와 service 재개는 해당 maintenance 절차를 따른다.

`.env`에서 주로 관리하는 값은 다음과 같다.

| 변수 | 용도 |
|---|---|
| `ALPHA_COMPOSE_PROJECT_NAME` | event별 network·volume·로컬 image namespace |
| `ALPHA_SITE_ADDRESS` | loopback HTTP 또는 공개 HTTPS DNS 이름 |
| `ALPHA_BIND_ADDRESS` | development `127.0.0.1`, production `0.0.0.0` |
| `ALPHA_HTTP_PORT`, `ALPHA_HTTPS_PORT` | 위 bind address의 Caddy host 포트 |
| `ALPHA_ENVIRONMENT` | 생성기가 정하는 `development` 또는 `production` |
| `ALPHA_COOKIE_SECURE` | 생성기가 주소에 맞춰 설정 |
| `ALPHA_ALLOWED_ORIGINS`, `ALPHA_TRUSTED_HOSTS` | browser origin과 Host allowlist |
| `ALPHA_POSTGRES_DB`, `ALPHA_POSTGRES_*_USER` | 비밀이 아닌 DB/owner/migrator/runtime 식별자 |
| `ALPHA_ADMIN_EMAIL`, `ALPHA_ADMIN_USERNAME` | bootstrap 관리자 식별자 |
| `ALPHA_ADMIN_BOOTSTRAPPED` | 빈 관리자 secret을 허용하는 운영 복구 marker(`true`/`false`) |
| `ALPHA_BACKUP_AGE_RECIPIENT` | 백업을 암호화할 공개 age recipient |
| `POSTGRES_IMAGE`, `REDIS_IMAGE` | 검증된 Chainguard manifest digest |

`ALPHA_ADMIN_BOOTSTRAPPED`는 Compose service 환경이나 build argument로 전달되지 않는
운영 복구 상태다. deployment source digest는 이 key가 정확히 한 번 있고 값이 `true` 또는
`false`인지 확인한 뒤 그 값만 고정된 `<operational-marker>`로 정규화한다. 따라서 복구 중
marker 전환만으로 기존 artifact 승인이 무효화되지는 않지만, 그 밖의 `.env` 내용이 한
바이트라도 달라지면 drift로 검출한다.

### 최초 관리자 비밀번호 폐기

초기 비밀번호는 `.secrets/admin_password`에만 있으며 생성기는 값을 출력하지 않는다.
첫 로그인 직후 `/account/security`에서 새 비밀번호로 변경한 다음 다음 절차를 수행한다.

```sh
set -eu
app_root=$(git rev-parse --show-toplevel)
cd "$app_root"
truncate -s 0 .secrets/admin_password
# .env에서 ALPHA_ADMIN_BOOTSTRAPPED=true로 변경한다.
make secret-acl
make up
```

파일 경로는 production 설정의 필수 계약이므로 파일을 삭제하지 않고 내용만 비운다.
gated `make up`으로 stack을 재생성하면 bootstrap secret이 프로세스 메모리에도 남지 않는다.
이후 비밀번호 복구는 [backend 복구 helper](backend/README.md#관리자-비밀번호-복구)만 사용한다.
helper는 같은 deployment lock을 잡고 시작 시 한 번, marker·secret 편집과 ACL/env 검증 뒤
one-off DB mutation 직전에 한 번 더 동일한 2시간 manifest binding을 검증한다. 검증 뒤
registry에서 image를 가져오지 않도록 one-off Compose run에는 `--pull never`를 강제하고,
같은 lock으로 canonical tag 재지정을 막는다.

## 서비스와 네트워크 경계

| 서비스 | 연결 network | host publish | 영구 상태·실행 형태 |
|---|---|---|---|
| `caddy` | `public`, `api`, `web` | TCP 80/443, UDP 443 | `caddy_data` |
| `frontend` | `web` | 없음 | 없음 |
| `backend` | `api`, `database`, `cache` | 없음 | 없음 |
| `db-roles` | `database` | 없음 | one-shot, PostgreSQL role provisioning |
| `migrate` | `database` | 없음 | one-shot, schema migration·bootstrap |
| `postgres` | `database` | 없음 | `postgres_data` |
| `redis` | `cache` | 없음 | `redis_data` |

canonical graph는 7개 service다. `postgres`가 healthy가 되면 `db-roles`가 역할을 구성하고
exit 0으로 끝나며, 이어서 `migrate`가 migration·bootstrap을 마치고 exit 0이어야 backend가
시작한다. 상시 실행 service는 backend, caddy, frontend, postgres, redis 5개다.

`api`, `web`, `database`, `cache`는 각각 `internal: true`인 분리 network다. 인터넷
egress가 가능한 `public`에는 Caddy만 연결된다. 따라서 frontend는 backend나 저장소에,
Caddy는 저장소에 직접 접근할 수 없다.

번들 PostgreSQL·Redis의 평문 transport 예외는 정확히 이름이 `postgres`, `redis`인
격리된 Compose 서비스에만 적용된다. backend는 외부 PostgreSQL에서
`ALPHA_DATABASE_TLS=true`와 `sslmode=verify-full`, 외부 Redis에서
`ALPHA_REDIS_TLS=true`와 `rediss://`를 강제한다. 다만 기본 Compose는 local 저장소와
internal network를 전제로 하므로 외부 저장소를 쓰려면 검토된 override, backend egress,
CA trust, `depends_on`과 백업 절차를 canonical Compose·validator·security gate의 reviewed
code change로 함께 바꿔야 한다. 임의 Compose override나 단순 `.env` 변경으로 전환하지
않는다.

## 이벤트 운영

상태는 다음 순서로만 전환된다.

```text
draft → registration → live → frozen → ended → archived
```

일정 필드는 자동 전환기가 아니라 추가 server gate다. `start_at` 전에는 live여도 문제와
제출이 닫히고, `end_at` 뒤에는 제출을 받지 않는다. frozen 이후 공개 점수판은
`freeze_at` 이후 solve를 숨기고, ended에서 최종 결과를 공개한다. archived에서는 이벤트,
문제와 공지가 읽기 전용이다.

이벤트의 `registration_access_mode`는 `open` 또는 `code`다. 기본값 `open`은 등록 상태와
일정 gate를 통과한 사용자의 가입을 허용하고, `code`는 이에 더해 현재 이벤트의 유효한 등록
접근 코드를 요구한다. 운영자는 코드 모드를 켜기 전에 관리자 설정 화면에서 코드를 먼저
발급한다. 각 코드는 1~10,000회의 사용 상한 또는 무제한, 선택적 만료 시각과 폐기 상태를
가진다. 평문은 생성 응답에 한 번만 나타나며 DB·목록·audit/outbox에는
`registration-access` domain HMAC과 metadata만 남는다.

누락·미등록·만료·소진·폐기 코드는 모두 HTTP 403 `registration_access_denied`로 동일하게
거부한다. 등록은 rate limit 뒤, Argon2 전에 코드를 사전 확인하고 Argon2 뒤에는 Event와
RegistrationCode 행을 다시 잠가 최신 상태를 검증한다. 코드 사용 횟수 증가, User·Session,
audit와 outbox는 한 transaction으로 commit되므로 실패한 가입이 사용량만 소비하거나 동시
가입이 상한을 초과하지 않는다.

동적 문제의 solve `ScoreEvent`에는 각 solve가 발생했을 때의 solve 수로 계산한 점수를
기록하며, 이후 solve 때문에 과거 행을 bulk rewrite하지 않는다. 공개 점수판은 이 이력값을
합산하는 대신 현재 점수판 phase에 보이는 solve 수로 동적 문제의 점수를 다시 계산한다.

공개 점수판은 전체 Solve를 Python에 적재하지 않고 문제별 solve 수, 팀별 점수·solve 수와
마지막 solve 시각을 SQL aggregate로 계산한다. 정렬 뒤 hard top 1,000개만 응답하고 전체
순위 항목 수는 `total_entries`, 잘림 여부는 `truncated`로 알린다. `/api/v1/meta`도
`limits.max_public_scoreboard_entries=1000`을 공개한다.

cold cache 재구축은 event·phase별 45초 Redis lease 하나로 single-flight하며, lease는 발급
token 소유자만 해제할 수 있다. cache는 `{generation, payload}` envelope로 저장하고 정답
solve와 점수판에 영향을 주는 admin mutation이 generation을 올린다. 집계 중 generation이
바뀌면 stale 결과를 게시하지 않는다. 점수판 cache·lease 조작 장애는 HTTP 503, 아직 cache가
없는 상태에서 lease를 얻지 못한 요청도 HTTP 503과 `Retry-After: 1`로 fail-closed한다.

다음 대회는 archived DB를 재사용하지 않는다. age 백업과 복구 검증을 마친 뒤 고유한
`ALPHA_COMPOSE_PROJECT_NAME`, 새 checkout과 빈 volume으로 시작한다.

### 저장공간·남용 상한과 보존

public mutation 경로에는 환경변수로 완화할 수 없는 다음 hard cap이 적용된다.

| 범위 | 상한 | 도달 시 동작 |
|---|---:|---|
| 한 팀의 한 문제 제출(`max_attempts=0`) | 1,000개 | HTTP 409 `submission_storage_limit_reached` |
| 한 팀의 한 이벤트 전체 제출 | 10,000개 | HTTP 409 `submission_storage_limit_reached` |
| 한 팀의 구성원 | 100명 | HTTP 409 `team_capacity_reached` |
| 한 사용자의 한 이벤트 팀 구성 변경 | 100회 | HTTP 409 `team_mutation_limit_reached` |
| 전체 participant 계정 | 100,000명 | HTTP 409 `participant_capacity_reached` |
| 한 사용자의 active session | 10개 | 새 session 생성 시 가장 오래된 active session 폐기 |

문제의 `max_attempts`가 1~1,000이면 그 설정 상한에서 HTTP 409
`attempt_limit_reached`를 반환한다. `max_attempts=0`은 문제 설정상 별도 제한이 없다는
뜻이지만 시스템 hard cap 1,000은 그대로 적용된다. 팀 구성 변경은 생성·가입·초대 회전·
소유권 이전·멤버 제거·탈퇴를 합산하며, lifetime cap에 앞서 기본 사용자 session당
20회/시간과 source IP당 100회/시간 Redis limit을 적용한다. Redis가 실패하면 HTTP 503으로
변경을 허용하지 않는다.
기존 idempotency key의 replay는 cap 검사 전에 원래 결과를 반환하고 새 행을 소비하지 않는다.
팀 가입은 Team row lock 안에서 구성원 수를 세므로 동시 가입도 100명을 넘지 않는다.
`GET /api/v1/meta`는 이 계약을 `limits.max_members_per_team=100`으로 공개하지만 최종 검사는
backend transaction이 수행한다.

소유자만 등록 기간의 팀전에서 활성 참가자 멤버에게 소유권을 이전하거나 멤버를 제거할 수
있다. 해당 팀에서 Submission을 한 사용자는 탈퇴하거나 제거할 수 없어 오답을 포함한 제출
이력과 시도 제한을 다른 팀으로 옮겨 초기화하지 못한다. 멤버 제거가 성공하면 현재 초대
코드를 같은 transaction에서 회전해 제거된 사용자의 즉시 재가입을 막고, 새 평문 초대 코드는
응답과 화면에 한 번만 표시하며 audit/outbox에는 기록하지 않는다.

제출의 PostgreSQL lock 순서는 `Event → User → Membership → Team → Challenge`다. Redis
rate 검사는 대기 가능한 actor row lock보다 먼저 수행하고, 통과한 뒤 User, Membership과
Team을 순서대로 잠근다. 이때 Membership을 다시 읽어 같은 팀인지 재검증하며, 정답 후보만
Challenge를 잠근 뒤 최신 flag로 다시 확인한다. 오답 제출은 공개 점수판 cache를
무효화하지 않고, 정답 commit만 `live`, `frozen`, `final` cache를 무효화한다.

계층형 Redis rate 검사는 인증 scope의 `identity → IP → registration global`, 팀 변경의
`session → IP`, 제출의 `team → IP → challenge` 순서로 한 tier씩 실행한다. 첫 deny에서 즉시
중단하므로 좁은 identity·session·team bucket이 거부한 요청은 뒤의 공유 IP와
global·challenge budget을 소비하지 않는다. 어느 tier의 store 오류도 HTTP 503이다.

등록은 기존 identity 20회/60초, source IP 200회/60초 제한과 별도로 전체 배포에서
1,000회/60초 Redis budget을 사용한다. 서로 다른 identity·IP로 분산해도 global key를
공유하며 Redis 장애는 HTTP 503으로 fail-closed한다. Argon2 계산 뒤 Event 행을 exclusive
lock하고 participant 수와 등록 가능 상태를 다시 검사한 다음 User를 insert하므로 concurrent
N+1 등록도 100,000명을 넘지 않는다.

새 로그인은 만료 session을 제한된 batch로 정리하고, 해당 사용자의 가장 최근 active
session 9개만 남긴 뒤 새 session을 추가한다. 따라서 사용자당 active session은 10개이며
밀려난 가장 오래된 token은 더 이상 인증되지 않는다. 비밀번호 변경은 회전하는 session
token key 외에 stable user-ID rate key도 검사하므로 성공할 때마다 token을 바꿔 budget을
우회할 수 없다.

관리자는 참가자 계정을 사유와 함께 정지하고 다시 활성화할 수 있지만 관리자 계정은 이
경로의 대상이 아니다. 정지는 User row를 잠근 뒤 `credential_version`을 증가시키고 모든
SessionToken을 삭제한다. 제출·비밀번호 변경·팀 mutation도 최종 쓰기 전에 User를 잠그고
active 상태, version과 필요한 session을 다시 확인한다. 따라서 정지가 먼저 commit된 뒤에는
초기 인증 snapshot만으로 진행 중 요청이 쓰기를 완료할 수 없고, 반대로 요청이 User lock을
먼저 얻었다면 그 transaction이 정지보다 먼저 직렬화된다. 재활성화는 기존 폐기 session을
복원하지 않으므로 사용자가 새로 로그인해야 한다.

Submission 행은 정답·오답 모두 HMAC과 audit에 필요한 최소 metadata로 이벤트 DB에
보존하지만, 오답마다 OutboxEvent를 만들지는 않는다. 정답 solve·audit·outbox와 중요한 팀
변경 audit/outbox, 관리자 변경 기록은 보존한다. 반복되는 `auth.login`, `auth.logout`,
`auth.password_changed` audit은 사용자·action별 최신 집계행에 `occurrences`,
`first_seen_at`, `last_seen_at`을 누적하며 기존 과거 행을 삭제하지 않는다.
`user.password_changed` Outbox도 같은 사용자의 미전달(`delivered_at IS NULL`) 행에 횟수를
병합하고 전달된 행은 보존한다.

이는 공개 반복 경로의 행 수와 남용을 제한하는 정책이지 모든 audit/outbox의 자동 retention,
전역 disk quota나 기간 기반 삭제를 구현한 것이 아니다. 중요한 다른 기록은 보존되므로
PostgreSQL volume과 age backup 용량을 모니터링해야 한다. 이벤트 종료 후에는 검증한 age
archive를 보존하고, 다음 이벤트는 새 project와 빈 database에서 시작하는 것이 canonical
retention 경계다.

## 문제 import

```sh
make import-challenge CHALLENGE=challenges/welcome/challenge.yaml
make import-challenge CHALLENGE=challenges/dynamic-regex/challenge.yaml
```

Make target은 private YAML을 장기 실행 backend에 mount하지 않고 일회성 컨테이너의
표준 입력으로 전달한다. DB mutation 직전에는 deployment manifest가 2시간 이내이고 현재
source·rendered Compose graph·canonical tag와 검사한 exact image ID의 binding이 모두
일치하는지 `verify-prestart`로 확인하며, 누락·stale·drift·tag 재지정이면 import를 시작하지
않는다. 같은 directory-inode lock을 preverify부터 one-off 종료까지 유지하고 Compose run에
`--pull never`를 강제하므로 검증과 실행 사이에 같은 canonical 운영 경로가 시도하는
retag·manifest 폐기를 거부한다.
exact flag 원문은 import transaction 안에서 HMAC으로 바뀐다.
운영 파일은 Git에서 제외된 `templates/.private/`에 mode 0600으로 잠깐 보관한다.
`FLAG{...}`는 예시일 뿐 실제 형식은 문제별 `exact` 또는 `regex` 규칙이 정한다.

## age 암호화 백업

먼저 별도 보안 매체에 age identity를 만들고 공개 recipient만 `.env`의
`ALPHA_BACKUP_AGE_RECIPIENT`에 기록한다. identity는 애플리케이션 서버의 backup,
Git 또는 CI artifact에 넣지 않는다.

```sh
set -eu
make validate
make backup
# 또는 mode 0700의 별도 mount
make backup BACKUP_DIR=/srv/ctfnight-backups
```

백업은 기본적으로 `/dev/shm` tmpfs에서만 암호화 전 자료를 조립하고 즉시 age로
암호화한다. recipient가 없거나 staging이 tmpfs가 아니면 실패한다. 게시 산출물은 다음
두 파일뿐이다.

```text
ctfnight-YYYYMMDDTHHMMSSZ.tar.gz.age
ctfnight-YYYYMMDDTHHMMSSZ.tar.gz.age.sha256
```

암호화 archive에는 PostgreSQL custom-format dump, `.env`, 여섯 secret, Compose/Caddy
설정, image 목록, source revision·dirty 상태, 내부 `SHA256SUMS`가 포함된다. Redis의
cache/rate-limit 상태와 재발급 가능한 Caddy 인증서는 제외된다.

### 복구

복구는 현재 DB를 교체하는 maintenance 작업이다. 대상 project와 archive를 다시 확인하고
현재 상태의 새 암호화 백업을 만든 뒤 실행한다. 자동 restore script는 제공하지 않는다.

```sh
set -eu
umask 077

archive=/srv/ctfnight-backups/ctfnight-YYYYMMDDTHHMMSSZ.tar.gz.age
archive_dir=${archive%/*}
archive_name=${archive##*/}
identity=/secure/ctfnight-backup.agekey
restore_root=
plaintext_archive=

cleanup_restore() {
  case "${plaintext_archive:-}" in
    /dev/shm/ctfnight-restore.??????.tar.gz)
      [ ! -f "$plaintext_archive" ] || rm -f -- "$plaintext_archive"
      ;;
  esac
  case "${restore_root:-}" in
    /dev/shm/ctfnight-restore.??????)
      [ ! -d "$restore_root" ] || rm -rf -- "$restore_root"
      ;;
  esac
}
trap cleanup_restore EXIT
trap 'exit 130' HUP INT TERM

(cd "$archive_dir" && sha256sum -c "$archive_name.sha256")
plaintext_archive=$(mktemp /dev/shm/ctfnight-restore.XXXXXX.tar.gz)
restore_root=$(mktemp -d /dev/shm/ctfnight-restore.XXXXXX)
age --decrypt --identity "$identity" \
  --output "$plaintext_archive" "$archive"
tar -tzf "$plaintext_archive"
tar -xzf "$plaintext_archive" -C "$restore_root"
(cd "$restore_root" && sha256sum -c SHA256SUMS)
```

`age`가 성공한 뒤에만 tar 목록 검사와 추출이 실행된다. `identity` 경로는 복구 시간에만
read-only로 연결한 별도 보안 매체를 가리켜야 한다. 같은 shell session에서
`manifest.txt`, `images.txt`와 source revision을 대조한다. 새 서버라면 첫 기동 전에
복호화한 `.env`를 mode 0600으로, `secrets/*`를 `.secrets/`에 복원한 뒤 첫 기동 전에
`make secret-acl`을 실행한다. tar archive는 host POSIX ACL을 보존하지 않으므로 mode만
복구하고 끝내면 UID 65532 container가 secret을 읽을 수 없거나 잘못된 ACL이 남을 수 있다.
기존 서버에서 같은 shell session으로 DB를 교체하는 예시는 다음과 같다.

```sh
set -eu
app_root=$(git rev-parse --show-toplevel)
cd "$app_root"
: "${restore_root:?같은 shell에서 앞의 archive 검증·추출 절차를 먼저 실행하세요.}"
command -v cleanup_restore >/dev/null
make backup BACKUP_DIR=/srv/ctfnight-pre-restore
./scripts/compose.sh stop caddy frontend backend
./scripts/compose.sh exec -T --user 70:70 postgres sh -ec \
  'dropdb --if-exists --username "$POSTGRES_USER" "$POSTGRES_DB"'
./scripts/compose.sh exec -T --user 70:70 postgres sh -ec \
  'createdb --username "$POSTGRES_USER" "$POSTGRES_DB"'
./scripts/compose.sh exec -T --user 70:70 postgres sh -ec \
  'pg_restore --exit-on-error --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" --no-owner' \
  < "$restore_root/database.dump"
make up
make smoke
cleanup_restore
trap - EXIT HUP INT TERM
```

복구 후 관리자 로그인, 문제, 제출 감사와 최종 점수판을 확인한다. 중간 명령이 실패하거나
signal을 받아도 EXIT trap이 명시적으로 제한한 tmpfs 평문 archive와 작업 디렉터리를
제거한다. 성공 시에도 예시의 마지막 두 명령으로 즉시 제거한다.

## 업그레이드와 migration

schema가 바뀌지 않는 lock/image-only release는 source와 image를 release 단위로 함께
올린다.

```sh
set -eu
make backup
make up
make smoke
```

`migrate` one-shot이 backend 시작 전에 항상 `alembic upgrade head`를 실행하므로 schema
변경 release에 위의 all-at-once 절차를 사용한다. schema 변경을 코드만 rollback하는 것도
안전하지 않으며, 실패 시 이전 source/image와 업그레이드 직전 age backup을 함께 복구한다.

특히 `20260824_0002`의 `credential_version` migration은 비밀번호 변경·복구와 session
generation 검증을 하나의 계약으로 도입한다. 이 release는 rolling으로 혼합하지 않고
maintenance/all-at-once로 배포한다.

`20260824_0003`은 기존 `max_attempts > 1000` 값을 1,000으로 낮춘 뒤 PostgreSQL에는 CHECK,
SQLite에는 동등한 INSERT/UPDATE trigger 상한을 추가한다. 새 runtime의 submission hard
cap과 같은 계약이므로 이 migration도 아래 schema maintenance 절차에 포함한다.

`20260824_0004`는 Event에 `registration_access_mode`를 기본값 `open`으로 추가하고
`registration_codes` table, 사용량·label·HMAC 길이·폐기 상태 제약과 index를 만든다.
PostgreSQL은 CHECK, SQLite는 event mode에 동등한 trigger를 사용한다. 코드 모드 runtime과
schema를 따로 배포하지 않고 같은 maintenance/all-at-once 절차에 포함한다.

1. image를 미리 빌드하고 암호화 백업을 검증한다.
2. Caddy와 모든 frontend/backend replica를 정지한다.
3. `make up`이 role provisioning과 migration/bootstrap one-shot을 순서대로 완료한다.
4. 같은 revision의 backend/frontend를 모두 올린 뒤 Caddy를 연다.
5. health, 로그인, 등록 코드 사용·소진·폐기, 참가자 정지 뒤 기존 session 거부를 확인한다.

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

role 분리 이전 volume은 구 release가 실행 중일 때 구 `backup.sh`로 암호화 backup과 복구
가능성을 먼저 검증한 뒤 stack을 중지한다. 그 다음 새 source로 전환해
`./scripts/upgrade-database-roles.sh`를 실행하고 `make validate`, `make build`, `make up` 순서로
진행한다. 새 `backup.sh`는 canonical 여섯 secret을 요구하므로 helper보다 먼저 실행할 수
없다. helper는 legacy `alpha` owner와 `postgres_password` 값을 바꾸지 않고 owner 경로로
이관하며 서로 다른 migrator/runtime credential을 만든다. 중단 후 재실행은 가능하지만
새 role key와 legacy key가 섞인 모호한 상태는 거부한다.

## 공급망 보안과 검증

PostgreSQL·Redis와 frontend Node/nginx, custom Caddy의 Go builder/static runtime은
Chainguard manifest digest로 고정된다. backend Python base도 official image digest로
고정된다. Caddy는 `deploy/caddy/Dockerfile`이 full source commit을 직접 빌드하는 custom
binary이며 official prebuilt Caddy image를 사용하지 않는다.

```sh
set -eu
make check-locks
SECURITY_REQUIRE_DOCKER=1 make security
make config
make ci
```

security gate는 모든 hash-locked Python 환경, npm lock, Trivy filesystem과
backend/frontend/Caddy builder·runtime, PostgreSQL·Redis 실제 운영 image를 검사한다.
npm은 `info`부터, Trivy는 `UNKNOWN`, `LOW`, `MEDIUM`, `HIGH`, `CRITICAL` 전 severity를
포함하며 허용 근거 없는 finding에 실패한다. 각 image의 CycloneDX SBOM을
`security-reports/`에 만들고 Caddy VEX는 컴파일 dependency graph 검사로 도달 불가능함을
확인한 한 항목에만 적용한다.

Makefile과 backup은 `scripts/compose.sh`만 사용한다. 이 wrapper는 `.env`를 검증하고
project directory, env file과 `compose.yaml`을 고정하며 graph·profile·interpolation
override를 거부한다. gate는 backend lock 선언 동기화부터 확인하고 render된
graph가 정확히 7개 service, 3개 local build context, 전 service `linux/amd64`와 검사한
PostgreSQL·Redis digest인지 확인한다. `db-roles`는 PostgreSQL, `migrate`는 backend image를
별도 build 없이 재사용한다. `COMPOSE_FILE`,
`COMPOSE_ENV_FILES`와 command-line `COMPOSE`를 포함한 우회는 지원하지 않는다. graph 변경은
Compose·validator·gate를 함께 고치는 reviewed code change로만 수행한다.

운영자가 wrapper를 직접 호출하는 범위는 `config`, `stop`, `exec`, `run` 같은 보조 작업뿐이다.
image build와 service start는 wrapper의 `build`·`up`을 직접 호출하지 않는다. build는 전체
scan을 수행하는 `make build`, start는 유효한 manifest binding을 검증하고 필요할 때 전체
gate를 다시 실행하는 `make up` 또는 `make wait`를 사용한다.

`make build`는 canonical application image를 한 번만 build하고 PostgreSQL·Redis image를
pull한 뒤, builder와 5개 고유 service image artifact의 exact ID를 검사해 JSON report·
CycloneDX SBOM을 만든다. 통과하면 source·`.env`, 7-service rendered graph, canonical
reference와 5개 고유 exact image ID를 묶은 mode 0600
`security-reports/deployment-manifest.json`을 원자적으로 게시한다.

새 전체 gate는 입력 snapshot 직후 기존 deployment manifest를 unlink하고 parent directory를
`fsync`해 이전 승인을 먼저 내구성 있게 폐기한다. 모든 build·scan·SBOM 검사가 성공한 경우에만
새 manifest를 게시하므로, 새 advisory로 재검사가 실패해도 이전 2시간 manifest로 돌아갈 수
없다.

scanner뿐 아니라 `make up`도 같은 `security-reports/` directory-inode lock을 잡는다.
`make up`은 lock 안에서 수행하는 최종 preverify부터 `up`과 postverify가 모두 끝날 때까지
잠금을 유지하므로 concurrent scan의 manifest 폐기·canonical tag 재지정을 거부한다.

`make up`과 이를 호출하는 `make wait`는 manifest가 2시간 이내인지와 현재
source·`.env`·rendered graph·reference/tag가 기록된 exact ID에 모두 일치하는지 검사한다.
manifest가 없거나 stale·tampered·drift 상태면 전체 build/scan/SBOM gate를 다시 실행하며,
Docker·scanner를 쓸 수 없거나 finding이 있으면 start하지 않는다. start 직전에 같은 ID
binding을 다시 확인하고 `up --no-build --remove-orphans`로 재생성한다. poststart는 Compose
project의 orphan까지 포함한 전체 container를 열거하고 service label이 canonical 7개와
정확히 일대일인지 확인해 unknown orphan, duplicate 또는 누락을 거부한다. 이어 상시 5개가
running인지, `db-roles`·`migrate`가 exited(0)인지, 두 one-shot이 각각 PostgreSQL·backend의
검사된 alias image ID를 사용했는지도 확인한다. 어느 검증이든 실패하면 배포를 성공으로
간주하지 않는다.

CI는 `SECURITY_REQUIRE_DOCKER=1`이므로 Docker나 scanner를 사용할 수 없어도 성공으로
간주하지 않는다. 스캔 결과는 그 실행 시점의 데이터베이스와 build에 대한 근거일 뿐
미래 zero-day까지 절대 0개라고 보장하지 않는다. 매일 03:17 UTC gate는 정기 탐지 지연을
주 단위에서 하루 수준으로 낮추는 운영 보완이며 취약점 부재 보장이 아니다. 최신 scanner DB,
배포 직전 재스캔과 fail-closed 정책을 운영자가 계속 유지해야 한다. 자세한 정책은
[SECURITY.md](SECURITY.md)를 따른다.

## 자주 쓰는 명령

```text
make help       # 전체 target
make secret-acl # 수동 교체·복구한 secret의 UID 65532 ACL 재적용·검증
make validate   # .env/.secrets 검증
make config     # 최종 Compose render 검증
make build      # canonical 1회 build, exact ID scan·SBOM, private manifest 게시
make up         # 유효한 2시간 manifest 재사용 또는 재검사, --no-build start·ID 검증
make wait       # make up과 같은 manifest 검증·안전한 대기/재개
make smoke      # 비파괴 HTTP 확인
make logs       # 최근 로그 follow
make stop       # 컨테이너만 정지
make down       # 컨테이너/network 제거, named volume 보존
```

의도적으로 volume을 삭제하는 Make target은 없다. 삭제가 필요하면 암호화 백업, project
이름과 정확한 volume을 확인한 뒤 운영자가 별도 절차로 수행한다.
