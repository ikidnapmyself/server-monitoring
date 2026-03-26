#!/bin/bash
#
# Docker deployment script for server-maintanence
# Pre-flight checks, build, and deploy via docker compose
#

set -e

# Get the directory where this script is located (bin/)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Project root is parent of bin/
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Compose file location
COMPOSE_FILE="$PROJECT_DIR/deploy/docker/docker-compose.yml"

# Change to project directory
cd "$PROJECT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Print colored output
info() { echo -e "${BLUE}[INFO]${NC} $1"; }
success() { echo -e "${GREEN}[SUCCESS]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ===========================================
#   Pre-flight checks
# ===========================================

echo ""
echo "============================================"
echo "   Docker Deployment — Pre-flight Checks"
echo "============================================"
echo ""

# Check 1: .env file exists
info "Checking for .env file..."
if [ ! -f "$PROJECT_DIR/.env" ]; then
    error ".env file not found."
    echo "  Run ./bin/install.sh first, or copy .env.sample to .env and configure it."
    exit 1
fi
success ".env file found"

# Check 2: Docker daemon is running
info "Checking Docker daemon..."
if ! docker info >/dev/null 2>&1; then
    error "Docker is required. Install it from https://docs.docker.com/get-docker/ and ensure the daemon is running."
    exit 1
fi
success "Docker daemon is running"

# Check 3: docker compose v2 is available
info "Checking docker compose v2..."
if ! docker compose version >/dev/null 2>&1; then
    error "docker compose v2 is required but not available."
    echo "  Docker Compose v2 is included with Docker Desktop, or can be installed as a plugin."
    echo "  See: https://docs.docker.com/compose/install/"
    exit 1
fi
COMPOSE_VERSION="$(docker compose version --short)"
success "docker compose v2 is available (v${COMPOSE_VERSION})"

echo ""
success "All pre-flight checks passed"

# --- Build & Start ---

info "Building Docker images..."
docker compose -f "$COMPOSE_FILE" build
success "Docker images built"

echo ""
info "Starting Docker Compose stack..."
docker compose -f "$COMPOSE_FILE" up -d
success "Docker Compose stack started"

echo ""