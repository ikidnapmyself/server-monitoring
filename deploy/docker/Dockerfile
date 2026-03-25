# --- Builder stage ---
FROM python:3.12-slim AS builder

WORKDIR /app

# Pin uv version for reproducible builds
COPY --from=ghcr.io/astral-sh/uv:0.5.10 /uv /usr/local/bin/uv

# Copy dependency files first for layer caching
COPY pyproject.toml uv.lock ./

# Install production dependencies including gunicorn
RUN uv sync --frozen --no-dev --extra prod --no-editable

# --- Runtime stage ---
FROM python:3.12-slim

WORKDIR /app

# Copy installed virtualenv from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY . .

# Ensure virtualenv is on PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1
ENV DJANGO_SETTINGS_MODULE=config.settings
ENV DATABASE_PATH=/app/data/db.sqlite3

EXPOSE 8000

# collectstatic + migrate run at startup (requires DJANGO_SECRET_KEY from env)
CMD ["sh", "-c", "mkdir -p /app/data && python manage.py collectstatic --noinput && python manage.py migrate --noinput && gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers ${WEB_CONCURRENCY:-3}"]