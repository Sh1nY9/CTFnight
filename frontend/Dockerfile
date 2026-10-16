FROM cgr.dev/chainguard/node:latest-dev@sha256:e2fa61befbde3556bd8a2ea15b11791c257aee8857f6b7e56337b66c9829cdc5 AS build
WORKDIR /app
COPY --chown=65532:65532 package*.json ./
RUN npm ci --ignore-scripts
COPY --chown=65532:65532 tsconfig*.json vite.config.ts index.html ./
COPY --chown=65532:65532 src ./src
RUN npm run build

FROM cgr.dev/chainguard/nginx:latest@sha256:b75e46f5101f5248c274ed1153b4fe9d9d3c25b2f4c22c0634d6c7394b25283d AS runtime
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=65532:65532 /app/dist /usr/share/nginx/html
EXPOSE 8080
USER 65532:65532
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/usr/sbin/nginx", "-t", "-q", "-c", "/etc/nginx/nginx.conf"]
