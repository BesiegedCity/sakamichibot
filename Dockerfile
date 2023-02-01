# syntax=docker/dockerfile:1
FROM tiangolo/uvicorn-gunicorn-fastapi:python3.9-slim

ENV LANG zh_CN.UTF-8
ENV LANGUAGE zh_CN.UTF-8
ENV LC_ALL zh_CN.UTF-8
ENV TZ Asia/Shanghai
ENV DEBIAN_FRONTEND noninteractive

RUN python3 -m pip install poetry && poetry config virtualenvs.create false

COPY ./pyproject.toml /
RUN poetry install

# Temp edit for bilibili-api-python
RUN sed -i 's/\"biz\": \"draw\"/\"biz\": \"new_dyn\"/' \
    /usr/local/lib/python3.9/site-packages/bilibili_api/dynamic.py
RUN sed -i "s/https\:\/\/api.vc.bilibili.com\/api\/v1\/drawImage\/upload/https:\/\/api.bilibili.com\/x\/dynamic\/feed\/draw\/upload_bfs/" \
    /usr/local/lib/python3.9/site-packages/bilibili_api/data/api/dynamic.json

WORKDIR /bot
COPY . .

CMD ["nb","run"]
