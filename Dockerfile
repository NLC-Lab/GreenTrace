FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    GREENTRACE_DATA_DIR=/app/data

WORKDIR /app
COPY pyproject.toml README.md ./
COPY greentrace ./greentrace

RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown -R appuser:appuser /app

USER appuser
EXPOSE 8000
VOLUME ["/app/data"]

CMD ["uvicorn", "greentrace.api:app", "--host", "0.0.0.0", "--port", "8000"]
