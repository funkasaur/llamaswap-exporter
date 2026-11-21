FROM python:3.15.2-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY llamaswap_exporter.py .
USER nobody
EXPOSE 9105
CMD ["python", "-u", "llamaswap_exporter.py"]
