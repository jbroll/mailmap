.PHONY: build clean test dev show

# Build target for deploy.sh binary_service module
# Outputs binary to build/mailmap (deploy.sh convention)
build:
	@echo "Building mailmap package..."

	# Create build directory structure
	mkdir -p build/lib

	# Install Python dependencies to build/lib
	pip install --target build/lib \
		imapclient httpx websockets html2text

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
	pytest tests/ -v

# Development - install in editable mode
dev:
	pip install -e ".[dev]"

# Show what will be deployed
show:
	@echo "Files that will be deployed:"
	@echo "  Binary: build/mailmap -> /usr/local/bin/mailmap"
	@echo "  Libraries: build/lib/ -> /usr/local/lib/mailmap/"
	@if [ -f build/config.toml ]; then echo "  Config: build/config.toml"; fi
	@if [ -f build/categories.txt ]; then echo "  Categories: build/categories.txt"; fi


inbox-zero:
	mailmap classify \
		--folder outlook.office365.com:INBOX \
		--copy \
		--target-account imap \
		--ollama-url http://gpu.local:11434 \
		--concurrency 5

