FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY cloud/requirements.txt cloud/requirements.txt
RUN pip install --no-cache-dir -r cloud/requirements.txt
COPY outputs/prepay-offers-service/ outputs/prepay-offers-service/
COPY outputs/retailer-offers-service/ outputs/retailer-offers-service/
COPY outputs/amazon-offers-service/ outputs/amazon-offers-service/
COPY outputs/combine_deals.py outputs/combine_deals.py
COPY outputs/public_page.html outputs/public_page.html
COPY cloud/ cloud/
# Each module's offline regression tests must pass in this image.
RUN cd outputs/prepay-offers-service && python -m unittest -q \
 && cd ../retailer-offers-service && python -m unittest -q \
 && cd ../amazon-offers-service && python -m unittest discover -p 'test_*.py' -q
ENV PYTHONUNBUFFERED=1
# Web service by default; the Cloud Run Job overrides the command with cloud/run_collect.py.
CMD exec gunicorn --chdir cloud --bind :$PORT --workers 1 --threads 8 --timeout 60 web:app
