FROM python:3.11-slim
WORKDIR /app
COPY pyproject.toml ./
COPY poe2bot ./poe2bot
RUN pip install --no-cache-dir .
ENV DB_PATH=/data/poe2bot.db
VOLUME ["/data"]
CMD ["python", "-m", "poe2bot.main"]
