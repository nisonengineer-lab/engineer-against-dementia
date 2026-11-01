FROM python:3.12-slim
RUN pip install --no-cache-dir flask
COPY journal_api.py demo_api.py run.py /app/
COPY demo_site/ /app/demo_site/
COPY config.py /app/config.py
COPY storage/ /app/storage/
WORKDIR /app
CMD ["python3", "/app/run.py"]
