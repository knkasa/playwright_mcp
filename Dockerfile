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

# Pin Playwright browser install location explicitly
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Install @playwright/mcp globally and verify the install path
RUN npm install -g @playwright/mcp && \
    npm root -g && \
    ls -la $(npm root -g)/@playwright/mcp/

# Cache bust to force fresh Chromium install
ARG CACHEBUST=1

# Install browser using playwright-mcp's own installer
# (the MCP server requires the "chrome-for-testing" channel specifically,
# even when --browser chromium is passed at runtime)
RUN npx --yes @playwright/mcp install-browser chrome-for-testing && \
    ls -la /ms-playwright

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
