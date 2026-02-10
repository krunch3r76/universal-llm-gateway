#!/bin/bash
set -e

VERSION=${1:-$(date +%Y%m%d-%H%M%S)}
REGISTRY=${REGISTRY:-ghcr.io}
IMAGE_NAME=${IMAGE_NAME:-krunch3r76/universal-llm-gateway}
FULL_IMAGE="${REGISTRY}/${IMAGE_NAME}:obfuscated-${VERSION}"
LATEST_IMAGE="${REGISTRY}/${IMAGE_NAME}:obfuscated-latest"

echo "🚀 Deploying Universal LLM Gateway (Obfuscated)"
echo "   Version: ${VERSION}"
echo "   Registry: ${REGISTRY}"
echo ""

# Step 1: Build
echo "📦 Step 1: Building obfuscated image..."
./docker/build-gpu.sh --obfuscate

# Step 2: Tag
echo "🏷️  Step 2: Tagging image..."
docker tag universal-llm-gateway:gpu ${FULL_IMAGE}
docker tag universal-llm-gateway:gpu ${LATEST_IMAGE}

# Step 3: Push
echo "📤 Step 3: Pushing to registry..."
docker push ${FULL_IMAGE}
docker push ${LATEST_IMAGE}

echo ""
echo "✅ Deployment complete!"
echo ""
echo "📋 Image: ${FULL_IMAGE}"
echo "📋 Latest: ${LATEST_IMAGE}"
