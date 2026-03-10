# ── Stage 1: build dependencies ──────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# 只複製依賴清單，充分利用 layer cache
COPY requirements.txt .

RUN pip install --upgrade pip \
 && pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: runtime ──────────────────────────────────────────────────────────
FROM python:3.11-slim

LABEL org.opencontainers.image.source="https://github.com/hwchiu/mohw"
LABEL org.opencontainers.image.description="Prometheus intelligent anomaly analyzer powered by local LLM"

# 建立非 root 使用者
RUN addgroup --system analyzer && adduser --system --ingroup analyzer analyzer

WORKDIR /app

# 從 builder 複製已安裝的套件
COPY --from=builder /install /usr/local

# 複製應用程式原始碼
COPY *.py ./

USER analyzer

ENTRYPOINT ["python", "__main__.py"]
CMD ["--help"]
