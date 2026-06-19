FROM python:3.11-slim
WORKDIR /app
COPY requirements-api.txt requirements.txt ./
# Install CPU-only torch first to avoid 2.5GB+ CUDA variants (Fly.io has no GPU)
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
RUN pip install --no-cache-dir -r requirements-api.txt -r requirements.txt && \
    pip uninstall -y opencv-python && \
    pip install --no-cache-dir opencv-python-headless
COPY . .
# Pre-download CLIP weights so cold starts don't need to fetch 338 MB
RUN python -c "import clip; clip.load('ViT-B/32', device='cpu')"
# For local docker run only — shadowed by Fly.io volume mount in production.
# If this COPY fails, run: bash scripts/predeploy.sh
# deploy_snapshot/ is gitignored; it must be created locally before building.
COPY deploy_snapshot/embeddings /app/data/embeddings
EXPOSE 8080
CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8080"]
