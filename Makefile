ifeq ($(OS),Windows_NT)
PYTHON ?= python
else ifneq ($(wildcard /proc/sys/fs/binfmt_misc/WSLInterop),)
PYTHON := /usr/bin/python3
else
PYTHON ?= python3
endif

.PHONY: build pdf epub mobi build-local build-docker release verify doctor clean help

build:
	$(PYTHON) scripts/build_book.py build --engine auto

pdf:
	$(PYTHON) scripts/build_book.py build --engine auto --format pdf

epub:
	$(PYTHON) scripts/build_book.py build --engine auto --format epub

mobi:
	$(PYTHON) scripts/build_book.py build --engine auto --format mobi

build-local:
	$(PYTHON) scripts/build_book.py build --engine local

build-docker:
	$(PYTHON) scripts/build_book.py build --engine docker

release:
ifeq ($(OS),Windows_NT)
	powershell -NoProfile -ExecutionPolicy Bypass -File scripts/release.ps1 $(if $(VERSION),--version $(VERSION),)
else
	$(PYTHON) scripts/release.py $(if $(VERSION),--version $(VERSION),)
endif

verify:
	$(PYTHON) scripts/build_book.py verify

doctor:
	$(PYTHON) scripts/build_book.py doctor

clean:
	$(PYTHON) scripts/build_book.py clean

help:
	@echo "make build         Build PDF, EPUB and MOBI locally (use WSL on Windows)"
	@echo "make pdf           Build PDF only"
	@echo "make epub          Build EPUB only"
	@echo "make mobi          Build MOBI (and the EPUB it is derived from)"
	@echo "make build-local   Require local Pandoc, XeLaTeX and Calibre"
	@echo "make build-docker  Force the reproducible Docker builder"
	@echo "make release       Auto-release after >10 commits or >24 hours"
	@echo "make release VERSION=v0.2.0  Release an explicit semantic version"
	@echo "make verify        Validate existing files in dist/"
	@echo "make doctor        Show source and dependency diagnostics"
	@echo "make clean         Remove generated .build/ and dist/ directories"
