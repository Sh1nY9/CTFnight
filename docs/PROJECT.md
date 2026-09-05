# CTFnight

## 프로젝트 개요

**CTFnight**라는 독자적인 CTF 엔진을 만들고 배포하는 프로젝트입니다. `Alpha`는
개발 프로젝트의 코드명으로 유지합니다.

## 상태

CTFnight의 첫 배포 가능한 Jeopardy형 MVP를 구현했습니다. FastAPI 백엔드, React 프런트엔드,
PostgreSQL·Redis, Caddy 자동 TLS와 Docker Compose 운영 구성을 포함합니다.
현재 버전은 배포당 하나의 이벤트를 `draft`부터 `archived`까지 운영하는 범위입니다.

- [애플리케이션 실행·배포 안내](../README.md)
- [아키텍처와 API 계약](../ARCHITECTURE.md)
- [오픈소스 CTF 엔진 조사](reserch.md)

실제 배포 기준 루트는 [app/](../)입니다.
