.PHONY: serve demo test rank keepalive install-keepalive verify-linux

serve:
	python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8765

demo:
	python3 -m app.cli demo

rank:
	python3 -m app.cli rank --master data/samples/master_page3_clean.xlsx --targets data/samples/targets_page3_overlap.xlsx

test:
	python3 -m pytest tests/ -q

verify-linux:
	python3 scripts/verify_linux_runtime.py

keepalive:
	bash scripts/keep_alive.sh

install-keepalive:
	bash scripts/install_keepalive_launchd.sh
