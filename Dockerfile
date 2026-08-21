FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0 \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_BROWSER_GATHER_USAGE_STATS=false \
    STREAMLIT_CLIENT_SHOW_ERROR_DETAILS=false \
    ZERODHA_CLOUD_MODE=true \
    ZERODHA_DATA_DIR=/mnt/zerodha

WORKDIR /app

COPY requirements.txt ./
RUN apt-get update \
    && apt-get install --no-install-recommends --yes gosu \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY src ./src
COPY deploy/docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN useradd --create-home --uid 10001 dashboard \
    && mkdir -p /mnt/zerodha \
    && chown -R dashboard:dashboard /app /mnt/zerodha \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=3)"

ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["streamlit", "run", "src/aadithya_quantlab/zerodha_live_trading/app.py"]
