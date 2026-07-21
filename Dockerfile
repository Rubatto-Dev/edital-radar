FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# app/ is copied before the install because pyproject declares it as the
# package; installing first would fail on a missing package directory.
COPY pyproject.toml ./
COPY app ./app
RUN uv pip install --system --no-cache .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
