FROM node:24.19.0-alpine AS builder

RUN corepack enable && corepack prepare pnpm@11.20.0 --activate

WORKDIR /app

COPY package.json pnpm-lock.yaml pnpm-workspace.yaml .npmrc ./
COPY apps/web/package.json ./apps/web/package.json
RUN pnpm install --frozen-lockfile --filter @ai-platform/web...

COPY apps/web ./apps/web
RUN pnpm --filter @ai-platform/web build

FROM nginx:1.29.1-alpine

COPY infra/nginx/default.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/apps/web/dist /usr/share/nginx/html

EXPOSE 8080
