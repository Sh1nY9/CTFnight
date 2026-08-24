FROM cgr.dev/chainguard/go:latest-dev@sha256:017584bf9b817a44d8c8a9eb1ce1472753053e7accc6adfbfa81d4b077a27338 AS builder

# This audited post-v2.11.4 snapshot contains the pending v2.11.5 fixes and
# dependency upgrades. A full commit ID plus Go's checksum database makes the
# source input immutable and verifiable.
ARG CADDY_COMMIT=0cf03d32f7d99cf160d5375e8a40fbe3d910d515
ENV CGO_ENABLED=0 \
    GOBIN=/out \
    GOTOOLCHAIN=local

RUN module_dir="$(go list -m -f '{{.Dir}}' "github.com/caddyserver/caddy/v2@${CADDY_COMMIT}")" && \
    if (cd "$module_dir" && go list -deps ./cmd/caddy) | grep -Fx 'golang.org/x/crypto/openpgp'; then \
      echo 'refusing to build: unmaintained openpgp package became reachable' >&2; \
      exit 1; \
    fi && \
    go install \
      -trimpath \
      -ldflags='-s -w -buildid=' \
      "github.com/caddyserver/caddy/v2/cmd/caddy@${CADDY_COMMIT}"

FROM cgr.dev/chainguard/static:latest@sha256:f68e3a8244c7d0f4cd56635aaff8e6a533cf6cc3850d8fb339567a5782d6a0b0 AS runtime

ENV XDG_DATA_HOME=/data \
    XDG_CONFIG_HOME=/tmp/caddy-config

COPY --from=builder /out/caddy /usr/bin/caddy
COPY --chown=65532:65532 data/.keep /data/.keep

USER 65532:65532
EXPOSE 80 443 443/udp
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["/usr/bin/caddy", "version"]
ENTRYPOINT ["/usr/bin/caddy"]
CMD ["run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]
