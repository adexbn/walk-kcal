#!/bin/sh
# 容器 / 裸机通用入口。
#
# 为什么要有这个文件：
#   Glama 之类的地方会用 debian:trixie-slim 作基础镜像，再由它自己装 Python。
#   不同的装法下，PATH 里可能只有 `python`，也可能只有 `python3`。
#   直接写死其中一个，换一个环境就报 "not found"，而报错信息指向的其实是启动命令，
#   不是代码 —— 排查起来很容易走错方向。
#
#   `-u` 是 unbuffered：stdio 传输下 stdout 是管道，宁可不要缓冲。
#   （`entry/mcp_server.py` 自己每条消息后也 flush 了，这里是双保险。）
set -e

cd "$(dirname "$0")/.."

if command -v python >/dev/null 2>&1; then
    exec python -u entry/mcp_server.py
fi
exec python3 -u entry/mcp_server.py
