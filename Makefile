SHELL := /bin/sh
.DEFAULT_GOAL := help

# Deployment commands intentionally use one canonical Compose graph and env
# file. Command-line COMPOSE overrides could otherwise bypass the image refs
# that the fail-closed security gate rendered and scanned.
override COMPOSE := ./scripts/compose.sh
DEPLOYMENT_MANIFEST := security-reports/deployment-manifest.json
CHALLENGE ?=
BACKUP_DIR ?=

.PHONY: help init secret-acl validate check-locks security config build up bootstrap wait smoke ps logs stop down restart backup upgrade-database-roles import-challenge ci

help: ## 사용 가능한 운영 명령 표시
	@awk 'BEGIN {FS = ":.*## "; printf "CTFnight 운영 명령\n\n"} /^[a-zA-Z0-9_-]+:.*## / {printf "  %-18s %s\n", $$1, $$2}' $(MAKEFILE_LIST)

init: ## 덮어쓰기 없이 안전한 .env 생성
	./scripts/generate-env.sh

secret-acl: ## 수동 교체·복구한 secret에 UID 65532 전용 read ACL 재적용
	./scripts/set-secret-acl.sh
	./scripts/validate-env.sh .env

validate: ## secret, 주소, 파일 권한 검증
	./scripts/validate-env.sh .env

check-locks: ## pyproject 직접 의존성과 hash lock 동기화 검증
	python3 scripts/check-backend-locks.py

security: ## hash lock, npm, 파일시스템, 사용 가능한 Docker 이미지 보안 검사
	./scripts/security-scan.sh

config: validate ## 최종 Compose 구성 렌더링·검증
	$(COMPOSE) config --quiet

build: config ## 전 이미지 보안 게이트로 배포할 canonical 이미지를 한 번만 빌드
	SECURITY_REQUIRE_DOCKER=1 ./scripts/security-scan.sh
	python3 scripts/deployment-manifest.py verify-prestart \
		--manifest "$(DEPLOYMENT_MANIFEST)" --app-root . --env-file .env

up: config ## 유효한 검사 artifact를 재사용하거나 새로 만든 뒤 exact image ID만 시작
	@if python3 scripts/deployment-manifest.py verify-prestart \
		--manifest "$(DEPLOYMENT_MANIFEST)" --app-root . --env-file .env; then \
		printf '%s\n' '2시간 이내의 현재 source·graph·image security artifact를 재사용합니다.'; \
	else \
		printf '%s\n' '유효한 security artifact가 없어 canonical 이미지를 한 번 빌드·검사합니다.'; \
		SECURITY_REQUIRE_DOCKER=1 ./scripts/security-scan.sh; \
	fi
	@set -eu; \
		test -d security-reports && test ! -L security-reports || { printf '%s\n' 'security-reports는 실제 private directory여야 합니다.' >&2; exit 1; }; \
		exec 9<security-reports; \
		flock -n 9 || { printf '%s\n' '다른 security gate 또는 deployment 작업이 진행 중입니다.' >&2; exit 1; }; \
		stop_on_exit=0; \
		cleanup() { \
			status=$$?; \
			trap - 0 1 2 3 15; \
			if [ "$$stop_on_exit" -eq 1 ]; then $(COMPOSE) stop || :; fi; \
			exit "$$status"; \
		}; \
		trap cleanup 0; \
		trap 'exit 130' 1 2 3 15; \
		python3 scripts/deployment-manifest.py verify-prestart \
			--manifest "$(DEPLOYMENT_MANIFEST)" --app-root . --env-file .env; \
		stop_on_exit=1; \
		$(COMPOSE) up --detach --no-build --force-recreate --remove-orphans --wait --wait-timeout 180; \
		python3 scripts/deployment-manifest.py verify-running \
			--manifest "$(DEPLOYMENT_MANIFEST)" --app-root . --env-file .env; \
		stop_on_exit=0

bootstrap: ## 새 서버용 env 생성 → 시작 → 스모크 테스트
	$(MAKE) init
	$(MAKE) up
	$(MAKE) smoke

wait: ## 유효한 security artifact 재검증·갱신 후 현재 서비스 health 대기
	$(MAKE) up

smoke: ## 공개 주소의 읽기 전용 배포 스모크 테스트
	./scripts/smoke-test.sh

ps: ## 컨테이너와 health 상태 표시
	$(COMPOSE) ps

logs: ## 최근 로그를 따라가기
	$(COMPOSE) logs --tail=200 --follow

stop: ## 컨테이너 정지(데이터 보존)
	$(COMPOSE) stop

down: ## 컨테이너·네트워크 제거(영구 volume 보존)
	$(COMPOSE) down --remove-orphans

restart: ## 데이터를 보존하며 전체 서비스 재생성
	$(MAKE) down
	$(MAKE) up

backup: ## PostgreSQL과 복구 필수 설정을 mode-0700 디렉터리에 백업
	@if [ -n "$(BACKUP_DIR)" ]; then \
		./scripts/backup.sh "$(BACKUP_DIR)"; \
	else \
		./scripts/backup.sh; \
	fi

upgrade-database-roles: ## legacy 단일 PostgreSQL credential을 3-role secret으로 안전하게 이관
	./scripts/upgrade-database-roles.sh

import-challenge: ## CHALLENGE=challenges/.../challenge.yaml 정의 import/upsert
	@test -n "$(CHALLENGE)" || { echo 'CHALLENGE 상대 경로를 지정하세요.' >&2; exit 2; }
	@case "$(CHALLENGE)" in /*|*..*) echo '절대 경로와 .. 경로는 허용하지 않습니다.' >&2; exit 2 ;; esac
	@case "$(CHALLENGE)" in *[!A-Za-z0-9_./-]*) echo '경로에는 영문, 숫자, _, ., /, -만 사용할 수 있습니다.' >&2; exit 2 ;; esac
	@test -f "templates/$(CHALLENGE)" || { echo '템플릿 파일을 찾을 수 없습니다.' >&2; exit 2; }
	@set -eu; \
		test -d security-reports && test ! -L security-reports || { printf '%s\n' 'security-reports는 실제 private directory여야 합니다.' >&2; exit 1; }; \
		exec 9<security-reports; \
		flock -n 9 || { printf '%s\n' '다른 security gate 또는 deployment 작업이 진행 중입니다.' >&2; exit 1; }; \
		python3 scripts/deployment-manifest.py verify-prestart \
			--manifest "$(DEPLOYMENT_MANIFEST)" --app-root . --env-file .env; \
		$(COMPOSE) run --rm --no-deps --pull never -T --entrypoint python backend \
			-m alpha.cli import-challenge - < "templates/$(CHALLENGE)"

ci: ## CI/배포 전 통합 빌드·health·HTTP 검증
	$(MAKE) up
	$(MAKE) smoke
