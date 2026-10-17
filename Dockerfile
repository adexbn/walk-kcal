# walk-kcal MCP server —— stdio 传输，纯标准库，无第三方依赖
#
# 只拷运行所需的目录：
#   entry/       MCP 入口（stdio + JSON-RPC 2.0）
#   walkkcal/    内核（额度算法 / 定位 / 渲染 / 语料）
#   packs/       数据源包（麦当劳快照 / 周边餐厅 / 语料 provider）
#   corpus/      菜名 → 热量表
#   examples/    内置快照点位（无 Key 时用它们）
# 不拷：config.local.json（本机凭证）、tests/、assets/、demo/、docs/、out/
#
# 构建：docker build -t walk-kcal .
# 运行：docker run -i --rm walk-kcal        # stdin/stdout 走 MCP 协议

FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANG=C.UTF-8

WORKDIR /app

COPY entry/ ./entry/
COPY walkkcal/ ./walkkcal/
COPY packs/ ./packs/
COPY corpus/ ./corpus/
COPY examples/ ./examples/
COPY config.example.json ./

# 非 root 运行（容器内只需要读权限 + 一个可写的 /tmp）
RUN useradd -r -u 10001 -s /usr/sbin/nologin walkkcal \
 && chown -R 10001:10001 /app

USER 10001

# 没有任何网络监听：这是一个 stdio server
ENTRYPOINT ["python", "entry/mcp_server.py"]
