# Use lightweight Python base image
FROM python:3.10-slim

# Install ffmpeg & dependencies
RUN apt-get update && apt-get install -y ffmpeg && apt-get clean

# Set working directory
WORKDIR /app

# Copy all files to container
COPY . .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Expose nothing (worker bot)
CMD ["python", "bot.py"]
