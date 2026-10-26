# Official Playwright Python image based on Ubuntu Noble
# Contains all pre-configured OS dependencies for Chromium headless
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

# Prevent python from buffering stdout/stderr and running in noninteractive mode
ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive

# Set working directory
WORKDIR /app

# Copy dependencies list
COPY requirements.txt .

# Install python dependencies without cache to reduce image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy bot script
COPY bot.py .

# Default command to run the bot
CMD ["python", "bot.py"]
