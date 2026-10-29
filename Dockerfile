# Official Playwright Python image based on Ubuntu Noble
# Contains pre-configured OS dependencies for headless Chromium
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

# Prevent python from buffering stdout/stderr and noninteractive mode
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=10000

# Install ffmpeg for audio processing and speech recognition
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependencies list first for efficient Docker layer caching
COPY requirements.txt .

# Install python dependencies without cache
RUN pip install --no-cache-dir -r requirements.txt

# Ensure Playwright Chromium browser and its system dependencies are completely downloaded
RUN playwright install chromium --with-deps

# Copy all application files (including bot.py and state.json if present)
COPY . .

# Expose Render web service port
EXPOSE 10000

# Default command to run the bot
CMD ["python", "bot.py"]
