# Challenge-as-code 템플릿

이 디렉터리는 CTFnight 문제 정의를 코드 리뷰 가능한 YAML로 관리하기 위한 예제다.
백엔드는 다음 명령으로 정의를 검증하고 `event_slug + slug` 기준으로 생성 또는 갱신한다.

```sh
make import-challenge CHALLENGE=challenges/welcome/challenge.yaml
```

`CHALLENGE`은 host의 `templates/` 기준 상대 경로다. Make target은 파일을 backend
서비스에 mount하지 않고, 일회성 non-web 컨테이너의 표준 입력으로만 전달한다.
현재 단일 이벤트 MVP에서는 최초 bootstrap이 만든 `ctfnight`를 대상으로 사용한다.
이벤트 생성 API는 아직 없으므로 다른 `event_slug`를 쓰려면 후속 다중 이벤트 기능이
필요하다.

## 지원 필드

| 필드 | 필수 | 설명 |
|---|---:|---|
| `event_slug` | 예 | 이미 존재하는 이벤트 slug |
| `slug` | 예 | 이벤트 안에서 고유한 문제 식별자 |
| `title`, `category` | 예 | 화면 제목과 분류 |
| `description_md` | 예 | 참가자에게 표시할 Markdown |
| `connection_info` | 아니요 | 접속 주소 또는 명령 |
| `scoring.type` | 예 | `fixed` 또는 `dynamic` |
| `scoring.initial` | 예 | 최초 또는 고정 점수 |
| `scoring.minimum`, `scoring.decay` | 동적 점수 | 최저 점수와 감쇠 기준 |
| `max_attempts` | 아니요 | 0~1,000; `0`은 문제별 추가 제한 없음 |
| `visible`, `visible_at` | 아니요 | 공개 여부와 예약 공개 시각 |
| `flag.type` | 예 | `exact` 또는 `regex` |
| `flag.value` | 예 | exact 원문 또는 정규식 패턴 |
| `prerequisites` | 아니요 | 먼저 해결해야 할 문제 slug 목록 |

정확한 구조는 [challenge.schema.json](challenge.schema.json)에도 정의되어 있다.
기본 예시는 `FLAG{...}`이지만 CTFnight 코어는 특정 접두사나 중괄호 형식을
강제하지 않는다. 문제 제작자는 `exact`에 임의의 전체 문자열을 지정하거나
`regex`로 대회·문제별 포맷을 정의할 수 있다.

`max_attempts=0`도 무한한 Submission 저장을 허용하지 않는다. 시스템은 한 팀이 한 문제에
최대 1,000번, 한 이벤트 전체에 최대 10,000번만 제출하게 하는 설정 불가능 hard cap을
적용한다. 1~1,000을 지정하면 그 더 낮은 문제별 상한을 사용한다. 따라서 정상적인 대회
운영에 필요한 최소값을 명시하고, 무제한이라는 표현으로 시스템 상한 해제를 기대하지 않는다.

## Flag secret 취급

버전 관리되는 예제의 flag는 데모 값일 뿐이다. 실제 대회 flag 원문을 Git에
커밋하지 않는다. 현재 importer는 가져오는 순간 exact flag를 HMAC으로 변환하지만,
가져오기 전 YAML에는 원문이 존재한다.

운영 정의는 다음처럼 Git에서 제외된 임시 디렉터리에서 다룬다.

```sh
set -eu
install -d -m 700 templates/.private
install -m 600 templates/challenges/welcome/challenge.yaml \
  templates/.private/welcome.yaml
# 편집기로 flag와 event_slug를 변경
make import-challenge CHALLENGE=.private/welcome.yaml
rm -f templates/.private/welcome.yaml
```

`.private/`는 `.gitignore`에 포함되어 있다. 실행 중인 web backend에는 `templates/`가
mount되지 않으며, mode-0600 파일은 host에서 실행한 Make만 읽는다. 일회성 importer는
YAML을 표준 입력으로 받은 뒤 종료된다. 더 엄격한 환경에서는 배포 시점에 비밀 저장소가
임시 파일을 만들게 하고, import 직후 안전하게 제거한다.

## 설계 경계

이 정의는 점수판의 제어 데이터다. 취약한 문제 컨테이너를 CTFnight 웹 앱이나 Docker
socket으로 직접 실행하지 않는다. 컨테이너형 문제는 후속 `ChallengeRuntime`
provider가 별도 호스트 또는 Kubernetes에 배포하고, 여기에는 접속 정보만 기록한다.
