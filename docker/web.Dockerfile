# syntax=docker/dockerfile:1.7
#
# The compiled React bundle, served by an unprivileged nginx on 8080. TLS and
# the public port belong to `proxy`, never to this container.

# --------------------------------------------------------------------------
# Stage 1 — build the bundle
# --------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /build

COPY web/package.json web/package-lock.json* ./
# `npm ci` when a lockfile is present, `npm install` on the very first build
# before one has been committed.
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY web/ ./
RUN npm run build

# --------------------------------------------------------------------------
# Stage 2 — serve
# --------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY docker/nginx/web.conf /etc/nginx/conf.d/default.conf
COPY --from=build /build/dist /usr/share/nginx/html

# nginx:alpine already ships an `nginx` user; run the master as it too so the
# container holds no root process at all.
RUN touch /var/run/nginx.pid \
 && chown -R nginx:nginx /var/run/nginx.pid /var/cache/nginx /usr/share/nginx/html

USER nginx

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["wget", "--quiet", "--tries=1", "--spider", "http://127.0.0.1:8080/healthz"]

CMD ["nginx", "-g", "daemon off;"]
