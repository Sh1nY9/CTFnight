# 오픈소스 CTF 엔진 조사

> 조사 기준일: 2026-08-24 (KST)  
> 대상 프로젝트: Alpha (제품명 CTFnight) — 나만의 독자적인 CTF 플랫폼 개발 및 배포  
> 조사 원칙: 각 프로젝트의 공식 문서, 공식 GitHub 저장소, 릴리스 및 라이선스를 우선 확인했다.  
> 파일명은 요청한 철자인 **reserch.md**를 그대로 사용했다.

## 0. 제품 이름 조사와 명명 결정

2026-08-24에 정확히 붙여 쓴 **CTFnight**를 CTF 엔진·플랫폼·공개
소프트웨어 제품명으로 사용한 선행 사례가 있는지 확인했다. GitHub의 정확한
`ctfnight` 사용자·저장소와 PyPI, npm, crates.io의 동일 패키지는 확인되지
않았고, 동일한 공개 CTF 엔진도 찾지 못했다.

다만 띄어쓴 **CTF Night**는 이미 CTF 생태계의 행사명으로 쓰인다.

- Uttara University Cyber Security Club은 `CTF Night 0x1`, `0x2`를 월간 CTF
  대회로 운영했다: [공식 결과](https://cybersecurity.club.uttara.ac.bd/NewsPortal/ctf-night-0x1-results.html),
  [공식 행사 목록](https://cybersecurity.club.uttara.ac.bd/events.html)
- InfoPhreak는 정기 커뮤니티 세션을 `CTF Night`라고 부른다:
  [공식 커뮤니티 페이지](https://infophreak.com/community/)
- Champlain College에서도 `Cybersecurity Club CTF Night`라는 행사명을
  사용했다: [공식 행사 페이지](https://www.champlain.edu/event/cybersecurity-club-ctf-night/)

결론적으로 **정확히 같은 엔진·제품 충돌은 확인되지 않아 제품명을
`CTFnight`로 확정**했다. 그러나 이벤트명과의 검색 혼동 가능성은 남아 있다.
`Alpha`는 상위 프로젝트 코드명과 기존 기술 식별자에만 유지한다. 이 조사는
공개 기술·명칭 가용성 조사이며 정식 상표 법률 검토를 대체하지 않는다.

## 1. 조사 목적과 범위

CTFnight의 목표는 단순히 CTFd 화면을 다시 만드는 것이 아니라, 계정·팀·문제·플래그 제출·점수·관리·배포를 독자적으로 통제할 수 있는 CTF 플랫폼을 만들고 실제로 배포하는 것이다.

이번 조사에서는 다음 질문에 답하고자 했다.

1. 현재 공개되어 있는 CTF 엔진은 어떤 경기 형식과 운영 방식을 지원하는가?
2. CTFd, rCTF, GZCTF 등 현대적인 엔진은 어떤 구조와 확장 방식을 채택했는가?
3. 점수판과 취약한 문제 실행 환경은 어디에서 분리해야 하는가?
4. 오래되거나 보관된 프로젝트에서 여전히 배울 수 있는 설계는 무엇인가?
5. 라이선스와 유지보수 상태를 고려할 때 CTFnight가 직접 재사용해도 되는 범위는 어디까지인가?
6. CTFnight의 첫 버전과 장기 구조는 어떻게 잡는 것이 합리적인가?

여기서 “CTF 엔진”이라는 표현은 넓게 사용한다. 다만 실제 설계에서는 아래 세 부류를 반드시 구분해야 한다.

- **대회 제어 엔진**: 사용자·팀, 문제, 제출, 채점, 점수판, 관리자, API를 담당한다.
- **문제 실행 인프라**: 취약한 컨테이너·가상 머신을 빌드하고 배포하며 수명주기를 관리한다.
- **Attack–Defense 게임 서버**: tick, checker, 서비스 상태, flag 수명, 공격 제출, SLA를 관리한다.

한 프로젝트가 이 책임을 모두 가질 수도 있지만, 같은 보안 경계에 두어야 한다는 뜻은 아니다.

---

## 2. 가장 중요한 결론

### 2.1 현재 핵심 비교 대상

- **CTFd**는 활발히 유지되는 범용 Jeopardy형 제어 엔진이자 중요한 비교 기준이다. 플러그인, 테마, REST API, 내보내기·가져오기 생태계가 강하다.
- **OtterSec rCTF v2**는 provider 중심의 치환 가능한 구조, 명확한 PostgreSQL·Redis 확장 모델, 팀별 인스턴서 통합이 돋보인다.
- **GZCTF**는 팀별 컨테이너와 동적 플래그, 실시간 운영, 트래픽 기능까지 통합한 강력한 비교 대상이다. 그러나 저장소 전체를 순수 AGPL 프로젝트로 간주하면 안 되며 제한 컴포넌트를 따로 확인해야 한다.
- **RootTheBox**는 스토리, 업그레이드, 게임 내 화폐 등 게임화와 다양한 플래그 형식을 연구하기 좋다.
- **Flagpost**는 실시간 협업, 자동화, 세분화된 권한을 보여 주는 신생 설계다.
- **echoCTF.RED**는 일회성 문제 목록보다 장기 운영형 cyber range와 실제 타깃 네트워크 관리에 가깝다.
- **FAUST CTF Gameserver**, **EnoEngine**, **ctf01d**는 Jeopardy가 아닌 Attack–Defense를 이해하는 핵심 자료다.

### 2.2 보관·저활동 프로젝트의 취급

- **redpwn/rCTF v1**, **picoCTF 2019 플랫폼**, **FBCTF**, **Cardinal**은 보관 상태이므로 신규 CTFnight의 기반으로 삼지 않는다.
- **Mellivora**, **HackTheArch**, **ForcAD**는 구조가 비교적 읽기 쉽지만 유지보수 신호가 약하다.
- 보관된 프로젝트에서도 per-user 인스턴스, 지도형 King of the Hill, 동적 힌트, 전통적 PHP 배포 같은 개념은 참고할 수 있다.

### 2.3 CTFnight가 가져가야 할 핵심 원칙

1. **제어면과 문제 실행 데이터면을 분리한다.**
2. **Jeopardy와 Attack–Defense를 하나의 얕은 점수 모델로 합치지 않는다.**
3. **점수 계산, 플래그 검증, 저장소, 알림, 인스턴서 등을 versioned provider 계약으로 분리한다.**
4. **제출 처리는 원자적 트랜잭션과 멱등성을 보장한다.**
5. **Docker Compose 기반의 쉬운 시작과 Kubernetes 기반의 운영 구성을 별도 배포 프로필로 제공한다.**
6. **플러그인은 완전 신뢰 코드로 취급하거나, 가능하면 별도 프로세스와 제한된 권한으로 실행한다.**
7. **독자성이 목표라면 기존 코드의 대규모 fork보다 공개된 개념을 바탕으로 새 도메인 모델을 설계하는 편이 장기적으로 유리하다.**

---

## 3. CTF 경기 형식부터 구분하기

### 3.1 Jeopardy

참가자가 카테고리별 문제를 풀고 플래그를 제출해 점수를 얻는다. Web, Pwn, Reversing, Crypto, Forensics 같은 일반적인 온라인 CTF가 이 형식이다.

필요한 코어 기능은 다음과 같다.

- 사용자 또는 팀 등록
- 문제 공개 조건과 선행 문제
- 정적·정규식·동적 플래그 검증
- 고정 또는 solve 수 기반 동적 점수
- 힌트, 첨부 파일, 원격 접속 정보
- 제출 제한과 rate limit
- 점수판, 동점 처리, freeze
- 공지, 관리자, 감사 로그

CTFd, rCTF, GZCTF, RootTheBox, Mellivora, Flagpost가 주로 이 영역에 속한다.

### 3.2 Attack–Defense 또는 AWD

각 팀은 동일한 취약 서비스를 운영한다. 라운드 또는 tick마다 checker가 서비스 가용성과 flag 보존을 검사하고, 다른 팀의 flag를 탈취해 제출한다.

Jeopardy와 달리 다음 개념이 코어가 된다.

- tick과 라운드 스케줄
- checker의 PUTFLAG, GETFLAG, PUTNOISE, GETNOISE, HAVOC
- 서비스 상태와 SLA
- flag 발급·수명·회수
- 공격 flag 제출 서버
- 중복, 자가 제출, 오래된 flag, 잘못된 발신자 검증
- 공격·방어·가용성 점수의 분리
- 팀 vulnbox와 VPN 상태

FAUST CTF Gameserver, EnoEngine, ctf01d, ForcAD, Cardinal이 이 영역이다.

### 3.3 King of the Hill

어떤 자원이나 서비스를 일정 시간 소유하거나 통제할 때 점수를 얻는다. 외부 상태가 시간에 따라 점수를 계속 갱신해야 하므로 일반 solve 레코드만으로 표현하기 어렵다.

FBCTF는 지도 기반 King of the Hill을 지원했고, rCTF v2의 외부 dynamic score feed는 이런 점수원을 연결할 수 있다.

### 3.4 교육형 cyber range와 장기 wargame

단발성 대회보다 개인별 실습 환경, 진행도, 지속적인 타깃 수명주기, 강사 운영이 중요하다.

- picoCTF 구형 플랫폼: 교육형 문제와 개인별 인스턴스
- echoCTF.RED: 장기 운영 타깃 네트워크
- Haaukins: VM·컨테이너 기반 교육 환경
- RootTheBox: 스토리와 게임화

---

## 4. 전체 비교표

상태는 조사일의 공식 저장소와 릴리스를 기준으로 한 스냅샷이다.

| 프로젝트 | 주 형식 | 주요 기술 | 문제 인스턴스 | 라이선스 | 2026-08-24 판단 |
|---|---|---|---|---|---|
| [CTFd](https://github.com/CTFd/CTFd) | Jeopardy | Python, Flask, SQLAlchemy | OSS 코어 외부 | Apache-2.0 | 활발, 범용 기준점 |
| [rCTF v2](https://github.com/otter-sec/rctf) | Jeopardy, 외부 동적 점수 | Bun, Hono, SvelteKit, PostgreSQL, Redis | Docker·Kubernetes provider | Apache-2.0 | 활발, provider 구조 우수 |
| [redpwn/rCTF v1](https://github.com/redpwn/rctf) | Jeopardy | Node.js, Fastify, Preact | rCDS 등 외부 | BSD-3-Clause | 2025-02-04 보관 |
| [GZCTF](https://github.com/GZTimeWalker/GZCTF) | Jeopardy | ASP.NET Core, React, PostgreSQL, Redis | Docker·Kubernetes 통합 | AGPL 코어 + 제한 컴포넌트 | 활발, 라이선스 주의 |
| [RootTheBox](https://github.com/moloch--/RootTheBox) | Jeopardy, 게임화 | Python, Tornado, SQLAlchemy | 주로 외부 | Apache-2.0 | 유지 중, 게임화 기준 |
| [Flagpost](https://github.com/tbcsec/flagpost) | Jeopardy | FastAPI, Next.js, PostgreSQL, Redis, MinIO | 범위 밖 | AGPL-3.0 | 활발한 신생 프로젝트 |
| [Mellivora](https://github.com/Nakiami/mellivora) | Jeopardy | PHP, MySQL·MariaDB | 외부 | GPL-3.0 | 저활동 |
| [picoCTF 2019](https://github.com/picoCTF/picoCTF) | 교육형 Jeopardy | Flask, nginx, Ansible | 개인별 인스턴스 | MIT | 폐기·보관 |
| [FBCTF](https://github.com/facebookarchive/fbctf) | Jeopardy, KotH | Hack, HHVM, MySQL | 제한적 | CC BY-NC 4.0 | 2020 보관, 비상업 |
| [H1ve](https://github.com/D0g3-Lab/H1ve) | Jeopardy, AWD | CTFd 기반, Docker | 통합 플러그인 | 표시와 추가 제한 충돌 | 저활동, 재사용 비권장 |
| [echoCTF.RED](https://github.com/echoCTF/echoCTF.RED) | cyber range | PHP, Yii2, MariaDB, Docker, OpenVPN | 타깃 수명주기 통합 | BSD-2-Clause | 활발 |
| [FAUST Gameserver](https://github.com/fausecteam/ctf-gameserver) | Attack–Defense | Django, Python·Go checker | vulnbox는 별도 | ISC | 활발, A/D 핵심 |
| [EnoEngine](https://github.com/enowars/EnoEngine) | Attack–Defense | C#, PostgreSQL | launcher 등 분리 | MIT | ENOWARS 생태계에서 유지 |
| [ctf01d](https://github.com/sea5kg/ctf01d) | Attack–Defense | C++, SQLite | 외부 | MIT | 활발, 작은 A/D 구현 |
| [ForcAD](https://github.com/pomo-mondreganto/ForcAD) | Attack–Defense | Python, Celery, PostgreSQL, Redis | Docker Compose | GPL-3.0 | 저활동 |
| [Cardinal](https://github.com/05sec/Cardinal) | AWD | Go | 팀 머신 연동 | AGPL-3.0 | 2024 보관 |

엔진과 혼동하기 쉬운 인접 도구는 별도 표로 분리한다.

| 프로젝트 | 실제 역할 | 라이선스 | CTFnight에서의 의미 |
|---|---|---|---|
| [Google kCTF](https://github.com/google/kctf) | Kubernetes 문제 인프라 | Apache-2.0 | 격리와 운영 위협 모델 |
| [picoCTF cmgr](https://github.com/picoCTF/cmgr) | 문제 빌드·검증·배포 관리자 | Apache-2.0 | challenge-as-code와 자동 solver |
| [rCDS](https://github.com/redpwn/rcds) | Git 중심 문제 배포·점수판 동기화 | BSD-3-Clause | revision 기반 배포 개념 |
| [ctfcli](https://docs.ctfd.io/docs/management/ctfcli/overview/) | CTFd 문제 동기화 CLI | Apache-2.0 | 선언형 문제 형식과 CI |
| [Haaukins](https://github.com/aau-network-security/haaukins) | 교육용 VM·컨테이너 가상화 | Apache-2.0 | cyber range 계층 |
| [Beast](https://github.com/sdslabs/beast) | Docker 문제 배포·healthcheck | MIT | 문제 수명주기 분리 사례 |

---

## 5. CTFd 심층 분석

공식 자료:

- [CTFd 공식 개요](https://docs.ctfd.io/docs/overview/)
- [CTFd GitHub](https://github.com/CTFd/CTFd)
- [CTFd 3.8.7 릴리스](https://github.com/CTFd/CTFd/releases/tag/3.8.7)
- [설치 문서](https://docs.ctfd.io/docs/deployment/installation/)
- [플러그인 문서](https://docs.ctfd.io/docs/plugins/overview/)
- [API 시작 문서](https://docs.ctfd.io/docs/api/getting-started/)

### 5.1 상태와 성격

조사 시점 최신 릴리스는 **3.8.7**, 공개일은 2026-08-19이다. 같은 릴리스에는 비공개 사용자 정의 필드가 API에 노출되던 문제와 최대 시도 횟수의 경쟁 상태 수정도 포함됐다. 이 사실을 CTFnight 설계 관점에서 해석하면 개인정보 필드의 접근 제어뿐 아니라 시도 횟수 같은 제출 정책의 동시성 제어도 보안에 영향을 주는 경계로 취급해야 한다.

CTFd는 범용 Jeopardy CTF의 대회 제어면이다. 오픈소스 코어가 기본적으로 제공하는 책임은 다음과 같다.

- 사용자 또는 팀 모드
- 문제, 카테고리, 힌트, 파일
- 제출, solve, award
- 점수판과 freeze
- 관리자 UI
- 플러그인과 테마
- REST API
- 전체 대회 내보내기·가져오기

반대로 오픈소스 코어는 취약한 문제 컨테이너를 안전하게 배포하고 격리하는 범용 orchestrator가 아니다. 공식 [문제 서비스 자동 배포](https://docs.ctfd.io/tutorials/challenges/deploying-challenges/)와 [Application Target](https://docs.ctfd.io/docs/custom-challenges/application-target/)은 Hosted 또는 Self-Hosted Enterprise 전용이고, Application Target은 beta다. 운영자가 외부 서비스를 수동 연결하거나 커뮤니티 플러그인을 쓰는 경우와 구분해야 하며, CTFnight가 오픈소스 코어만 비교할 때 이를 기본 기능으로 계산해서는 안 된다.

### 5.2 기술 구조와 배포

- Python과 Flask 기반 WSGI 애플리케이션
- SQLAlchemy 데이터 계층
- gunicorn과 gevent 기반 실행
- MySQL 권장, SQLite 기본 개발 경로
- Redis 권장 캐시, 파일 시스템 캐시는 성능상 불리
- Docker Compose 제공
- reverse proxy와 TLS는 운영자가 구성

[공식 설치 문서](https://docs.ctfd.io/docs/deployment/installation/)는 일반적인 대회에 4코어·2GB RAM 정도를 제시하며, 최소 시작선은 2코어·1GB RAM이다. 기본 Compose는 TLS를 제공하지 않으므로 인터넷 공개 상태로 그대로 운영하면 안 된다.

PostgreSQL은 기술적으로 사용 가능하지만 공식 문서에서는 드물게 쓰이며 향후 지원 중단 가능성까지 언급한다. CTFnight가 PostgreSQL을 표준으로 선택하더라도 CTFd와 DB 호환성을 목표로 할 이유는 없다.

### 5.3 사용자와 팀

[User Mode 문서](https://docs.ctfd.io/docs/accounts/user-mode/)에 따라 대회는 개인 또는 팀 단위로 운영할 수 있다. CTFnight에서는 이를 단순 boolean으로 저장하기보다 다음을 분리하는 편이 좋다.

- 사용자 계정
- 팀
- 팀 멤버십과 역할
- 이벤트별 참가 단위
- Division 또는 bracket

이렇게 해야 한 사용자가 여러 이벤트에 참가하거나, 이벤트마다 팀이 달라지는 장기 플랫폼을 자연스럽게 표현할 수 있다.

### 5.4 문제, 플래그와 점수

[Scoring 문서](https://docs.ctfd.io/docs/scoring/overview/)에 따르면 CTFd 점수는 주로 solve와 award의 합으로 구성된다. 점수판을 freeze할 수 있고, 동점은 더 이른 Solve ID를 가진 쪽이 앞선다.

기본 기능:

- 고정 점수
- solve 수에 따라 내려가는 [동적 점수](https://docs.ctfd.io/docs/custom-challenges/dynamic-value/)
- OSS 기본의 [정적·정규식 플래그](https://docs.ctfd.io/docs/flags/overview/)
- 힌트 비용
- 최대 시도 횟수
- 점수판 freeze

CTFnight에서는 “현재 총점”만 저장하지 말고 원장 형태의 불변 점수 사건을 남기는 것이 안전하다.

- SolveCreated
- SolveRevoked
- AwardGranted
- AwardRevoked
- ChallengeRepriced
- PenaltyApplied

현재 점수판은 이 원장에서 계산한 snapshot 또는 projection으로 만들 수 있다. 그러면 관리자가 solve를 취소하거나 동적 점수 공식을 바꿨을 때 감사와 재계산이 쉬워진다.

### 5.5 플러그인, 테마와 API

[플러그인 문서](https://docs.ctfd.io/docs/plugins/overview/)의 CTFd 플러그인은 Python 모듈이며 load(app) 진입점을 통해 다음을 할 수 있다.

- 새 route와 API 추가
- DB table 추가
- 문제 형식과 플래그 형식 교체
- 템플릿·정적 자산 추가
- 기존 동작 변경

이 유연성은 강력하지만 플러그인이 애플리케이션 전체 권한을 갖는다는 뜻이기도 하다. 악성 또는 취약한 플러그인은 데이터베이스, 비밀값, 세션에 접근할 수 있다.

CTFnight는 다음 두 층으로 확장 모델을 나누는 편이 좋다.

1. **신뢰 플러그인**: 코어와 같은 프로세스에서 실행하되 서명, 허용 목록, 정확한 버전 고정을 요구한다.
2. **외부 provider**: HTTP 또는 메시지 계약으로 실행하고 기능별 자격 증명과 네트워크 권한만 부여한다.

CTFd의 [REST API](https://docs.ctfd.io/docs/api/getting-started/)는 대부분의 UI 동작을 지원하고 API token 인증을 제공한다. 다만 공식 문서도 endpoint 형식이 바뀔 수 있음을 언급한다. CTFnight는 처음부터 API 버전을 URL 또는 미디어 타입으로 명시하고 deprecation 기간을 정의해야 한다.

[ctfcli](https://docs.ctfd.io/docs/management/ctfcli/overview/)는 Git 이벤트 저장소의 challenge.yml과 문제 파일을 CTFd API로 install·sync하고 반대 방향 mirror도 지원한다. 공식적으로 alpha 상태라는 점은 고려해야 한다. 이를 바탕으로 “Git의 challenge revision을 배포와 점수판 metadata의 원천으로 삼는 모델”까지 발전시키는 것은 ctfcli가 보장하는 사실이 아니라 CTFnight에 대한 설계 제안이다.

### 5.6 백업과 이식성

CTFd는 [전체 내보내기](https://docs.ctfd.io/docs/exports/ctfd-exports/)와 [가져오기](https://docs.ctfd.io/docs/imports/ctfd-imports/)를 제공한다. CTFnight도 MVP부터 다음을 보장해야 한다.

- DB schema 버전이 기록된 export manifest
- 문제 metadata와 첨부 파일
- 사용자·팀·멤버십
- 제출·solve·award
- 공지와 페이지
- 설정 중 비밀이 아닌 부분
- 암호화된 비밀의 별도 backup 절차
- restore dry-run과 migration 검증

### 5.7 CTFd에서 배울 점과 CTFnight의 별도 설계 선택

배울 점:

- 넓은 플러그인 생태계
- 테마와 기능 확장의 분리
- 사용자·팀 모드
- 단순하고 이해하기 쉬운 관리자 경험
- API와 export/import
- 고정·동적 점수의 실용적인 기본값

CTFnight 독립 구현에서 다르게 가져갈 설계 선택:

- 제어면과 문제 실행 환경을 제품 수준에서 더 명확히 분리
- 플러그인 권한과 호환성 계약을 더 엄격히 관리
- 제출 멱등성·동시성 불변식을 명시
- 이벤트별 멤버십과 권한 모델 강화
- PostgreSQL, Redis, object storage를 운영 표준으로 일관되게 정의
- challenge-as-code를 CTFnight 부가 도구가 아닌 일급 워크플로로 제공

---

## 6. rCTF 계보와 v2 심층 분석

공식 자료:

- [현행 OtterSec rCTF](https://github.com/otter-sec/rctf)
- [현행 공식 문서](https://rctf.osec.io/)
- [v2.1.3 릴리스](https://github.com/otter-sec/rctf/releases/tag/v2.1.3)
- [legacy redpwn/rCTF](https://github.com/redpwn/rctf)
- [v1에서 v2 업그레이드](https://rctf.osec.io/installation/upgrading/)

### 6.1 v1과 v2를 혼동하면 안 되는 이유

redpwn/rCTF v1 저장소는 2025-02-04 보관됐고 README도 더 이상 유지보수하지 않으며 OtterSec rCTF로 이동하라고 명시한다. v1은 BSD-3-Clause, 현행 v2는 Apache-2.0이다.

조사 시점 현행 릴리스는 **v2.1.3**, 공개일은 2026-08-15이다. v2는 단절된 새 이름만이 아니라 기존 DB와 v1 API의 마이그레이션 경로를 제공한다.

[공식 업그레이드 문서](https://rctf.osec.io/installation/upgrading/)의 핵심은 다음과 같다.

- v2 이미지가 Drizzle migration으로 v1 DB를 변환
- 기존 v1 API route 유지
- 새 기능은 /api/v2 경로에 추가
- 일부 v1 설정을 provider 설정으로 변환
- migration은 forward-only이므로 PostgreSQL 백업 필수

신규 CTFnight가 참고할 기준은 v2이며, v1은 호환성과 역사적 구조 연구에 한정한다.

### 6.2 v2 구조

[수동 설치와 프로젝트 구조](https://rctf.osec.io/installation/manual/):

- Bun 기반 TypeScript 모노레포
- Hono REST API
- SvelteKit 정적 웹 프런트엔드
- Drizzle ORM과 PostgreSQL 15 이상
- Redis 7 이상
- Zod 기반 설정 검증
- 별도 CLI
- Puppeteer 기반 admin bot
- Go Kubernetes operator
- Python·FastAPI Docker instancer

[아키텍처 문서](https://rctf.osec.io/installation/architecture/)상 프로덕션 rCTF 애플리케이션 컨테이너에는 supervisord, Bun API, leaderboard worker, nginx가 함께 들어간다. PostgreSQL과 Redis는 이 애플리케이션 컨테이너 밖의 상태 서비스다.

- PostgreSQL: 핵심 영속 데이터
- Redis: 점수 snapshot, rate limit, provider lock, worker 알림
- nginx: 정적 웹, API·업로드 proxy, 압축과 보안 header

기본 Compose는 rCTF, PostgreSQL, Redis를 한 서버에서 함께 실행하며 local upload는 host bind mount를 쓴다. 이때 rCTF 컨테이너의 root filesystem은 read-only이고 임시 경로는 tmpfs다. 따라서 “컨테이너 외부 상태”가 곧 off-host DB나 고가용성 구성을 뜻하지는 않는다.

### 6.3 Provider 모델

[Provider 목록](https://rctf.osec.io/providers/):

| 책임 | 구현 예 |
|---|---|
| Captcha | reCAPTCHA, hCaptcha, Cloudflare Turnstile |
| 이메일 | SMTP, SES, Postmark, Mailgun |
| 파일 | local, S3, GCS, Cloudflare R2 |
| 점수 | classic, sekai, steep, jammy, genni, legacy |
| Avatar moderation | OpenAI |
| Analytics | Google, Cloudflare |
| Instancer | Docker, Kubernetes |
| Admin bot | rCTF bot |
| First-blood 알림 | Discord, Telegram |
| Flag | static, regex, dynamic |

Captcha·이메일·moderation·analytics·instancer·admin bot·blood bot처럼 공식 표에서 None을 지원하는 선택형 provider는 None으로 두면 해당 기능이 비활성화된다. 반면 upload는 local, scoring은 classic이 기본이고 flag provider는 문제마다 고르며 항상 사용 가능하다. 이 구조는 CTFnight에 매우 유용하다. 단, provider마다 다음 계약이 추가되어야 한다.

- 계약 버전
- 필요한 capability
- 설정 JSON Schema
- 필요한 secret 목록
- health와 readiness
- timeout, retry, circuit breaker
- 감사 대상 operation
- 데이터 지역성과 개인정보 범위

### 6.4 점수 모델

[관리자 점수 문서](https://rctf.osec.io/admin/scoring/)의 두 “동적” 개념은 구분해야 한다.

1. **decay**: solve 수가 늘 때 해당 문제를 푼 모든 팀의 현재 점수를 다시 계산한다.
2. **dynamic**: 외부 시스템이 팀별 점수를 서명된 webhook으로 공급한다. King of the Hill이나 Attack–Defense 연동에 적합하다.

[점수 provider 문서](https://rctf.osec.io/providers/scores/)는 classic, sekai, steep, jammy, genni, legacy 알고리즘을 제공한다. 문제별 points.min 또는 points.max를 바꾸면 기존 solver가 즉시 재가격되고, 전역 scoring algorithm을 바꾸면 다음 leaderboard worker tick에서 모든 decay 문제가 재가격된다.

dynamic 문제에는 일반 flag를 제출할 수 없고, solve가 존재하면 decay와 dynamic 사이의 종류 변경도 거부된다. 외부 feed의 점수 0은 해당 팀 점수를 제거하고 음수도 허용된다. 존재하지 않는 team ID는 오류 없이 무시되므로 외부 점수 서비스와 rCTF 사이의 팀 ID 동기화 실패를 별도로 탐지해야 한다.

외부 dynamic score feed는 HMAC-SHA256 서명, timestamp, replay 방지를 사용한다. 그러나 대회 종료 뒤에도 외부 feed가 요청하면 갱신될 수 있으므로 운영자가 feed를 중단해야 한다. CTFnight에서는 이벤트 상태가 ended 또는 archived일 때 코어가 쓰기를 거부하는 편이 더 안전하다.

### 6.5 인증과 권한

[인증 문서](https://rctf.osec.io/api/auth/)에서 rCTF는 개인보다 팀을 인증 주체로 본다.

- 팀이 이름, 이메일, 점수, solve, 멤버를 소유
- Bearer token으로 팀을 대표
- 이메일 등록·검증·복구
- CTFtime OAuth
- Division과 이메일 ACL
- Auth, Team, Verify, CtftimeAuth token

Auth와 Team token은 기본적으로 만료되지 않는다. tokenKey를 바꾸면 AES-256-GCM으로 보호된 기존 token이 모두 무효화된다. 단순 운영에는 편하지만 탈취된 token의 장기 위험과 대규모 강제 로그아웃 문제가 있다.

[설정 문서](https://rctf.osec.io/configuration/)상 Division 이메일 ACL에는 이메일 provider가 필요하다. 또한 CTFtime 인증 사용자는 이메일 ACL을 우회해 Division을 선택할 수 있으므로 ACL을 보안 경계로 사용할 때는 공식 경고대로 CTFtime 인증을 비활성화해야 한다.

CTFnight에서는 다음을 권장한다.

- 짧은 access token과 회전 가능한 refresh token
- 개별 사용자 session과 팀 권한의 분리
- 기기·세션 목록과 즉시 폐기
- 관리자 MFA 또는 WebAuthn
- secret key versioning과 단계적 회전
- OAuth 계정 연결의 명시적 감사

### 6.6 관리와 확장

[관리자 문서](https://rctf.osec.io/admin/)에는 문제·팀·solve·제출·런타임 설정·admin bot 관리가 포함된다. 권한은 bit mask로 제한할 수 있고 CLI로 관리자를 승격하거나 강등할 수 있다.

[수평 확장 문서](https://rctf.osec.io/installation/scaling/)의 핵심:

- 여러 replica가 PostgreSQL과 Redis를 공유
- PostgreSQL session advisory lock으로 leaderboard worker 한 개 선출
- Redis로 점수 변경 통지
- healthz와 readyz 제공
- graceful shutdown과 요청 drain
- transaction pooling 방식 PgBouncer는 session lock과 호환되지 않음

CTFnight는 리더 선출을 DB session semantics에 묶을지, 독립 job queue와 lease에 둘지 초기에 결정해야 한다.

같은 [설정 문서](https://rctf.osec.io/configuration/)의 proxy.trust hop 수는 실제 reverse-proxy topology와 정확히 맞아야 한다. 너무 크게 잡으면 참가자가 X-Forwarded-For를 위조해 IP rate limit을 우회할 수 있고, 너무 작으면 모든 요청이 proxy IP에서 온 것으로 집계될 수 있다.

### 6.7 문제 인스턴스

legacy rCTF 자체는 문제를 배포하지 않았고 [rCDS](https://github.com/redpwn/rcds)를 권장했다. rCDS는 중앙 challenge Git 저장소를 single source of truth로 사용하고, CI에서 이미지·서비스 배포와 scoreboard metadata 동기화를 수행했다.

v2는 [Docker·Kubernetes instancer](https://rctf.osec.io/integrations/instancer/)를 선택적으로 통합한다.

- 팀별 격리된 복제본
- start, status, extend, stop
- HTTP, HTTPS, TCP-over-TLS endpoint
- timeout과 연장 가능 여부
- provider별 JSON Schema

[대회 배포 가이드](https://rctf.osec.io/meta/running-a-successful-ctf/deployment/)는 Konata와 CI로 source, attachment, image, instancer 설정, rCTF metadata를 같은 revision에 묶는 방식을 권장한다.

그래도 취약한 문제 workload를 플랫폼 DB와 사용자 데이터가 있는 호스트에 두어서는 안 된다. 특히 의도된 풀이가 코드 실행을 제공하는 shared remote는 컨테이너만 믿지 말고 nsjail 같은 연결별 sandbox를 반드시 적용해야 한다. 이는 모든 팀별 격리 인스턴스에 동일한 방식이 무조건 필요하다는 뜻이 아니라, 공유 프로세스·파일 시스템·다른 연결로의 탈출을 막는 위협 모델을 각 runtime 유형에 맞게 세워야 한다는 뜻이다.

### 6.8 rCTF가 의도적으로 하지 않는 것

[구현하지 않을 기능 문서](https://rctf.osec.io/meta/things-we-will-not-implement/)는 다음을 명시한다.

- first-blood 보너스 점수
- leaderboard freeze
- 제출 횟수 제한 또는 오답 페널티
- 문제·리더보드 열람에 로그인 강제

이는 결함 목록이라기보다 제품 철학이다. CTFnight는 각 기능을 “경쟁 제품이 하므로 추가”하지 말고 이벤트별 정책으로 지원할지 결정해야 한다.

### 6.9 rCTF에서 배울 점

- provider 기반 교체 가능성
- 명확한 DB·cache 역할
- leaderboard worker의 분리
- 단일 서버와 수평 확장 모드의 구분
- challenge revision과 배포 동기화
- signed external score feed
- 팀별 인스턴서의 일급 통합
- 운영 중 solve 삭제·정정과 팀 token 생성 도구

---

## 7. GZCTF 심층 분석

공식 자료:

- [GZCTF GitHub](https://github.com/GZTimeWalker/GZCTF)
- [공식 문서](https://gzctf.gzti.me/)
- [Quick Start](https://gzctf.gzti.me/guide/start/quick-start.html)
- [배포 선택지](https://gzctf.gzti.me/guide/deployment/options)
- [v1.8.7 릴리스](https://github.com/GZTimeWalker/GZCTF/releases/tag/v1.8.7)

### 7.1 상태와 기술

조사 시점 최신 릴리스는 **v1.8.7**, 공개일은 2026-07-05이며 기본 브랜치는 2026-08-23까지 갱신됐다.

- C#과 ASP.NET Core
- React와 Mantine UI
- PostgreSQL
- Redis 또는 Garnet cache
- SignalR 실시간 통신
- Docker와 Kubernetes
- 로컬·S3·MinIO 계열 object storage

### 7.2 주요 기능

- 정적·동적 attachment
- 정적·동적 container
- 팀별 고유 플래그와 문제 인스턴스
- 사용자 정의 지수형 동적 점수
- first-three-solve 보너스
- 실시간 제출·로그·공지
- 팀 그룹과 권한 수준
- writeup 수집과 심사
- SMTP와 Turnstile
- TCP-over-WebSocket proxy
- 트래픽 전달과 캡처
- 다국어와 한국어
- metrics와 tracing

통합 범위만 보면 GZCTF는 CTFd OSS 코어보다 “점수판 + 문제 인스턴스” 경험이 더 일체화되어 있다.

### 7.3 배포

[Quick Start](https://gzctf.gzti.me/guide/start/quick-start.html)는 Docker Compose와 Docker socket mount를 사용한다. 공식 문서도 이 구성을 로컬 테스트에 적합한 것으로 보고, 규모가 크거나 장기간 운영하는 경우 Kubernetes를 권장한다.

[배포 선택지](https://gzctf.gzti.me/guide/deployment/options):

- Kubernetes: 대규모·장기 운영 권장
- Docker 제어면 + 원격 Kubernetes 문제 실행: 학교·소규모 운영 선택지
- 단일 Docker 호스트: 테스트 중심, 보안·자원 격리 한계

CTFnight도 Docker socket을 웹 애플리케이션에 직접 노출하지 말고 별도 runtime broker를 두어야 한다. broker는 제한된 이미지 registry, namespace, quota, network policy만 사용할 수 있어야 한다.

### 7.4 라이선스의 중요한 주의점

GZCTF 저장소는 코어에 AGPL-3.0을 사용하지만 별도 제한 컴포넌트와 상표 정책이 존재한다.

- [제한 컴포넌트 목록](https://github.com/GZTimeWalker/GZCTF/blob/develop/PROPRIETARY_COMPONENTS.md)
- [추가 라이선스 조항](https://github.com/GZTimeWalker/GZCTF/blob/develop/LICENSE_ADDENDUM.txt)
- [상표 정책](https://github.com/GZTimeWalker/GZCTF/blob/develop/TRADEMARKS.md)

제한 목록에는 컨테이너 관리자, 일부 클라이언트 hook, 저작권 컴포넌트가 포함된다. [Restricted License 원문](https://github.com/GZTimeWalker/GZCTF/blob/develop/license/LicenseRef-GZCTF-Restricted.txt)은 제한 컴포넌트 원본의 사용·복제·상업적 사용·재배포를 조건부로 허용하지만, 무단 수정·삭제·파생물 작성·역공학 등을 금지하고 라이선스·고지·upstream 링크 보존을 요구한다. 따라서 다음과 같이 처리해야 한다.

- 기능과 운영 개념은 비교 연구
- 실제 코드 재사용 전 파일 단위 라이선스 확인
- 제한 컴포넌트는 원문 조건을 충족하는 원본 재배포와 무단 수정·파생 사용을 구분
- AGPL 공개 의무와 추가 조항을 별도 검토

이는 법률 자문이 아니며 실제 배포·배포물 판매 전에는 전문적인 라이선스 검토가 필요하다.

### 7.5 GZCTF에서 배울 점

- 문제 metadata와 컨테이너 수명주기의 통합 UX
- 팀별 동적 플래그
- 실시간 운영 로그
- writeup 수집을 대회 종료 흐름에 포함
- Docker와 Kubernetes provider를 모두 제공
- 트래픽 proxy·capture를 선택 기능으로 분리

제한 컴포넌트는 허용된 원본 사용·재배포 조건과 수정 금지 범위를 정확히 따라야 한다. 브랜딩도 전면 금지가 아니라 공식 GZCTF로 오인시키는 명칭·로고 사용 등 [상표 정책](https://github.com/GZTimeWalker/GZCTF/blob/develop/TRADEMARKS.md)의 제한을 따라야 한다.

---

## 8. 다른 Jeopardy·교육형 엔진

### 8.1 RootTheBox

[공식 기능 목록](https://github.com/moloch--/RootTheBox#features):

- 팀전·개인전
- WebSocket 실시간 점수판과 애니메이션
- static, regex, datetime, multiple choice, file 플래그
- 동적 점수, 힌트, 오답, 페널티, 레벨 보너스
- 팀 파일·텍스트 공유
- CyberChef와 Rocket.Chat 연동
- CTFtime JSON
- 점수판 freeze
- story mode, unlock, upgrade, 게임 내 화폐
- 문제와 플래그 export

기술은 Python, Tornado, SQLAlchemy·Alembic 중심이며 Apache-2.0이다. 최신 정식 릴리스 [3.14.0](https://github.com/moloch--/RootTheBox/releases/tag/3.14.0)은 2024-03-25이고 기본 브랜치는 2026-04-20까지 갱신됐다.

CTFnight의 코어에는 단순한 문제·점수 모델을 유지하고 게임 경제와 story는 선택 모듈로 두는 편이 좋다.

### 8.2 Flagpost

[공식 문서](https://docs.flagpost.io/start/introduction/)상 2026년에 등장한 self-hosted 플랫폼이다.

- WebSocket 실시간 점수판, presence, 알림, 지원 ticket
- When–If–Then 시각적 자동화
- Y.js CRDT 팀 공동 노트
- DB 기반 세분화 권한
- 정적·정규식·객관식 플래그
- 동적 점수, 선행 문제, 예약 공개
- bracket, division, freeze, CTFtime feed
- ctfcli YAML import/export
- OIDC, SAML, LDAP·AD

FastAPI, SQLAlchemy, PostgreSQL, Redis, MinIO, Next.js를 사용하고 AGPL-3.0이다. [v1.4.0](https://github.com/tbcsec/flagpost/releases/tag/v1.4.0)은 2026-08-13이다.

문제 컨테이너 provisioning은 의도적으로 범위 밖이다. 자동화와 협업 기능은 흥미롭지만 신생 프로젝트이므로 장기간 부하와 upgrade 안정성은 별도로 검증해야 한다.

### 8.3 Mellivora

[공식 기능 목록](https://github.com/Nakiami/mellivora#features):

- 임의 category와 challenge
- 복수 팀 유형
- 자동 또는 수동 자유 텍스트 채점
- 힌트와 선행 문제 unlock
- 시간 기반 공개
- 이메일 정규식 가입 제한
- 로컬·S3 파일
- 제출 throttling과 IP 상관 분석
- TOTP 2FA
- CTFtime JSON

PHP, MySQL·MariaDB, Composer 기반이고 GPL-3.0이다. 최신 릴리스 [v2.2.1](https://github.com/Nakiami/mellivora/releases/tag/v2.2.1)은 2022-01-07, 기본 브랜치 마지막 갱신은 2023-09-06이다. 최소형 전통 Jeopardy 엔진 연구에는 좋지만 CTFnight의 현대 운영 기반으로 선택하기에는 유지보수 신호가 약하다.

### 8.4 picoCTF 2019 플랫폼과 현재 cmgr

[picoCTF 2019 저장소](https://github.com/picoCTF/picoCTF)는 공식 README에서 폐기된 구형 플랫폼이며 새 대회에 사용하지 말라고 명시한다. 2024-05-13 보관됐다.

구조:

- nginx 정적 UI
- Flask REST API
- 별도 shell server
- challenge instance manager
- Ansible
- 로컬 Vagrant, 원격 Terraform

핵심 연구 가치는 하나의 논리 문제에서 사용자별 인스턴스와 고유 flag를 발급해 공유를 추적하는 구조다. MIT 라이선스지만 오래된 전체 플랫폼을 CTFnight 기반으로 사용해서는 안 된다.

현재 활발한 공개 구성요소인 [picoCTF cmgr](https://github.com/picoCTF/cmgr)는 완전한 점수판이 아니라 challenge manager다.

- Go CLI와 REST 서버
- Docker 문제 빌드·테스트·실행
- 자동 solver 검증
- 서비스·artifact·flag-only 문제
- SQLite metadata

최신 [v3.1.0](https://github.com/picoCTF/cmgr/releases/tag/v3.1.0)은 2026-08-20이고 Apache-2.0이다. CTFnight의 challenge CI와 사전 검증 도구를 설계할 때 유용하다.

### 8.5 FBCTF

[공식 README](https://github.com/facebookarchive/fbctf#what-is-fbctf)는 Jeopardy와 King of the Hill을 지원한다.

- 지도 기반 게임 UI
- base 점령과 레벨
- 팀 등록과 관리자 UI
- Hack, HHVM, MySQL, memcached, nginx
- Ubuntu 16.04 시대 배포 자료

2020-03-03 보관됐으며 기본 브랜치의 마지막 코어 갱신도 2018년이다. [라이선스](https://github.com/facebookarchive/fbctf/blob/master/LICENSE)는 CC BY-NC 4.0으로 비상업 제한이 있다. 현대적 기반이나 상업 가능 제품의 코드 원천으로 부적합하며, 지도형 인터랙션만 역사적으로 참고한다.

### 8.6 H1ve

[H1ve](https://github.com/D0g3-Lab/H1ve)는 CTFd를 바탕으로 Jeopardy와 AWD, 독립 컨테이너, 동적 플래그를 통합하려 했다.

- CTFd-Owl과 CTFd-Glowworm 플러그인
- Web·Pwn 컨테이너 관리
- Aliyun instance 연동
- Docker Compose

저장소의 LICENSE는 Apache-2.0처럼 보이지만 README에는 상업 교육·대회·제품 판매와 로고 변경을 제한하는 추가 문구가 있다. 정상적인 Apache-2.0 권리와 충돌하거나 범위가 불명확하므로 명시적인 권리 확인 없이 코드를 재사용하지 않는다. [기본 master 브랜치의 마지막 커밋](https://github.com/D0g3-Lab/H1ve/commits/master)은 2020-11-25이며 2022년에는 비주류 브랜치 push만 관측되므로 설계 참고에만 쓴다.

### 8.7 HackTheArch

[HackTheArch](https://github.com/mcpa-stlouis/hack-the-arch)는 Rails 기반 점수 서버다.

- 힌트를 열면 점수 차감
- 웹 관리자에서 문제와 힌트 편집
- 실시간 제출과 채팅
- 문제 서버와 점수 서버의 분리 권고

MIT 라이선스지만 최신 릴리스가 2019년, 기본 브랜치 활동도 2022년 수준이다. 동적 힌트와 단순 관리자 흐름만 참고한다.

### 8.8 echoCTF.RED

[echoCTF.RED](https://github.com/echoCTF/echoCTF.RED)는 단발성 Jeopardy보다 실제 네트워크 타깃을 오래 공격하는 cyber range에 가깝다.

- 개인·팀 점수판
- 참가자·팀별 고유 플래그
- 타깃 start, stop, restart, log, exec, healthcheck
- on-demand target
- 동적 network, 환경 변수, volume
- public·private network와 OpenVPN
- REST와 WebSocket 활동 스트림
- 세밀한 flag·network 감사

[공식 아키텍처](https://echoctfred.readthedocs.io/ARCHITECTURE/)는 PHP 8.2·Yii2, 관리자 backend, MariaDB·MySQL, memcached, Docker API server, target host, 선택적 OpenVPN gateway를 분리한다. BSD-2-Clause이며 기본 브랜치는 2026-08-17까지 활동했다.

CTFnight가 장기 실습 환경으로 확장될 때는 중요한 참고 자료지만, 초기 MVP에 이 네트워크 운영 복잡도를 넣는 것은 과도하다.

---

## 9. Attack–Defense 엔진

### 9.1 FAUST CTF Gameserver

[FAUST CTF Gameserver](https://github.com/fausecteam/ctf-gameserver)는 재사용 가능하고 실제 대회에서 검증된 Attack–Defense 게임 서버다.

구성:

- Django web: 등록, 점수판, 안내, DB 모델
- controller: tick과 flag 배치
- checker master와 checker script
- Python·Go checker library
- flag submission server
- 선택적 VPN 상태 수집

checker는 각 팀 서비스에 flag를 넣고 다시 읽어 서비스 상태와 무결성을 확인한다. 라이선스는 ISC이고 기본 브랜치는 2026-08-08까지 갱신됐다. GitHub release 날짜가 오래됐다고 해서 유지보수 종료로 판단하면 안 되는 사례다.

### 9.2 EnoEngine

[EnoEngine](https://github.com/enowars/EnoEngine)은 ENOWARS의 고성능 C# Attack–Defense 엔진이다.

- PostgreSQL과 Docker·dotnet 배포
- EnoConfig, EnoLauncher, EnoFlagSink, EnoEngine 분리
- putflag, getflag, putnoise, getnoise, havoc checker 흐름
- 라운드별 flag 유효성
- TCP flag 제출
- 자가 제출, 오래된 flag, 중복 flag, 잘못된 sender, spam 검증
- 공격·방어·SLA와 서비스별 상태 분리

MIT 라이선스다. 조직은 2026년에도 활동하지만 일부 README의 SDK 버전 표기가 오래됐으므로 실제 도입 시 이미지와 dependency를 고정하고 재현 시험해야 한다.

### 9.3 ctf01d

[ctf01d](https://github.com/sea5kg/ctf01d)는 비교적 작은 Attack–Defense jury다.

- up, down, corrupt, mumble 같은 checker 상태
- 공격·방어·SLA 점수
- flag 수명과 제출 API
- C++ 웹 서버
- 내장 SQLite
- Docker Compose

MIT 라이선스이고 최신 [v0.7.3](https://github.com/sea5kg/ctf01d/releases/tag/v0.7.3)은 2026-06-10, 기본 브랜치는 2026-08-20까지 갱신됐다. FAUST보다 작은 구현에서 A/D 코어 흐름을 읽기 좋다.

### 9.4 ForcAD

[ForcAD](https://github.com/pomo-mondreganto/ForcAD)는 순수 Python 중심의 배포 가능한 A/D 플랫폼이다.

- PostgreSQL, Redis, RabbitMQ
- Celery와 Flower
- 설정 파일 중심 팀·서비스·checker·round·flag lifetime
- Docker Compose
- Hackerdom 호환 checker

GPL-3.0이고 마지막 활동은 2023-12-06 수준이다. 설치 학습에는 좋지만 현행 기준점은 FAUST와 ctf01d가 더 적합하다.

### 9.5 Cardinal

[Cardinal](https://github.com/05sec/Cardinal)은 Go 기반 AWD 플랫폼이다.

- 팀 머신과 flag 생성·내보내기
- round
- 공격과 checkdown 점수
- 자동 flag 교체
- webhook과 실시간 머신 상태

AGPL-3.0이며 2024-04-17 보관됐다. 지역형 AWD 설계의 역사적 참고 자료로만 본다.

### 9.6 CTFnight에 A/D를 넣는 방법

초기 Jeopardy 코어에 A/D column 몇 개를 추가하는 방식은 피한다. 장기적으로 다음 경계를 둔다.

    Event
      ├─ JeopardyGameEngine
      │    ├─ Challenge
      │    ├─ Submission
      │    └─ Solve / Award
      └─ AttackDefenseGameEngine
           ├─ Tick
           ├─ Service
           ├─ CheckerRun
           ├─ FlagLease
           ├─ AttackSubmission
           └─ SLA / Attack / Defense score

공통으로 쓸 수 있는 것은 사용자, 팀, 권한, 공지, 감사, 이벤트 시간, UI shell 정도다. 경기 상태와 점수 원장은 별도 모듈이어야 한다.

---

## 10. 문제 실행 인프라와 제어 엔진의 경계

### 10.1 Google kCTF

[Google kCTF](https://github.com/google/kctf)와 [공식 문서](https://google.github.io/kctf/)는 Kubernetes 기반 문제 인프라다. 계정, 팀, 점수판을 제공하는 대회 엔진이 아니다.

CTFnight가 배울 부분:

- 로컬과 GCP 운영 흐름
- challenge별 namespace와 자원 격리
- 취약 서비스 운영 위협 모델
- 빌드와 배포 manifest
- 인터넷 노출과 내부 제어면 분리

### 10.2 rCDS

[rCDS](https://github.com/redpwn/rcds)는 중앙 challenge Git 저장소를 원천으로 삼아 CI에서 다음을 동기화한다.

- 문제 이미지
- 서비스 배포
- 첨부 파일
- 점수판 문제 metadata
- Git history 기반 감사와 rollback

Git 기반 point-in-time rollback은 장점이지만 [공식 challenge 설정 문서](https://rcds.redpwn.net/en/latest/challenge/)는 배포된 문제를 제거하거나 visible을 false로 바꿀 때 일부 scoreboard backend가 문제와 solve까지 삭제할 수 있다고 경고한다. 동기화 전에 backend별 파괴적 변경을 preview하고 DB를 백업해야 한다.

rCDS는 BSD-3-Clause이고 공식적으로 archived 또는 deprecated라고 선언되지는 않았다. 다만 기본 브랜치 [마지막 커밋](https://github.com/redpwn/rcds/commits/master)이 2023-06-12이고 [pyproject.toml](https://github.com/redpwn/rcds/blob/master/pyproject.toml)이 Python 3.6 계열과 오래된 dependency를 기준으로 한다. 따라서 신규 핵심 의존성보다 개념 참고가 적절하다는 것은 이 조사에서 내린 유지보수 위험 평가다.

### 10.3 picoCTF cmgr

cmgr의 중요한 교훈은 배포 전 자동 solver로 문제 정답 가능성을 확인하고, source·artifact·image·flag 규칙을 하나의 revision으로 취급하는 것이다.

CTFnight challenge pipeline의 이상적인 흐름:

    challenge repository
      → schema 검증
      → image build
      → dependency·secret 검사
      → 자동 solver 및 healthcheck
      → artifact 생성
      → registry push
      → runtime 배포
      → scoreboard metadata 원자적 반영
      → smoke test

### 10.4 Haaukins, Beast와 playCTF

- [Haaukins](https://github.com/aau-network-security/haaukins)는 Go, Docker, VirtualBox, gRPC를 이용한 교육용 가상화 플랫폼이다. 점수판보다 VM·컨테이너 실습 환경 계층에 가깝다.
- [Beast](https://github.com/sdslabs/beast)는 Go, Docker, healthcheck 기반 문제 배포기이고 [playCTF](https://github.com/sdslabs/playCTF)는 참가자·관리자 UI다. 계층 분리 사례는 유용하지만 playCTF의 활동은 오래됐다.

---

## 11. 기능 축별 비교와 CTFnight의 결정

### 11.1 참가자, 팀과 Division

관찰:

- CTFd: 이벤트를 사용자 또는 팀 모드로 운영
- rCTF: 팀이 인증과 점수의 중심
- GZCTF·Flagpost: 팀 그룹, Division·bracket 강화
- 장기 플랫폼: 한 사용자가 여러 이벤트와 팀에 속할 필요

CTFnight 권장:

- User는 전역 identity
- Team은 전역 또는 이벤트 소유 객체
- EventParticipant가 개인전·팀전을 통일
- Membership에 이벤트별 역할과 유효 기간
- Division은 eligibility 규칙과 별도 scoreboard view
- 관리자와 문제 출제자 권한 분리

### 11.2 문제와 revision

문제는 mutable row 하나가 아니라 revision을 가져야 한다.

- Challenge: 논리적 정체성
- ChallengeRevision: 설명, 점수, flag 규칙, attachment, image digest
- PublishSchedule: 공개·종료 시간
- Dependency: 선행 문제와 unlock 조건
- RuntimeTemplate: 인스턴스 사양

대회 중 변경은 새 revision으로 남기고 누가 왜 바꿨는지 감사한다.

### 11.3 플래그

필요 provider:

- ExactFlag
- CaseInsensitiveFlag
- RegexFlag
- GeneratedPerTeamFlag
- ExternalValidator
- File 또는 manual review는 별도 submission type

평문 flag를 일반 로그에 남기지 않는다. 정적 flag는 필요에 따라 keyed hash 또는 암호화 저장하고, 외부 validator에는 최소 정보만 전달한다. 정규식은 ReDoS를 막기 위해 안전한 engine이나 timeout을 사용한다.

### 11.4 점수

최소 지원:

- fixed
- solve-count decay
- first-solve metadata
- award와 penalty
- scoreboard freeze
- Division별 view

확장:

- 시간 기반 점수
- 외부 signed score feed
- King of the Hill
- 별도 Attack–Defense ledger

공식은 version을 가져야 하며 변경 시 과거 solve를 재계산할지, 변경 이후에만 적용할지 정책을 명시해야 한다.

### 11.5 힌트와 선행 조건

- 무료 또는 비용 힌트
- 단계별 힌트
- 팀 단위 공개
- 선행 문제·카테고리 진행률
- 예약 공개
- 운영자 수동 unlock

점수 차감은 힌트 열람 사건으로 기록해 취소와 감사를 가능하게 한다.

### 11.6 실시간 기능

대상:

- 점수판
- 공지
- 문제 공개
- solve·first blood
- 인스턴스 상태
- 관리자 제출 stream

WebSocket 또는 Server-Sent Events를 쓸 수 있다. 중요한 것은 DB commit 전에 참가자에게 성공 이벤트를 내보내지 않는 것이다. transactional outbox로 commit 후 안정적으로 발행한다.

### 11.7 관리자와 감사

필수 권한:

- 이벤트 운영
- 문제 작성
- 문제 공개
- 팀 지원
- 제출·solve 열람
- solve 취소
- 점수 조정
- 인프라 운영
- 감사 로그 열람

관리자의 모든 쓰기 작업은 actor, 대상, 이전 값, 이후 값, 사유, 시간, request ID를 남긴다. 민감한 flag와 token 원문은 감사 로그에 포함하지 않는다.

### 11.8 호환성과 API

- versioned REST 또는 GraphQL
- OpenAPI 문서
- CTFtime JSON feed
- ctfcli 호환 또는 변환 가능한 challenge YAML
- webhook 서명과 replay 방지
- 전체 export/import
- 서비스 계정과 scope token
- pagination, idempotency key, rate-limit header

---

## 12. 보안과 운영에서 공통으로 배운 점

### 12.1 가장 중요한 신뢰 경계

    인터넷 참가자
          │
          ▼
    Edge / TLS / WAF
          │
          ▼
    CTF Control Plane
      API · Web · Admin
       │      │      │
       ▼      ▼      ▼
    PostgreSQL Redis Object Storage
       │
       ▼  제한된 명령 계약
    Runtime Broker
       │
       ├─ Docker 개발 환경
       └─ Kubernetes 운영 환경
              │
              ▼
        취약한 Challenge Workload

취약한 문제 workload는 다음에 직접 접근하지 못해야 한다.

- 플랫폼 DB
- Redis
- object storage 관리자 credential
- 관리자 UI
- cloud metadata
- 다른 팀 namespace
- Docker socket
- control-plane 내부 network

### 12.2 제출 처리의 불변식

정답 제출은 하나의 DB transaction 또는 동등한 원자적 흐름에서 다음을 검사한다.

1. 이벤트가 live 상태인가?
2. 참가자가 해당 Division과 문제에 eligible한가?
3. 문제가 공개됐고 선행 조건을 만족했는가?
4. idempotency key가 이미 처리됐는가?
5. rate limit과 최대 시도 제한을 통과했는가?
6. flag provider가 제한 시간 안에 결과를 반환했는가?
7. 동일 팀의 solve가 이미 있는가?
8. Submission과 Solve 또는 실패 기록을 일관되게 저장했는가?
9. 점수 원장과 outbox가 같은 commit에 포함됐는가?

unique constraint로 event, participant, challenge의 중복 solve를 막아야 한다. 애플리케이션의 사전 조회만 믿으면 동시 제출 경쟁 상태가 생긴다.

### 12.3 인증과 secret

- 고정된 강한 application secret
- secret manager와 key version
- 관리자 MFA
- 짧은 access token과 회전 가능한 refresh token
- session·token 폐기
- 비밀번호는 Argon2id 등 현대적 KDF
- CSRF, CSP, secure cookie, SameSite 정책
- reverse proxy의 실제 client IP 신뢰 범위 제한
- webhook HMAC, timestamp, replay cache
- 로그에서 token, flag, 이메일 최소화

### 12.4 플러그인과 테마

CTFd식 in-process 플러그인은 완전 신뢰 대상으로 관리한다.

- 검증된 registry
- checksum과 서명
- core 호환 버전 범위
- migration dry-run
- staging 설치
- disable·rollback 경로
- dependency vulnerability scan

제3자 integration은 가능하면 외부 provider로 격리한다.

### 12.5 배포와 복구

개발 프로필:

- Docker Compose
- 단일 PostgreSQL과 Redis
- local object storage
- 모의 runtime provider

운영 프로필:

- TLS reverse proxy 또는 ingress
- 관리형·고가용 PostgreSQL
- Redis
- S3 호환 object storage
- 별도 worker와 runtime broker
- Kubernetes challenge cluster
- metrics, logs, traces, alert
- PITR 가능한 DB backup
- export와 실제 restore 훈련

대회 직전에는 load test, scoreboard 재계산, 대량 동시 제출, cache 장애, DB failover, 인스턴스 생성 폭주, 종료·freeze 경계를 시험해야 한다.

---

## 13. 라이선스와 재사용 판단

### 13.1 비교적 재사용하기 쉬운 permissive 계열

| 프로젝트 | 라이선스 | 주의 |
|---|---|---|
| CTFd | Apache-2.0 | 재배포 시 라이선스·관련 고지 유지 및 수정 파일의 변경 표시, 상표권 별도 |
| rCTF v2 | Apache-2.0 | 재배포 시 라이선스·관련 고지 유지, 상표 별도 |
| redpwn/rCTF v1 | BSD-3-Clause | 보관 상태와 오래된 dependency |
| RootTheBox | Apache-2.0 | 통지와 상표 |
| picoCTF 2019 | MIT | 폐기 코드의 보안·dependency |
| picoCTF cmgr | Apache-2.0 | 엔진이 아닌 문제 관리자 |
| FAUST Gameserver | ISC | A/D 전용 |
| EnoEngine | MIT | 배포 문서 최신성 검증 |
| ctf01d | MIT | 작은 A/D 구현 |
| echoCTF.RED | BSD-2-Clause | 복잡한 운영 구조 |
| kCTF | Apache-2.0 | 문제 인프라 |

### 13.2 copyleft 또는 특별 주의

| 프로젝트 | 라이선스 | 판단 |
|---|---|---|
| Mellivora | GPL-3.0 | 파생 배포 의무 검토 |
| Flagpost | AGPL-3.0 | 네트워크 제공 시 의무 검토 |
| ForcAD | GPL-3.0 | 파생 배포 의무 검토 |
| Cardinal | AGPL-3.0 | 보관 상태 |
| GZCTF | AGPL 코어 + 제한 컴포넌트 | 파일 단위 권리 확인 필수 |
| FBCTF | CC BY-NC 4.0 | 비상업 제한, 일반적 FOSS 기반에 부적합 |
| H1ve | Apache 표기 + README 추가 제한 | 권리 범위 불명확, 직접 재사용 비권장 |

라이선스와 상표는 별개다. Apache·MIT 코드를 사용할 수 있더라도 원 프로젝트 이름과 로고를 그대로 독자 제품에 사용할 수 있다는 뜻은 아니다.

이 절은 법률 자문이 아니다. CTFnight가 공개 배포, 유료 호스팅 또는 상업 서비스를 할 예정이라면 실제 소스 파일, dependency, asset, font, icon, 상표를 모두 포함한 검토가 필요하다.

---

## 14. CTFnight의 권장 아키텍처

### 14.1 전체 구조

    Browser / CLI / Automation
              │
              ▼
       Edge · TLS · Rate Limit
              │
              ▼
         Versioned API
              │
      ┌───────┼────────┐
      ▼       ▼        ▼
    Identity Event   Admin/Audit
      │       │        │
      ├───────┼────────┤
      ▼       ▼        ▼
    Challenge Submission Scoring
      │       │        │
      └───────┼────────┘
              ▼
     PostgreSQL + Outbox
          │          │
          ▼          ▼
        Redis      Workers
                     │
          ┌──────────┼───────────┐
          ▼          ▼           ▼
       BlobStore Notification RuntimeProvider
                                  │
                                  ▼
                          Docker / Kubernetes

초기에는 모듈러 모놀리스로 시작해도 된다. 중요한 것은 코드베이스가 하나인지보다 데이터 소유권과 보안 경계가 명확한지다.

### 14.2 핵심 도메인 객체

- Event
- EventPolicy
- User
- Team
- Membership
- Division
- Role과 Permission
- Challenge
- ChallengeRevision
- FlagRule
- Hint
- Attachment
- RuntimeTemplate
- ChallengeInstance
- Submission
- Solve
- Award
- ScoreEvent
- ScoreSnapshot
- Announcement
- AuditEvent
- ExportManifest

### 14.3 Provider 계약

- ScorePolicy
- FlagValidator
- ChallengeRuntime
- BlobStore
- NotificationProvider
- IdentityProvider
- CaptchaProvider
- ModerationProvider
- AnalyticsSink

각 provider는 설정 schema, health, 권한, secret, timeout, retry, 버전을 선언해야 한다.

### 14.4 이벤트 상태 기계

    draft
      ↓
    registration
      ↓
    live
      ↓
    frozen
      ↓
    ended
      ↓
    archived

허용되는 쓰기 작업을 상태별로 정의한다.

- draft: 문제와 정책 편집
- registration: 참가 등록, 비공개 문제 준비
- live: 제출과 점수 반영
- frozen: 제출은 받되 공개 점수판 snapshot 고정 가능
- ended: 새 제출·외부 점수 갱신 거부
- archived: 읽기 전용, export와 공개 기록

### 14.5 플러그인 전략

세 선택지가 있다.

1. **CTFd fork**
   - 가장 빠른 시작
   - 생태계 즉시 활용
   - 독자 아키텍처와 upstream 추적 사이에 충돌

2. **CTFd 호환 확장 제품**
   - API, ctfcli 형식, plugin 일부 호환
   - 기존 운영자의 이전 비용 감소
   - 호환성 제약이 새 설계를 제한

3. **독립 구현**
   - CTFnight 목표와 가장 잘 맞음
   - 도메인과 보안 경계를 처음부터 설계
   - MVP 개발 비용과 생태계 구축 비용이 큼

현재 목표 문구인 “나만의 독자적인 CTFd”에는 **독립 구현 + 선택적 CTFd 데이터·challenge 형식 import**가 가장 잘 맞는다. CTFd 코드를 복사하지 않고 사용자 기대와 공개 API·export 개념을 연구해 새 계약을 설계한다.

---

## 15. 권장 개발 단계

### 0단계 — 제품 결정과 위협 모델

- CTFnight 공개 이름 조사 완료; 정식 상표 권리 검토는 공개·상업 배포 전 별도 진행
- 독립 구현, fork, 호환 구현 중 선택 확정
- Jeopardy 우선 여부 확정
- 예상 동시 참가자·팀·문제 수
- single event와 multi-tenant 서비스 범위
- 공개·비공개·상업 배포 정책
- dependency와 라이선스 정책
- 제어면·문제면 threat model

### 1단계 — Jeopardy MVP

- 사용자, 팀, 멤버십
- 이벤트 상태
- 문제 revision과 attachment
- static·regex flag
- fixed·decay score
- 제출, solve, award와 원장
- 관리자 RBAC와 감사 로그
- 공지와 점수판
- freeze와 동점 정책
- Docker Compose
- export/import

초기 문제 연결은 static file과 외부 remote endpoint로 제한한다. 취약 컨테이너 자동 배포는 뒤로 미룬다.

### 2단계 — 운영성과 생태계

- versioned API와 OpenAPI
- CLI와 challenge-as-code
- CTFtime feed
- PostgreSQL migration 정책
- Redis cache와 worker
- S3 object storage
- OIDC, 이메일, captcha
- metrics, logs, trace
- backup, restore, disaster recovery
- load와 race test
- signed webhook

### 3단계 — 문제 인스턴스

- 별도 runtime broker
- Docker 개발 provider
- Kubernetes 운영 provider
- 팀별 instance
- 동적 flag
- quota, timeout, extend, stop
- image allowlist와 digest pinning
- namespace와 network policy
- per-connection sandbox 지원
- source·artifact·image·metadata revision 동기화

### 4단계 — 선택 확장

- Division과 교육 과정
- 공동 노트와 지원 ticket
- workflow automation
- story와 게임화
- writeup 수집·심사
- 외부 dynamic score feed
- 장기 cyber range

### 5단계 — Attack–Defense

Jeopardy와 별도 game engine으로 다음을 구현한다.

- tick scheduler
- checker protocol과 worker
- flag lease와 제출 서버
- 서비스 상태
- attack, defense, SLA ledger
- team network와 VPN integration
- A/D 전용 점수판

---

## 16. 피해야 할 설계

- CTF 웹 애플리케이션에 Docker socket을 직접 mount
- 취약 문제와 사용자 DB를 같은 network·credential로 운영
- score 총합만 저장하고 원장·재계산 근거를 버림
- 중복 solve 방지를 애플리케이션 조회에만 의존
- 플러그인을 격리 없이 설치하면서 신뢰 경계를 문서화하지 않음
- latest 이미지 tag로 대회 당일 배포
- migration 전에 DB backup과 staging 시험을 생략
- 외부 score feed가 대회 종료 뒤에도 점수를 변경하게 둠
- 장기 만료 token만 제공하고 세션 폐기 기능을 두지 않음
- flag와 token 원문을 로그·trace·오류 추적 서비스에 기록
- 정규식 flag를 timeout 없이 실행
- 이벤트 상태를 여러 boolean으로 흩어 놓음
- Jeopardy와 Attack–Defense를 같은 Submission·Score table에 억지로 합침
- GZCTF 제한 컴포넌트, FBCTF, H1ve 코드를 권리 확인 없이 복사
- 보관된 프로젝트의 오래된 dependency를 그대로 재배포
- 브랜드와 오픈소스 라이선스를 같은 문제로 간주

---

## 17. CTFnight의 비교 기준표

향후 기술 선택이나 프로토타입 평가 때 아래 항목을 공통 점수표로 사용한다.

| 영역 | 확인 질문 |
|---|---|
| 도메인 | 여러 이벤트, 개인전·팀전, Division을 자연스럽게 표현하는가? |
| 제출 | 원자성, 멱등성, 중복 방지, rate limit이 명확한가? |
| 점수 | 공식 version, 원장, 재계산, freeze, 동점 정책이 있는가? |
| 문제 | revision, 첨부, 선행 조건, 예약 공개가 있는가? |
| 인스턴스 | Docker·Kubernetes가 provider로 분리되는가? |
| 보안 | 제어면과 문제면이 네트워크·권한으로 격리되는가? |
| 확장 | plugin 또는 provider 계약이 versioned인가? |
| 운영 | health, readiness, graceful shutdown, 관측성이 있는가? |
| 복구 | 전체 export와 실제 restore가 가능한가? |
| 호환 | OpenAPI, CTFtime, challenge YAML, webhook을 제공하는가? |
| 관리자 | RBAC, MFA, 감사, 변경 사유와 rollback이 있는가? |
| 라이선스 | 코드·asset·상표를 독자 제품에서 사용할 수 있는가? |
| 유지보수 | 릴리스뿐 아니라 commit, 보안 공지, migration 경로가 살아 있는가? |

---

## 18. 최종 추천

CTFnight의 1차 목표는 **현대적인 Jeopardy 제어 엔진**으로 한정하는 것이 좋다.

추천 조합:

- CTFd에서 관리자 경험, plugin·theme·export 생태계를 학습
- rCTF v2에서 provider, scaling, revision·instancer 연결을 학습
- GZCTF에서 팀별 인스턴스와 실시간 운영 UX를 학습하되 코드는 라이선스 경계 밖에서 다룸
- RootTheBox에서 게임화가 코어를 복잡하게 만드는 지점을 학습
- Flagpost에서 자동화와 세분 권한을 연구
- picoCTF cmgr와 kCTF에서 문제 build·검증·격리 수명주기를 학습
- FAUST와 EnoEngine에서 A/D가 별도 엔진이어야 하는 이유를 학습

권장 기술 방향은 특정 언어보다 다음 성질을 우선한다.

- PostgreSQL 중심의 강한 트랜잭션
- Redis는 cache·rate limit·queue 보조이며 영속 원장의 대체가 아님
- S3 호환 object storage
- 모듈러 모놀리스 제어면
- transactional outbox와 worker
- 외부 runtime broker
- versioned provider와 API
- Docker Compose 개발 배포
- Kubernetes 문제 인프라

가장 먼저 구현해야 할 것은 화려한 UI나 컨테이너 자동 배포가 아니라 **정확한 이벤트 상태, 참가자 모델, 제출 트랜잭션, 점수 원장, 관리자 감사**다. 이 기반이 맞아야 이후 테마, 게임화, 동적 컨테이너, 교육 기능을 안전하게 확장할 수 있다.

---

## 19. 공식 자료 색인

### 핵심 Jeopardy 엔진

- [CTFd 공식 문서](https://docs.ctfd.io/)
- [CTFd GitHub](https://github.com/CTFd/CTFd)
- [CTFd 플러그인](https://docs.ctfd.io/docs/plugins/overview/)
- [CTFd API](https://docs.ctfd.io/docs/api/getting-started/)
- [CTFd 배포 설정](https://docs.ctfd.io/docs/deployment/configuration/)
- [CTFd 업데이트](https://docs.ctfd.io/docs/deployment/updating/)
- [현행 rCTF 문서](https://rctf.osec.io/)
- [현행 rCTF GitHub](https://github.com/otter-sec/rctf)
- [rCTF architecture](https://rctf.osec.io/installation/architecture/)
- [rCTF scaling](https://rctf.osec.io/installation/scaling/)
- [rCTF provider](https://rctf.osec.io/providers/)
- [legacy redpwn/rCTF](https://github.com/redpwn/rctf)
- [GZCTF 공식 문서](https://gzctf.gzti.me/)
- [GZCTF GitHub](https://github.com/GZTimeWalker/GZCTF)
- [RootTheBox](https://github.com/moloch--/RootTheBox)
- [Flagpost 공식 문서](https://docs.flagpost.io/start/introduction/)
- [Mellivora](https://github.com/Nakiami/mellivora)

### 교육형·역사적 엔진

- [picoCTF 2019 플랫폼](https://github.com/picoCTF/picoCTF)
- [picoCTF cmgr](https://github.com/picoCTF/cmgr)
- [FBCTF](https://github.com/facebookarchive/fbctf)
- [HackTheArch](https://github.com/mcpa-stlouis/hack-the-arch)
- [H1ve](https://github.com/D0g3-Lab/H1ve)
- [echoCTF.RED](https://github.com/echoCTF/echoCTF.RED)
- [Haaukins](https://github.com/aau-network-security/haaukins)

### Attack–Defense

- [FAUST CTF Gameserver](https://github.com/fausecteam/ctf-gameserver)
- [FAUST Ansible 배포](https://github.com/fausecteam/ctf-gameserver-ansible)
- [EnoEngine](https://github.com/enowars/EnoEngine)
- [ctf01d](https://github.com/sea5kg/ctf01d)
- [ForcAD](https://github.com/pomo-mondreganto/ForcAD)
- [Cardinal](https://github.com/05sec/Cardinal)

### 문제 인프라와 관리 도구

- [Google kCTF](https://github.com/google/kctf)
- [Google kCTF 문서](https://google.github.io/kctf/)
- [rCDS](https://github.com/redpwn/rcds)
- [ctfcli](https://docs.ctfd.io/docs/management/ctfcli/overview/)
- [ctfcli GitHub와 라이선스](https://github.com/CTFd/ctfcli)
- [Beast](https://github.com/sdslabs/beast)
- [playCTF](https://github.com/sdslabs/playCTF)

---

## 20. 조사 상태 메모

- 유지보수 상태와 최신 버전은 2026-08-24 시점의 스냅샷이며 이후 바뀔 수 있다.
- 공개 저장소가 있다고 해서 모든 파일을 자유롭게 수정·재배포할 수 있는 것은 아니다.
- CTF 엔진과 challenge infrastructure를 같은 제품 범주로 단순 비교하지 않았다.
- 요구사항, 보안 불변식, 기술 스택과 Jeopardy MVP 범위는
  [구현 계약](../ARCHITECTURE.md)으로 확정하고 애플리케이션에 반영했다.
- Attack–Defense, challenge runtime provider와 정식 상표 권리 검토는 후속 범위로 남아 있다.
