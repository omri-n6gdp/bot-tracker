# Only the Python packages live in the image. The code, settings and data are mounted from
# the folder on the server (see docker-compose.yml), so an update is just a copy + restart.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HOME=/tmp
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
