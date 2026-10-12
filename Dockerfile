# Deprecated/unmaintained deployment asset; native Pi setup: docs/raspberry-pi.md.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md constraints.txt ./
COPY src ./src
ARG EXTRAS=gmail
RUN pip install --no-cache-dir -c constraints.txt ".[${EXTRAS}]" \
    && groupadd --gid 10001 sky \
    && useradd --uid 10001 --gid sky --no-create-home sky \
    && mkdir -p /data /config /secrets \
    && chown sky:sky /data /secrets
USER 10001:10001
EXPOSE 8008
VOLUME ["/data"]
CMD ["python", "-m", "all_sky_alert", "--config", "/config/config.toml", "serve"]
