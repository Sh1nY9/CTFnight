# CTFnight frontend

React, TypeScript, Vite로 만든 CTFnight의 참가자·관리자 SPA다. 공개·접근 코드 가입,
팀 소유권 이전·멤버 제거, 참가자 정지·복구와 등록 코드 운영 화면을 제공한다. 모든 API 요청은
같은 origin의 `/api/v1`을 사용한다.

## 로컬 실행

Node.js 22와 npm이 필요하다.

```bash
npm ci
npm run dev
```

`dev`는 기본적으로 host loopback `127.0.0.1`에만 bind하고 `/api` 요청을
`http://localhost:8000`으로 프록시한다. 운영 bundle을 로컬에서 확인할 때도 build 후
`preview`가 같은 loopback 기본값을 사용한다.

```bash
npm run build
npm run preview
```

### LAN 접속 opt-in

다른 장치에서 접근해야 할 때만 명시적으로 bind를 넓힌다.

```bash
npm run dev -- --host 0.0.0.0
# 또는 이미 빌드한 bundle만 확인
npm run preview -- --host 0.0.0.0
```

이 override는 모든 host interface에 개발 서버를 노출한다. Vite dev/preview는 production
Caddy의 자동 TLS, 공개 배포 검증과 운영 경계를 대신하지 않는다. 신뢰된 격리 LAN에서 host
firewall로 접근자를 제한한 짧은 시험에만 사용하고 인터넷·공용 Wi-Fi·운영 배포에는 쓰지
않는다. `/api` proxy까지 함께 노출되므로 local backend에 실제 운영 데이터나 credential을
연결하지 않는다.

## 검증

```bash
npm run typecheck
npm test
npm run build
```

## 운영 이미지

`Dockerfile`은 다이제스트로 고정한 Chainguard Node 개발 이미지에서 `npm ci --ignore-scripts`로 SPA를 빌드하고, 다이제스트로 고정한 non-root Chainguard nginx가 `:8080`에서 정적 파일을 제공한다. 외부 edge Caddy가 `/api/*`는 backend로, 나머지는 이 컨테이너로 프록시해야 한다.

SPA fallback과 보안 헤더는 `nginx.conf`가 담당한다. `/assets/*`만 immutable cache를 허용하고 SPA HTML과 `/healthz`는 `no-store`다. `Caddyfile`은 이전 배포와의 호환 확인용으로만 유지하는 deprecated 설정이다.

## 보안 계약

- 세션: production `__Host-alpha_session`, development `alpha_session` HttpOnly cookie
- CSRF 발급: `GET /api/v1/auth/csrf`의 `{ "csrf_token": "..." }`
- 변경 요청: `X-CSRF-Token` header와 same-origin credentials
- API 오류: `{ "error": { "code", "message", "request_id" } }`
- 서버 상한: `GET /api/v1/meta`가 제출 1,000/10,000, 전체 participant 100,000,
  사용자별 active session 10을 공개하지만 최종 등록·session 판단은 backend가 수행
- 가입 방식: event의 `registration_access_mode`가 `code`이면 가입 form이 접근 코드를
  요구한다. frontend 검사는 안내용이며 code 유효성·사용량·만료·폐기의 최종 판단은 backend다.
  누락·미등록·만료·소진·폐기는 backend의 동일한 generic denial을 그대로 표시한다.
- 등록 코드 관리: 관리자는 label, 1~10,000회 또는 무제한 사용량과 선택적 만료를 지정하고
  폐기할 수 있다. 새 평문은 React state로 생성 직후 한 번만 표시하고 `localStorage`, URL,
  목록이나 client log에 보존하지 않는다. DB의 `registration-access` HMAC, row lock과
  User·Session·사용량 transaction은 backend의 신뢰 경계다.
- 참가자 관리: 사용자 목록에서 participant만 정지·재활성화한다. 정지는 사유 입력과 두 번째
  확인을 요구하지만 관리자 권한, `credential_version` 증가와 session 폐기는 backend 계약이다.
- 팀 관리: owner는 등록 기간의 팀전에서 멤버에게 소유권을 이전하거나 멤버를 제거한다.
  제거 응답의 회전된 새 초대 코드는 한 번만 표시하며 leave/remove 가능 여부와 제출 이력
  검사는 backend가 결정한다.
- Markdown: 원시 HTML을 렌더링하지 않는 `react-markdown` 기본 정책
- 문제 플래그: 관리자 write 요청에서만 `{ type, value }`로 전송하며 응답에는 원문이 없음

`VITE_*`에 비밀을 넣지 않는다. Vite 환경 변수는 최종 브라우저 번들에 포함된다.

이 화면들은 `20260824_0004`가 적용된 backend의 `registration_access_mode`와
`registration_codes` API를 전제로 한다. schema와 backend/frontend revision을 분리하거나
rolling으로 혼합하지 않고 상위 문서의 maintenance/all-at-once 절차로 배포한다.
