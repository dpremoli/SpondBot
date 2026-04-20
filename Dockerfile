FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    SPONDBOT_DATA=/data

WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends git \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY spond ./spond
COPY webui ./webui

VOLUME ["/data"]
EXPOSE 8000

CMD ["uvicorn", "webui.app:app", "--host", "0.0.0.0", "--port", "8000"]
