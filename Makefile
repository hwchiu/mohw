IMAGE_REPO  := hwchiu/mohw
APP_NAME    := prometheus-analyzer

# 取得當前 git commit short hash；若不在 git repo 中則使用 "dev"
COMMIT      := $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")

# 完整 image tag
IMAGE_TAG   := $(IMAGE_REPO):$(APP_NAME)-$(COMMIT)
# 同時 tag 一個 latest 方便本地使用
LATEST_TAG  := $(IMAGE_REPO):$(APP_NAME)-latest

.PHONY: all build push release clean help

all: help

## build：建立 docker image，tag 為當前 commit hash
build:
	@echo ">>> Building $(IMAGE_TAG)"
	docker build \
		--build-arg COMMIT=$(COMMIT) \
		-t $(IMAGE_TAG) \
		-t $(LATEST_TAG) \
		.
	@echo ">>> Built: $(IMAGE_TAG)"

## push：將 image push 到 container registry
push:
	@echo ">>> Pushing $(IMAGE_TAG)"
	docker push $(IMAGE_TAG)
	docker push $(LATEST_TAG)
	@echo ">>> Pushed: $(IMAGE_TAG)"

## release：build + push（一步完成）
release: build push

## run：本地快速執行（需傳入 ARGS，例如 make run ARGS="--prometheus http://... --start ... --end ..."）
run:
	docker run --rm $(LATEST_TAG) $(ARGS)

## clean：刪除本地 image
clean:
	-docker rmi $(IMAGE_TAG) $(LATEST_TAG) 2>/dev/null
	@echo ">>> Cleaned local images"

## info：顯示目前會使用的 image tag
info:
	@echo "COMMIT    : $(COMMIT)"
	@echo "IMAGE_TAG : $(IMAGE_TAG)"
	@echo "LATEST_TAG: $(LATEST_TAG)"

help:
	@echo ""
	@echo "Usage:"
	@grep -E '^## ' Makefile | sed 's/## /  make /'
	@echo ""
	@echo "Current image tag: $(IMAGE_TAG)"
	@echo ""
