# Image for the assistant's own services. Build context is services/, so
# nothing outside it (data, secrets, .env) can end up in the image.
FROM python:3.13-slim

WORKDIR /app
COPY requirements.txt /app/services/requirements.txt
RUN pip install --no-cache-dir -r /app/services/requirements.txt
COPY . /app/services

ENV PYTHONUNBUFFERED=1 \
    HOME_AGENT_CONFIG=/config \
    HOME_AGENT_DATA=/data

CMD ["uvicorn", "services.agent.api:app", "--host", "0.0.0.0", "--port", "8000"]
