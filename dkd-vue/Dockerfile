# syntax=docker/dockerfile:1
# ---------------------------------------------------------------------------
# dkd-web 镜像（管理前端：Vue 3.4 + Element Plus + Vite 5）
#
# 两阶段：node 里 `npm run build:prod` 出静态站 → nginx 只放静态文件。
# nginx 的站点配置**不打进镜像**，由 deploy/docker/docker-compose.yml 挂载
#   deploy/docker/nginx/default.conf（改反代/超时不需重新构建镜像）。
# 用法：
#   docker build -t dkd/dkd-web:local --build-arg NPM_REGISTRY=https://registry.npmmirror.com dkd-vue
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build

# 国内网络加速（留空用官方源）
ARG NPM_REGISTRY
WORKDIR /src

# 先只拷依赖清单：lock 不变时这一层命中缓存，CI/重复部署不必重装依赖
COPY package.json package-lock.json ./
RUN if [ -n "$NPM_REGISTRY" ]; then npm config set registry "$NPM_REGISTRY"; fi \
 && npm ci --no-audit --no-fund

COPY . .
# build:prod = vite build（读 .env.production：VITE_APP_BASE_API=/prod-api、VITE_BUILD_COMPRESS=gzip）
# → 产物里带 .gz，nginx 侧必须开 gzip_static（见 nginx/default.conf），否则等于白压一遍
RUN npm run build:prod

# ---------- 运行期 ----------
FROM nginx:1.27-alpine

ENV TZ=Asia/Shanghai
COPY --from=build /src/dist /usr/share/nginx/html

EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1
