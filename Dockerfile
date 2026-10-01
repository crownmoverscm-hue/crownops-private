FROM python:3.13-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --uid 10001 --create-home crown \
    && mkdir -p /data \
    && chown crown:crown /data
COPY --chown=crown:crown app ./app
COPY --chown=crown:crown config ./config
COPY --chown=crown:crown manage.py ./manage.py
USER crown
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import os,urllib.request; p=os.getenv('PORT','8080'); urllib.request.urlopen(f'http://127.0.0.1:{p}/api/health', timeout=4)" || exit 1
CMD ["/bin/sh","-c","exec python manage.py serve --host 0.0.0.0 --port ${PORT:-8080}"]
