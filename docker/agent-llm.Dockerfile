FROM python:3.11-slim
WORKDIR /app
COPY requirements*.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
# Cloud Run sets PORT=8080; use it so the container listens on the expected port
ENV PORT=8080
EXPOSE 8080
CMD uvicorn convonet.agent_llm_service:app --host 0.0.0.0 --port ${PORT} --loop asyncio
