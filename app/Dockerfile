FROM python:3.12-slim
WORKDIR /srv
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY cinema/ cinema/
EXPOSE 8888
CMD ["uvicorn", "--factory", "cinema.main:build", "--host", "0.0.0.0", "--port", "8888"]
