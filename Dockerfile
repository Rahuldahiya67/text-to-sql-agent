FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install ".[all]"
COPY eval ./eval

# Demo SQLite database baked into the image; override T2SQL_DATABASE_URL to use PostgreSQL.
RUN mkdir -p data && t2sql seed --url sqlite:///data/analytics.db

RUN useradd --create-home appuser && chown -R appuser /app
USER appuser
EXPOSE 8000 8501

CMD ["t2sql", "serve", "--host", "0.0.0.0", "--port", "8000"]
