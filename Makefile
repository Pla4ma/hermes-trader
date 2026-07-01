.PHONY: help install test lint clean scaffold

help:
	@echo "Hermes Trader Makefile"
	@echo ""
	@echo "  make install      Install dependencies"
	@echo "  make test         Run test suite"
	@echo "  make lint         Run lint checks"
	@echo "  make clean        Clean build artifacts"
	@echo "  make healthcheck  Run health check"
	@echo "  make research     Run research-only cycle"
	@echo "  make paper-cycle  Run paper autonomous cycle"
	@echo "  make eod-report   Run end-of-day report"
	@echo "  make cron-install Install cron jobs"

install:
	cd /opt/hermes-trader && uv pip install -e .

test:
	cd /opt/hermes-trader && python -m pytest tests/ -v

lint:
	cd /opt/hermes-trader && python -m py_compile src/hermes_trader/*.py src/hermes_trader/**/*.py

clean:
	find /opt/hermes-trader -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find /opt/hermes-trader -type f -name '*.pyc' -delete 2>/dev/null || true

healthcheck:
	bash /opt/hermes-trader/scripts/run_healthcheck.sh

research:
	bash /opt/hermes-trader/scripts/run_research_cycle.sh

paper-cycle:
	bash /opt/hermes-trader/scripts/run_paper_cycle.sh

eod-report:
	bash /opt/hermes-trader/scripts/run_eod_report.sh

cron-install:
	bash /opt/hermes-trader/scripts/install_cron.sh