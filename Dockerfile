FROM python:3.11-slim

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app files
COPY server.py translator.py config.py index.html logo.png ./

# Create required directories
RUN mkdir -p temp_uploads watch_input watch_output watch_done

EXPOSE 10000

# Use PORT env var (Render sets this automatically)
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT:-8000}"]
