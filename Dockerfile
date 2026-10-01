FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY engine.py chart.py bot.py ./
RUN useradd --create-home bot
USER bot
CMD ["python", "bot.py"]
