.PHONY: test layout-test history-test test-gpu build-gpu test-storage verify diff-check

test: layout-test history-test

layout-test:
	python3.12 -m unittest tests.test_repository_layout -v

history-test:
	python3.12 -m unittest tests.test_history_inventory -v

test-gpu:
	cd apps/gpu-monitor/frontend && npm run check
	cd apps/gpu-monitor && SECRET_KEY=baseline-test-key ADMIN_PASSWORD=baseline-test-password python3.12 -m unittest discover -s backend/tests -v

build-gpu:
	cd apps/gpu-monitor/frontend && npm run build

test-storage:
	cd apps/storage-monitor && python3 -m pytest -q -p no:cacheprovider
	cd apps/storage-monitor && find viewer -maxdepth 1 -name '*.js' -print0 | xargs -0 -n1 node --check
	cd apps/storage-monitor && bash deploy/test_deploy_scripts.sh
	@if [ "$$(uname -s)" = Linux ]; then \
		cd apps/storage-monitor && bash scanner/test_hstscan.sh && bash deploy/verify-linux.sh --local; \
	else \
		printf '%s\n' 'SKIP: Linux-only scanner tests use SYS_getdents64; covered by Task 3 remote Linux verification.'; \
	fi

verify: layout-test history-test test-gpu build-gpu test-storage diff-check

diff-check:
	git diff --check
