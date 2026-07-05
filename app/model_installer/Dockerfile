# model-installer reconciler image (design Phase 3). Separate from the stdlib
# control plane: this image DOES ship boto3 + kubernetes for its scoped infra
# actions. Runs as a non-root user; no shell services.
FROM python:3.12-slim

WORKDIR /srv
COPY app/model_installer/requirements.txt /srv/app/model_installer/requirements.txt
RUN pip install --no-cache-dir -r /srv/app/model_installer/requirements.txt

COPY app/__init__.py /srv/app/__init__.py
COPY app/model_installer/ /srv/app/model_installer/

RUN useradd -u 10001 -m reconciler
USER 10001
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "-m", "app.model_installer"]
