.PHONY: all venv run test test-only clean render-mod

all: venv

venv: .venv/bin/python
.venv/bin/python:
	python3 -m venv .venv
	.venv/bin/pip install -U pip wheel
	.venv/bin/pip install -e .

run: venv
	.venv/bin/python tracker.py

# Full QA harness
test: venv
	.venv/bin/python -m tests.qa

# Subset by substring (e.g. `make test-only PAT=undo`)
test-only: venv
	.venv/bin/python -m tests.qa $(PAT)

# Quick perf probe
perf: venv
	.venv/bin/python -m tests.perf

# Render the current demo song to a .mod file (smoke test the writer)
render-mod: venv
	.venv/bin/python -m tracker_tui.mod_writer demo.mod

clean:
	rm -rf .venv tracker_tui/__pycache__ tests/__pycache__ tests/out/*.svg
