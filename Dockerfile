FROM python:3.12-slim

# tzdata lets the TZ env var take effect so --invite-time fires in the right
# wall-clock timezone regardless of the Unraid host setting.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install the spond library from the local source tree.
COPY pyproject.toml README.md ./
COPY spond/ ./spond/
RUN pip install --no-cache-dir .

# Copy the bot script.
COPY examples/auto_accept_bot.py ./

# -u: unbuffered stdout/stderr so `docker logs -f` streams in real time.
ENTRYPOINT ["python", "-u", "auto_accept_bot.py"]
