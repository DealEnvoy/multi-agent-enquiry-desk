.PHONY: test demo

PYTHON ?= python3

test:
	$(PYTHON) -m pytest

demo:
	$(PYTHON) -m enquiry_desk demo
