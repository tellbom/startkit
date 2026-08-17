FROM python:3.13

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai

WORKDIR /app

ARG PIP_INDEX_URL=https://pypi.org/simple

COPY pyproject.toml README.md ./
COPY src ./src
RUN python -m pip install --no-cache-dir --index-url "$PIP_INDEX_URL" .

COPY scripts ./scripts

CMD ["python", "scripts/push_wecom.py"]
