FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY weekendwander/ ./weekendwander/
COPY data/ ./data/
COPY config.yaml ./config.yaml
# Secrets injected at runtime via env (TP_TOKEN, TG_BOT_TOKEN, TG_CHAT_ID)
ENTRYPOINT ["python", "-m", "weekendwander.cli", "--config", "config.yaml"]
