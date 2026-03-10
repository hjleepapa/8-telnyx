FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Placeholder command - will be updated for FastAPI
CMD ["python", "app.py"]
