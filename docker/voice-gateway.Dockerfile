FROM python:3.11-slim

WORKDIR /app

COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# FastAPI entrypoint
CMD ["uvicorn", "convonet.voice_gateway_service:app", "--host", "0.0.0.0", "--port", "8080"]
