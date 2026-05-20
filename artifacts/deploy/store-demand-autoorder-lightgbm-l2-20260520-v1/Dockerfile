FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY model_artifact ./model_artifact

ENV MODEL_DIR=/app/model_artifact
ENV PORT=8080

CMD ["python", "app.py"]
