FROM node:24.12.0-alpine3.23

WORKDIR /app

RUN apk add --no-cache bash curl
RUN corepack enable

COPY package.json pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .

EXPOSE 5173

COPY start-frontend /start-frontend
RUN sed -i 's/\r$//g' /start-frontend && chmod +x /start-frontend
CMD ["/start-frontend"]
