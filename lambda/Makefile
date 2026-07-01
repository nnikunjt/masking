# AWS ECR Deployment Makefile
# This Makefile automates the deployment of the Lambda PDF Masker to AWS ECR

# Configuration Variables
AWS_REGION := us-east-1
AWS_ACCOUNT_ID := 025066257788
ECR_REGISTRY := $(AWS_ACCOUNT_ID).dkr.ecr.$(AWS_REGION).amazonaws.com
ECR_REPOSITORY := vdb/masking
IMAGE_NAME := lambda-python311-pdf-masker
IMAGE_TAG := latest
PLATFORM := linux/amd64

# Dockerfile to use (change this if you want to use a different Dockerfile)
DOCKERFILE := Dockerfile

# Full image references
LOCAL_IMAGE := $(IMAGE_NAME):$(IMAGE_TAG)
REMOTE_IMAGE := $(ECR_REGISTRY)/$(ECR_REPOSITORY):$(IMAGE_TAG)

.PHONY: help login build tag push deploy clean test-build all

# Default target - show help
help:
	@echo "==================================="
	@echo "AWS ECR Deployment Commands"
	@echo "==================================="
	@echo ""
	@echo "Available targets:"
	@echo "  make login       - Authenticate with AWS ECR"
	@echo "  make build       - Build Docker image"
	@echo "  make tag         - Tag image for ECR"
	@echo "  make push        - Push image to ECR"
	@echo "  make deploy      - Run all steps (login, build, tag, push)"
	@echo "  make all         - Same as deploy"
	@echo "  make test-build  - Build and test locally without pushing"
	@echo "  make clean       - Remove local Docker images"
	@echo "  make info        - Show current configuration"
	@echo ""
	@echo "Configuration:"
	@echo "  AWS_REGION       = $(AWS_REGION)"
	@echo "  AWS_ACCOUNT_ID   = $(AWS_ACCOUNT_ID)"
	@echo "  ECR_REPOSITORY   = $(ECR_REPOSITORY)"
	@echo "  IMAGE_NAME       = $(IMAGE_NAME)"
	@echo "  IMAGE_TAG        = $(IMAGE_TAG)"
	@echo "  DOCKERFILE       = $(DOCKERFILE)"
	@echo ""

# Show current configuration
info:
	@echo "Current Configuration:"
	@echo "  AWS Region:      $(AWS_REGION)"
	@echo "  AWS Account:     $(AWS_ACCOUNT_ID)"
	@echo "  ECR Registry:    $(ECR_REGISTRY)"
	@echo "  ECR Repository:  $(ECR_REPOSITORY)"
	@echo "  Local Image:     $(LOCAL_IMAGE)"
	@echo "  Remote Image:    $(REMOTE_IMAGE)"
	@echo "  Platform:        $(PLATFORM)"
	@echo "  Dockerfile:      $(DOCKERFILE)"

# Login to AWS ECR
login:
	@echo "🔐 Logging in to AWS ECR..."
	@aws ecr get-login-password --region $(AWS_REGION) | \
		docker login --username AWS --password-stdin $(ECR_REGISTRY)
	@echo "✅ Successfully logged in to ECR"

# Build Docker image
build:
	@echo "🔨 Building Docker image: $(LOCAL_IMAGE)"
	@echo "   Platform: $(PLATFORM)"
	@echo "   Dockerfile: $(DOCKERFILE)"
	@docker build --platform=$(PLATFORM) -t $(LOCAL_IMAGE) -f $(DOCKERFILE) .
	@echo "✅ Build completed successfully"

# Tag image for ECR
tag:
	@echo "🏷️  Tagging image for ECR..."
	@docker tag $(LOCAL_IMAGE) $(REMOTE_IMAGE)
	@echo "✅ Tagged: $(LOCAL_IMAGE) -> $(REMOTE_IMAGE)"

# Push image to ECR
push:
	@echo "⬆️  Pushing image to ECR..."
	@docker push $(REMOTE_IMAGE)
	@echo "✅ Successfully pushed to $(REMOTE_IMAGE)"

# Full deployment pipeline
deploy: login build tag push
	@echo ""
	@echo "🎉 Deployment completed successfully!"
	@echo "   Image: $(REMOTE_IMAGE)"

# Alias for deploy
all: deploy

# Build and test locally (no push)
test-build: build
	@echo "✅ Test build completed. Image ready for local testing: $(LOCAL_IMAGE)"
	@echo "   Run with: docker run -p 9000:8080 $(LOCAL_IMAGE)"

# Clean up local Docker images
clean:
	@echo "🧹 Cleaning up Docker images..."
	-@docker rmi $(LOCAL_IMAGE) 2>/dev/null || true
	-@docker rmi $(REMOTE_IMAGE) 2>/dev/null || true
	@echo "✅ Cleanup completed"

# Build with specific Dockerfile (usage: make build-specific DOCKERFILE=Dockerfile-latest)
build-specific:
	@echo "🔨 Building with $(DOCKERFILE)..."
	@docker build --platform=$(PLATFORM) -t $(LOCAL_IMAGE) -f $(DOCKERFILE) .
	@echo "✅ Build completed"

# Quick rebuild and push (skips login)
quick-deploy: build tag push
	@echo "🎉 Quick deployment completed!"

# List local images
list-images:
	@echo "Local images:"
	@docker images | grep -E "(REPOSITORY|$(IMAGE_NAME)|$(ECR_REPOSITORY))" || echo "No matching images found"

# Build specific versions
build-latest:
	@$(MAKE) build DOCKERFILE=Dockerfile-latest

build-text:
	@$(MAKE) build DOCKERFILE=Dockerfile-for-text

# Deploy specific versions
deploy-latest:
	@$(MAKE) deploy DOCKERFILE=Dockerfile-latest IMAGE_TAG=latest

deploy-text:
	@$(MAKE) deploy DOCKERFILE=Dockerfile-for-text IMAGE_TAG=text
