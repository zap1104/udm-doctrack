web: gunicorn config.wsgi:application --bind 0.0.0.0:$PORT --workers 3 --timeout 120
worker: python manage.py qcluster
release: python manage.py migrate --noinput && python manage.py init_db
