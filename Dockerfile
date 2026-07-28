FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# app/ and evals/ are copied before the install because pyproject declares
# both as packages (setuptools needs the directories to exist to build);
# installing first would fail on a missing package directory. evals/ is
# also bind-mounted at runtime below — this copy only satisfies the build.
COPY pyproject.toml ./
COPY app ./app
COPY evals ./evals
# [embeddings] (torch + sentence-transformers, ~2GB) is in the image on
# purpose from Phase 1.7 on: /query embeds the user's question at request
# time, so the API container needs the model, not just the ingester/CLI.
RUN uv pip install --system --no-cache ".[embeddings]"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
