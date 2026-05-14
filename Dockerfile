FROM python:3.10-slim

WORKDIR /app

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

COPY bot.py .

VOLUME /app/data

ENV DB_PATH=/app/data/bot.db
ENV CHECK_INTERVAL=3600

CMD ["uv", "run", "python", "bot.py"]
