# Base image with Python 3.11
FROM python:3.11-slim

# Install system dependencies for Chromium + Node.js
RUN apt-get update && apt-get install -y \
    curl \
    gnupg \
    # Chromium dependencies
    libnss3 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libxkbcommon0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libpango-1.0-0 \
    libpangocairo-1.0-0 \
    libnspr4 \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 20 LTS
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install @playwright/mcp globally
RUN npm install -g @playwright/mcp

# Install Chromium browser for Playwright MCP
RUN node /usr/local/lib/node_modules/@playwright/mcp/node_modules/.bin/playwright.js install chromium \
    || npx playwright install chromium

# Set working directory
WORKDIR /app

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY app.py .

# Expose Gradio port
EXPOSE 7860

# Run app
CMD ["python", "app.py"]