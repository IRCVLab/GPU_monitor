SHELL := /bin/bash
.PHONY: test layout-test history-test impact-test policy-test deploy-readiness-test test-gpu build-gpu test-storage verify diff-check

test: layout-test history-test
	$(MAKE) impact-test
	$(MAKE) policy-test
	$(MAKE) deploy-readiness-test

layout-test:
	PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_repository_layout -v

history-test:
	PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_history_inventory -v

impact-test:
	PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_ci_impact -v

policy-test:
	PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_workflow_policy -v
	python3.12 scripts/validate_workflows.py .github/workflows

deploy-readiness-test:
	PYTHONDONTWRITEBYTECODE=1 python3.12 -m unittest tests.test_deploy_prerequisites -v

test-gpu:
	cd apps/gpu-monitor/frontend && npm run check
	cd apps/gpu-monitor && SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password python3.12 -m unittest discover -s backend/tests -v

build-gpu:
	cd apps/gpu-monitor/frontend && npm run build

test-storage:
	@set -euo pipefail; \
	assembled=$$(git rev-parse --show-toplevel); \
	storage_verify=$$(mktemp -d /tmp/storage-monorepo-command-check.XXXXXX); \
	trap 'rm -rf "$$storage_verify"' EXIT; \
	git clone --no-hardlinks "$$assembled" "$$storage_verify/repo"; \
	rsync -a --delete \
	  --exclude '.git/' \
	  --exclude '.pytest_cache/' \
	  --exclude '__pycache__/' \
	  --exclude 'output/verification/' \
	  "$$assembled/apps/storage-monitor/" \
	  "$$storage_verify/repo/apps/storage-monitor/"; \
	cd "$$storage_verify/repo/apps/storage-monitor"; \
	PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider; \
	find viewer -maxdepth 1 -name '*.js' -print0 | xargs -0 -n1 node --check; \
	bash deploy/test_deploy_scripts.sh; \
	if [ "$$(uname -s)" = Linux ]; then \
	  bash scanner/test_hstscan.sh; \
	  bash deploy/verify-linux.sh --local; \
	else \
	  printf '%s\n' 'SKIP: Linux-only scanner tests use SYS_getdents64; covered by Task 3 remote Linux verification.'; \
	fi

verify: test test-gpu build-gpu test-storage diff-check

diff-check:
	@set -euo pipefail; \
	base="$${DIFF_CHECK_BASE:-}"; \
	head="$${DIFF_CHECK_HEAD:-HEAD}"; \
	if [[ -n "$$base" ]]; then \
	  if [[ "$${DIFF_CHECK_MERGE_BASE:-false}" == true ]]; then \
	    base=$$(git merge-base "$$base" "$$head"); \
	  fi; \
	  git diff --check "$$base" "$$head"; \
	else \
	  git diff --check; \
	fi
