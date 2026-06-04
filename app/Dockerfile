FROM python:3.12-slim

WORKDIR /workspace

COPY app ./app
COPY pyproject.toml README.md ./

ENV PYTHONUNBUFFERED=1
EXPOSE 8080

CMD ["python", "-m", "app.control_plane"]
