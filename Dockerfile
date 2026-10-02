FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Dependencias con hashes y solo wheels (sin compilar), igual que la instalacion nativa.
COPY requirements.txt .
RUN pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.txt

COPY jzmiddle/ jzmiddle/
COPY migrations/ migrations/
COPY frontend/ frontend/

RUN useradd --system --no-create-home --shell /usr/sbin/nologin jzmiddle
USER jzmiddle

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4)"

# La IP real la resuelve la app con TRUSTED_PROXIES (como Tracker360), no uvicorn.
CMD ["uvicorn", "jzmiddle.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers"]
