FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Collect static files at build time (SECRET_KEY is a dummy, build-only value).
RUN SECRET_KEY=build-only python manage.py collectstatic --noinput

# /data holds the SQLite file and the recordings; it is mounted as a volume.
RUN useradd --create-home app && mkdir -p /data && chown app:app /data
USER app

EXPOSE 8000

# Apply migrations on start, then serve. 2 workers x 2 threads is plenty for ~8 participants.
CMD ["sh", "-c", "python manage.py migrate --noinput && exec gunicorn config.wsgi:application --bind 0.0.0.0:8000 --workers 2 --threads 2 --timeout 60"]
