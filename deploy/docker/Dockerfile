# --- Builder stage ---
FROM python:3.12-slim AS builder

WORKDIR /app

# Pin uv version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.5.10 /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies including gunicorn
# --no-install-project: only install dependencies, not the project itself
# (source code isn't copied yet — that happens in the runtime stage for layer caching)
RUN uv sync --frozen --no-dev --extra prod --no-install-project

# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# Create non-root user for running the application
RUN groupadd --system appuser && useradd --system --gid appuser appuser

# Copy application code first, then overlay the builder's virtualenv
# (COPY . . must come before the .venv copy so it doesn't overwrite it)
COPY . .
COPY --from=builder /app/.venv /app/.venv

# Create data directory and set ownership
RUN mkdir -p /app/data && chown -R appuser:appuser /app

# Ensure virtualenv is on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=config.settings
ENV DATABASE_PATH=/app/data/db.sqlite3

EXPOSE 8000

# Run as non-root user
USER appuser

# collectstatic + migrate run at startup (requires DJANGO_SECRET_KEY from env)
CMD ["sh", "-c", "python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-3}"]