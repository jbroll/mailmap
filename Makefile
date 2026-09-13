.PHONY: build clean test lint dev show

# Python version on the deploy host; compiled wheels must match it, not the local Python
VPS_PYTHON ?= 3.14

# Build target for deploy.sh binary_service module
# Outputs binary to build/mailmap (deploy.sh convention)
build:
	@echo "Building mailmap package..."

	# Start clean so packages dropped from the lock don't ship
	rm -rf build
	mkdir -p build/lib

	# Install locked runtime dependencies to build/lib
	uv export --frozen --no-dev --no-emit-project -o build/requirements.txt
	uv pip install --target build/lib --python-version $(VPS_PYTHON) --python-platform x86_64-unknown-linux-gnu -r build/requirements.txt
	rm -f build/lib/.lock

	# Copy mailmap package
	cp -r mailmap build/lib/

	# Copy config files if they exist
	@if [ -f config.toml ]; then cp config.toml build/; fi
	@if [ -f categories.txt ]; then cp categories.txt build/; fi

	# Create wrapper script in build/ (deploy.sh looks here after make)
	@echo '#!/bin/bash' > build/mailmap
	@echo '# Mailmap Email Classification Daemon' >> build/mailmap
	@echo 'set -euo pipefail' >> build/mailmap
	@echo '' >> build/mailmap
	@echo '# Determine installation directory' >> build/mailmap
	@echo 'SCRIPT_DIR="$$(dirname "$$(readlink -f "$$0")")"' >> build/mailmap
	@echo 'LIB_DIR="$${SCRIPT_DIR}/../lib/mailmap"' >> build/mailmap
	@echo 'DATA_DIR="$${MAILMAP_DATA_DIR:-/var/lib/mailmap}"' >> build/mailmap
	@echo '' >> build/mailmap
	@echo '# Set Python path to include installed dependencies' >> build/mailmap
	@echo 'export PYTHONPATH="$${LIB_DIR}:$${PYTHONPATH:-}"' >> build/mailmap
	@echo '' >> build/mailmap
	@echo '# Change to data directory for config files' >> build/mailmap
	@echo 'cd "$$DATA_DIR"' >> build/mailmap
	@echo '' >> build/mailmap
	@echo '# Execute mailmap' >> build/mailmap
	@echo 'exec python3 -m mailmap.main "$$@"' >> build/mailmap
	chmod +x build/mailmap

	@echo "Build complete! Binary: build/mailmap"

clean:
	rm -rf build/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	rm -rf *.egg-info

test:
	uv sync
	uv run pytest tests/ -v

lint:
	uv sync
	uv run ruff check .

# Development - create .venv with runtime and dev dependencies
dev:
	uv sync

# Show what will be deployed
show:
	@echo "Files that will be deployed:"
	@echo "  Binary: build/mailmap -> /usr/local/bin/mailmap"
	@echo "  Libraries: build/lib/ -> /usr/local/lib/mailmap/"
	@if [ -f build/config.toml ]; then echo "  Config: build/config.toml"; fi
	@if [ -f build/categories.txt ]; then echo "  Categories: build/categories.txt"; fi


inbox-zero:
	uv run mailmap classify \
		--folder outlook.office365.com:INBOX \
		--copy \
		--target-account imap \
		--ollama-url http://gpu.local:11434 \
		--concurrency 5

