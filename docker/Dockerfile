FROM python:3.12-slim

WORKDIR /app

# Install dependencies first — separate layer so rebuilds are fast when only code changes
COPY pyproject.toml .
RUN pip install --no-cache-dir \
    "fastapi==0.136.1" \
    "uvicorn[standard]==0.46.0" \
    "sqlalchemy==2.0.49" \
    "aiosqlite==0.22.1" \
    "jinja2==3.1.6" \
    "python-multipart==0.0.28" \
    "lxml==6.1.0" \
    "aiofiles==25.1.0"

# Copy source and install the package itself (no-deps since already installed above)
COPY app/ app/
COPY templates/ templates/
COPY static/ static/
RUN pip install --no-cache-dir --no-deps .

ENV DATA_DIR=/data
ENV SYSTEM_VERSION_LIMIT=5
ENV THUMBNAIL_VERSION_LIMIT=3

EXPOSE 8080

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
