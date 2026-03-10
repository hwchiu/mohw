IMAGE_REPO  := hwchiu/mohw
APP_NAME    := prometheus-analyzer

# 取得當前 git commit short hash；若不在 git repo 中則使用 "dev"
COMMIT      := $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")

# 完整 image tag
IMAGE_TAG   := $(IMAGE_REPO):$(APP_NAME)-$(COMMIT)
# 同時 tag 一個 latest 方便本地使用
LATEST_TAG  := $(IMAGE_REPO):$(APP_NAME)-latest

# ── .env 處理 ──────────────────────────────────────────────────────────────────
#
# Docker 容器本身不會自動讀取主機上的 .env 檔，必須透過以下其中一種方式傳入：
#
#   方式 A（make run，推薦）：
#     Makefile 使用 --env-file .env 將 .env 整包傳進容器
#     容器內 python-dotenv 的 load_dotenv() 不會作用（容器沒有 .env 檔），
#     但環境變數已由 Docker 直接注入，os.getenv() 一樣可以讀到。
#
#   方式 B（手動 docker run）：
#     docker run --rm --env-file .env $(IMAGE_TAG) --start "..." --end "..."
#
#   方式 C（CI / K8s）：
#     透過 -e KEY=VALUE 或 Secret/ConfigMap 個別注入，不需要 .env 檔。
#
# .env 不會被打包進 image（.dockerignore 已排除），敏感資訊不會外洩。
# ──────────────────────────────────────────────────────────────────────────────
ENV_FILE := .env

.PHONY: all setup check-env build push release run run-shell clean info help

all: help

# ── 初始化 ────────────────────────────────────────────────────────────────────

## setup：從 .env.example 建立 .env（若已存在則略過）
setup:
	@if [ -f $(ENV_FILE) ]; then \
		echo ">>> $(ENV_FILE) 已存在，略過（如需重建請先執行 rm $(ENV_FILE)）"; \
	else \
		cp .env.example $(ENV_FILE); \
		echo ">>> 已建立 $(ENV_FILE)，請編輯填入實際設定值："; \
		echo "      LLM_BASE_URL  = LLM endpoint（例如 http://test.com）"; \
		echo "      LLM_MODEL_ID  = 模型名稱"; \
		echo "      PROMETHEUS_URL= Prometheus endpoint"; \
	fi

## check-env：檢查 .env 必填欄位是否已設定
check-env:
	@echo ">>> 檢查 $(ENV_FILE) 必填欄位..."
	@if [ ! -f $(ENV_FILE) ]; then \
		echo "[ERROR] $(ENV_FILE) 不存在，請先執行 make setup"; exit 1; \
	fi
	@. $(ENV_FILE); \
	MISSING=""; \
	[ -z "$$LLM_BASE_URL" ]   && MISSING="$$MISSING LLM_BASE_URL"; \
	[ -z "$$LLM_MODEL_ID" ]   && MISSING="$$MISSING LLM_MODEL_ID"; \
	[ -z "$$PROMETHEUS_URL" ] && MISSING="$$MISSING PROMETHEUS_URL"; \
	if [ -n "$$MISSING" ]; then \
		echo "[ERROR] 以下必填欄位未設定：$$MISSING"; exit 1; \
	fi
	@echo ">>> 所有必填欄位已設定 ✓"

# ── Build / Push ──────────────────────────────────────────────────────────────

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

## release：check-env + build + push（一步完成）
release: check-env build push

# ── 執行 ──────────────────────────────────────────────────────────────────────
#
# make run 會自動帶入 --env-file .env，容器內 os.getenv() 可直接讀到所有設定。
# ARGS 可傳入額外的 CLI 覆蓋參數，例如：
#
#   make run ARGS="--start '2026-03-10 14:00:00' --end '2026-03-10 15:00:00'"
#   make run ARGS="--start '2026-03-10 14:00:00' --end '2026-03-10 15:00:00' --mode c"
#
# 若要臨時覆蓋 .env 中的某個值，直接加 -e：
#   make run EXTRA="-e LLM_MODEL_ID=other-model" \
#            ARGS="--start '...' --end '...'"
# ──────────────────────────────────────────────────────────────────────────────

## run：使用 .env 設定執行分析（需傳入 ARGS，例如 make run ARGS="--start ... --end ..."）
run: check-env
	docker run --rm \
		--env-file $(ENV_FILE) \
		$(EXTRA) \
		$(LATEST_TAG) \
		$(ARGS)

## run-shell：進入容器 shell 方便除錯（帶入 .env 環境變數）
run-shell: check-env
	docker run --rm -it \
		--env-file $(ENV_FILE) \
		--entrypoint /bin/sh \
		$(LATEST_TAG)

# ── 清理 / 資訊 ───────────────────────────────────────────────────────────────

## clean：刪除本地 image
clean:
	-docker rmi $(IMAGE_TAG) $(LATEST_TAG) 2>/dev/null
	@echo ">>> Cleaned local images"

## info：顯示目前會使用的 image tag 與 .env 設定摘要
info:
	@echo "COMMIT    : $(COMMIT)"
	@echo "IMAGE_TAG : $(IMAGE_TAG)"
	@echo "LATEST_TAG: $(LATEST_TAG)"
	@echo ""
	@if [ -f $(ENV_FILE) ]; then \
		echo "$(ENV_FILE) 設定摘要（敏感值已遮罩）:"; \
		. $(ENV_FILE); \
		echo "  LLM_BASE_URL  = $${LLM_BASE_URL:-（未設定）}"; \
		echo "  LLM_MODEL_ID  = $${LLM_MODEL_ID:-（未設定）}"; \
		echo "  LLM_API_KEY   = $${LLM_API_KEY:+（已設定）}$${LLM_API_KEY:-（未設定）}"; \
		echo "  PROMETHEUS_URL= $${PROMETHEUS_URL:-（未設定）}"; \
		echo "  ANALYSIS_MODE = $${ANALYSIS_MODE:-（自動偵測）}"; \
	else \
		echo "$(ENV_FILE) 不存在，請執行 make setup"; \
	fi

help:
	@echo ""
	@echo "Usage:"
	@grep -E '^## ' Makefile | sed 's/## /  make /'
	@echo ""
	@echo "典型流程："
	@echo "  1. make setup               # 建立 .env 並填入設定"
	@echo "  2. make check-env           # 確認必填欄位已設定"
	@echo "  3. make build               # 建立 docker image"
	@echo "  4. make run ARGS=\"--start '2026-03-10 14:00:00' --end '2026-03-10 15:00:00'\""
	@echo ""
	@echo "Current image tag: $(IMAGE_TAG)"
	@echo ""
