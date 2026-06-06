FROM python:3.12-slim

WORKDIR /workspace

COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir .

COPY app ./app

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "-m", "app.control_plane"]
