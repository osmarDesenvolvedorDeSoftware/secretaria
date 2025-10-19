FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY . /app

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    FLASK_APP=run.py \
    LOGGING_CONFIG=/etc/secretaria/logging.conf \
    APP_LOG_FILE=/var/log/secretaria/app.log

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && adduser --system --group --no-create-home secretaria \
    && mkdir -p /var/log/secretaria /etc/secretaria \
    && chown -R secretaria:secretaria /var/log/secretaria /etc/secretaria

COPY --from=builder /opt/venv /opt/venv
COPY --from=builder /app /app
COPY --from=builder /app/logging.conf /etc/secretaria/logging.conf

RUN chown -R secretaria:secretaria /app

USER secretaria

EXPOSE 8080

CMD ["python", "run.py"]
