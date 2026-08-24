import caddyConfig from "../../Caddyfile?raw";
import dockerfile from "../../Dockerfile?raw";
import nginxConfig from "../../nginx.conf?raw";
import { describe, expect, it } from "vitest";

const strictCsp = "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; manifest-src 'self'; media-src 'self'";

describe("standalone frontend server security contract", () => {
  it("pins the scanned Chainguard builder and non-root runtime", () => {
    expect(dockerfile).toContain("cgr.dev/chainguard/node:latest-dev@sha256:63476ddf30fd0f79863ee0c8e1b15841ccdf25deac29051cbf166eabd3d80e6e");
    expect(dockerfile).toContain("npm ci --ignore-scripts");
    expect(dockerfile).toContain("cgr.dev/chainguard/nginx:latest@sha256:b75e46f5101f5248c274ed1153b4fe9d9d3c25b2f4c22c0634d6c7394b25283d");
    expect(dockerfile).toContain("USER 65532:65532");
    expect(dockerfile).not.toMatch(/FROM (?:node|caddy):/);
  });

  it("serves only port 8080 with a health endpoint and SPA fallback", () => {
    expect(nginxConfig).toMatch(/listen 8080;/);
    expect(nginxConfig).toMatch(/location = \/healthz[\s\S]*return 200 "ok\\n";/);
    expect(nginxConfig).toMatch(/location \/[\s\S]*try_files \$uri \$uri\/ \/index\.html;/);
    expect(nginxConfig).toMatch(/location \^~ \/api\/[\s\S]*return 404;/);
  });

  it("keeps immutable assets separate from no-store HTML and API fallback", () => {
    expect(nginxConfig).toContain('default "no-store";');
    expect(nginxConfig).toContain('~^/assets/ "public, max-age=31536000, immutable";');
    expect(nginxConfig).toContain("add_header Cache-Control $frontend_cache_control always;");
  });

  it("applies the strict CSP and cross-origin isolation headers", () => {
    for (const config of [nginxConfig, caddyConfig]) {
      expect(config).toContain(strictCsp);
      expect(config).not.toMatch(/unsafe-inline|unsafe-eval|connect-src[^;]*wss:|img-src[^;]*https:/);
      expect(config).toMatch(/Cross-Origin-Opener-Policy[ \t]+["']?same-origin/);
      expect(config).toMatch(/Cross-Origin-Resource-Policy[ \t]+["']?same-origin/);
      expect(config).toMatch(/X-Permitted-Cross-Domain-Policies[ \t]+["']?none/);
      expect(config).toMatch(/X-Content-Type-Options[ \t]+["']?nosniff/);
    }
  });

  it("retains a hardened but explicitly deprecated Caddy compatibility file", () => {
    expect(caddyConfig).toMatch(/^# Deprecated compatibility configuration\./);
    expect(caddyConfig).toContain('header Cache-Control "no-store"');
  });
});
