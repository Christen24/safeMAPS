FROM python:3.11-slim

WORKDIR /app

# Install system dependencies for geospatial libs, osmium CLI tool, and compilation headers for python-osmium
RUN apt-get update && apt-get install -y \
    build-essential \
    cmake \
    libgeos-dev \
    libproj-dev \
    gdal-bin \
    libosmium2-dev \
    libboost-program-options-dev \
    libboost-python-dev \
    zlib1g-dev \
    libexpat1-dev \
    osmium-tool \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .

# Install CPU-only PyTorch first to prevent downloading the huge ~2GB CUDA wheel
RUN pip install --no-cache-dir torch>=2.2.0 --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY backend/ .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
