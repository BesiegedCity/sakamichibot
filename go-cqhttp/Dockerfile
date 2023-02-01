# dockerfile for build go-cqhttp with ffmpeg
FROM silicer/go-cqhttp:v1.0.0-rc4

RUN sed -i 's/dl-cdn.alpinelinux.org/mirrors.tuna.tsinghua.edu.cn/g' /etc/apk/repositories

RUN apk add --no-cache ffmpeg
