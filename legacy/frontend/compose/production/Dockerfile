# Stage 1: deps
FROM node:24.12.0-alpine3.23 AS deps
WORKDIR /app
RUN corepack enable
COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# Stage 2: build
FROM node:24.12.0-alpine3.23 AS build
WORKDIR /app
RUN corepack enable
COPY --from=deps /app/node_modules ./node_modules
COPY . .

ARG VITE_API_URL
RUN echo "VITE_API_URL=${VITE_API_URL}" > .env
RUN cat .env
RUN pnpm run build -- --mode production

# Stage 3: nginx
FROM nginx:1.28.1-alpine3.23
RUN apk add --no-cache --update curl && rm -rf /var/cache/apk/*

COPY compose/production/nginx/nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=nginx:nginx /app/dist /usr/share/nginx/html

RUN chmod -R 755 /usr/share/nginx/html \
    && chown -R nginx:nginx /var/cache/nginx \
    && touch /var/run/nginx.pid \
    && chown -R nginx:nginx /var/run/nginx.pid

HEALTHCHECK --interval=30s --timeout=3s \
    CMD curl --fail http://localhost:80 || exit 1

USER nginx
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
