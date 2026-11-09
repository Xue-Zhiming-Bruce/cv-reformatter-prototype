# Frontend image: existing Vite build served by nginx with a same-origin /api
# proxy. frontend/src is unchanged; the proxy mirrors frontend/vite.config.ts.
# Build context is the repository root so one .dockerignore covers both images.
FROM node:22-alpine AS build
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM nginx:1.29-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
