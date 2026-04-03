SHELL := /usr/bin/env bash

.PHONY: init close audit poll

init:
	python3 tools/agentctl.py init

poll:
	python3 tools/agentctl.py poll

close:
	python3 tools/agentctl.py close

audit:
	python3 tools/agentctl.py audit
