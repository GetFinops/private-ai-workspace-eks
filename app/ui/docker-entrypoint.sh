#!/bin/sh
# docker-entrypoint.sh — renders /config.json from environment variables
# and starts nginx.
#
# Required environment variables (injected via Helm ConfigMap):
#   OIDC_ISSUER             — OIDC provider issuer URL
#   OIDC_CLIENT_ID          — OIDC client ID for this app (public client / PKCE)
#   OIDC_REDIRECT_URI       — Redirect URI registered with the OIDC provider
#   CONTROL_PLANE_HOST      — DNS name of the control-plane Kubernetes service
#   CONTROL_PLANE_PORT      — Port of the control-plane service (default 8080)
#
# Optional:
#   OIDC_SCOPE              — OAuth scopes (default: openid email profile)
#   OIDC_AUTHORIZE_ENDPOINT — Override the authorize endpoint URL
#                             (default: {issuer}/oauth2/authorize — Cognito style)
#   OIDC_TOKEN_ENDPOINT     — Override the token endpoint URL
#                             (default: {issuer}/oauth2/token — Cognito style)
#   DEFAULT_MODEL           — Default model name shown in the UI
#   MODELS                  — Comma-separated list of model names
set -eu

CONTROL_PLANE_HOST="${CONTROL_PLANE_HOST:-control-plane}"
CONTROL_PLANE_PORT="${CONTROL_PLANE_PORT:-8080}"
OIDC_ISSUER="${OIDC_ISSUER:-}"
OIDC_CLIENT_ID="${OIDC_CLIENT_ID:-}"
OIDC_REDIRECT_URI="${OIDC_REDIRECT_URI:-}"
OIDC_SCOPE="${OIDC_SCOPE:-openid email profile}"
OIDC_AUTHORIZE_ENDPOINT="${OIDC_AUTHORIZE_ENDPOINT:-}"
OIDC_TOKEN_ENDPOINT="${OIDC_TOKEN_ENDPOINT:-}"
DEFAULT_MODEL="${DEFAULT_MODEL:-}"
MODELS="${MODELS:-}"

# Write /config.json for the SPA to consume.
cat > /usr/share/nginx/html/config.json <<EOF
{
  "issuer":              "${OIDC_ISSUER}",
  "client_id":           "${OIDC_CLIENT_ID}",
  "redirect_uri":        "${OIDC_REDIRECT_URI}",
  "scope":               "${OIDC_SCOPE}",
  "authorize_endpoint":  "${OIDC_AUTHORIZE_ENDPOINT}",
  "token_endpoint":      "${OIDC_TOKEN_ENDPOINT}",
  "default_model":       "${DEFAULT_MODEL}",
  "models":              [$(echo "${MODELS}" | sed 's/,/","/g' | sed 's/^/"/;s/$/"/' | sed 's/""//' )]
}
EOF

# Derive the CSP connect-src/form-action allowlist from the OIDC endpoints.
# Browsers compare CSP entries by origin, so we extract scheme://host[:port]
# from each configured URL.  Empty when no OIDC endpoint is configured —
# keeps the CSP strict for fresh installs.
extract_origin() {
    # Strip everything after the host (path/query/fragment) but keep
    # scheme://host[:port].  No-op when input is empty.
    echo "$1" | sed -nE 's,^(https?://[^/]+).*$,\1,p'
}

CONNECT_SRC_OIDC=""
for url in "${OIDC_ISSUER}" "${OIDC_AUTHORIZE_ENDPOINT}" "${OIDC_TOKEN_ENDPOINT}"; do
    origin=$(extract_origin "$url")
    if [ -n "$origin" ] && ! echo "$CONNECT_SRC_OIDC" | grep -qw "$origin"; then
        CONNECT_SRC_OIDC="${CONNECT_SRC_OIDC} ${origin}"
    fi
done
CONNECT_SRC_OIDC=$(echo "$CONNECT_SRC_OIDC" | sed 's/^[[:space:]]*//')
export CONNECT_SRC_OIDC

# Substitute env vars into nginx.conf.
envsubst '${CONTROL_PLANE_HOST} ${CONTROL_PLANE_PORT} ${CONNECT_SRC_OIDC}' \
  < /etc/nginx/nginx.conf.template \
  > /etc/nginx/nginx.conf

exec nginx -g 'daemon off;'
