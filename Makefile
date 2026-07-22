.PHONY: test layout-test history-test gpu-frontend-check gpu-frontend-build storage-tests diff-check

test: layout-test history-test

layout-test:
	python3.12 -m unittest tests.test_repository_layout -v

history-test:
	python3.12 -m unittest tests.test_history_inventory -v

gpu-frontend-check:
	cd apps/gpu-monitor/frontend && npm run check

gpu-frontend-build:
	cd apps/gpu-monitor/frontend && npm run build

storage-tests:
	cd apps/storage-monitor && python3.12 -m unittest \
		agent.test_block_media \
		agent.test_mount_policy \
		agent.test_scan_runner \
		collector.test_inventory \
		collector.test_jobs \
		collector.test_service \
		collector.test_snapshot \
		collector.test_store \
		collector.test_transport \
		viewer.test_serve

diff-check:
	git diff --check
