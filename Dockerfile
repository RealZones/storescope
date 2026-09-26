FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 DATA_DIR=/data
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY dragtrack ./dragtrack
VOLUME ["/data"]
EXPOSE 8787
HEALTHCHECK --interval=60s --timeout=10s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=5).status==200 else 1)" || exit 1
CMD ["python", "-m", "dragtrack"]
