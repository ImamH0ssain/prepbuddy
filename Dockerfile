FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN useradd --create-home --shell /bin/bash prepbuddy

COPY pyproject.toml README.md ./
COPY src ./src

RUN python -m pip install --upgrade pip \
    && python -m pip install ".[ui]"

COPY assessment_brief.pdf SLATEFALL_DOSSIER.pdf ./
COPY config ./config

RUN mkdir -p data outputs docs \
    && chown -R prepbuddy:prepbuddy /app

USER prepbuddy

EXPOSE 8000

CMD ["prepbuddy", "api", "--host", "0.0.0.0", "--port", "8000"]

