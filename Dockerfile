FROM python:3.12-slim

# Prevent Python from writing bytecode and enable unbuffered logging
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app

# Install minimal OS dependencies for cryptography, networking & certificates
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install python requirements first to leverage Docker layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Ensure tickets directories exist for runtime storage
RUN mkdir -p tickets/dev tickets/test tickets/prod

# Default container port
EXPOSE 8080

# Run the Bureau containing all 3 agents
CMD ["python", "metro_booking_agents.py"]
