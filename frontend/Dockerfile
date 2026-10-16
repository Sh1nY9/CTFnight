FROM cgr.dev/chainguard/node:latest-dev@sha256:63476ddf30fd0f79863ee0c8e1b15841ccdf25deac29051cbf166eabd3d80e6e AS build
WORKDIR /app
COPY --chown=65532:65532 package*.json ./
RUN npm ci --ignore-scripts
COPY --chown=65532:65532 tsconfig*.json vite.config.ts index.html ./
COPY --chown=65532:65532 src ./src
RUN npm run build

FROM cgr.dev/chainguard/nginx:latest@sha256:df0a97604163fb49366d0853c34b238cde40122606f3c92940d47717689a0473 AS runtime
COPY nginx.conf /etc/nginx/nginx.conf
COPY --from=build --chown=65532:65532 /app/dist /usr/share/nginx/html
EXPOSE 8080
USER 65532:65532
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/usr/sbin/nginx", "-t", "-q", "-c", "/etc/nginx/nginx.conf"]
