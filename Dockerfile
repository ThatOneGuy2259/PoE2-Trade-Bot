FROM python:3.11-slim

# ca-certificates so outbound TLS to Discord + poe2scout verifies (slim base may omit it)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
COPY poe2bot ./poe2bot
RUN pip install --no-cache-dir .

ENV DB_PATH=/data/poe2bot.db PYTHONUNBUFFERED=1
VOLUME ["/data"]
CMD ["python", "-m", "poe2bot.main"]
