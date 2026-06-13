# Private AI Workspace — UI image
# nginx serving the vanilla-JS SPA + proxying to the control plane.
#
# Build context: repository root (for access to app/ui/)
# Usage:
#   docker build -f app/ui/Dockerfile -t private-ai-ui:latest .

FROM nginx:1.27-alpine AS runtime

# Remove default nginx content and config.
RUN rm -rf /usr/share/nginx/html/* /etc/nginx/conf.d/default.conf

# Copy static assets (built once; no bundler needed for vanilla JS).
# Fira Code woff2 fonts are vendored under static/fonts/ and copied here —
# self-hosted, no build-time download. Fira Code is licensed under the SIL
# Open Font Licence 1.1 (see NOTICE).
COPY app/ui/static/ /usr/share/nginx/html/static/

# Copy the main HTML pages.
COPY app/ui/static/index.html /usr/share/nginx/html/index.html
COPY app/ui/static/login.html /usr/share/nginx/html/login.html

# nginx config: stored as a template; entrypoint performs envsubst.
COPY app/ui/nginx.conf /etc/nginx/nginx.conf.template

# Entrypoint: writes config.json + renders nginx.conf from env, then starts nginx.
COPY app/ui/docker-entrypoint.sh /docker-entrypoint.sh
RUN chmod +x /docker-entrypoint.sh && \
    # nginx needs to write temp files as non-root.
    chown -R nginx:nginx /var/cache/nginx /var/log/nginx /etc/nginx && \
    # Allow nginx to write its PID file to /tmp (non-root).
    mkdir -p /tmp && chown nginx:nginx /tmp

# Run as non-root.
USER nginx

EXPOSE 8080

ENTRYPOINT ["/docker-entrypoint.sh"]
