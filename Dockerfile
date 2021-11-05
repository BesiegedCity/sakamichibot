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

WORKDIR /bot
COPY . .

CMD ["nb","run"]
