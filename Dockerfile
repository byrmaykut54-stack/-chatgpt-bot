FROM python:3.12-slim
WORKDIR /app
COPY . .
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "python3 business_assistant.py"]
