FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e .

COPY app/ app/
COPY templates/ templates/

VOLUME ["/data"]

ENV DATA_DIR=/data
ENV SYSTEM_VERSION_LIMIT=5
ENV THUMBNAIL_VERSION_LIMIT=3

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
