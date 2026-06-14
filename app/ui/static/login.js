/*
 * Private AI Workspace — login page logic.
 * OIDC Authorization Code + PKCE flow.
 *
 * Kept in an external file (not inline in login.html) so it is served from
 * 'self' and complies with the strict Content-Security-Policy (script-src
 * 'self'; no inline scripts). Provenance recorded in NOTICE under
 * "M9 UI adaptation".
 */
(function () {
  'use strict';

  // Config is loaded from /config.json which nginx renders from Helm values.
  // Required keys: issuer, client_id, redirect_uri
  // Optional:      scope (default "openid email profile")
  var config = null;
  var btn = document.getElementById('login-btn');
  var errEl = document.getElementById('login-error');

  function showError(msg) {
    errEl.textContent = msg;
    errEl.classList.add('visible');
    btn.disabled = false;
  }

  // ── PKCE helpers ──────────────────────────────────────────────────────────
  function randomBytes(n) {
    var arr = new Uint8Array(n);
    crypto.getRandomValues(arr);
    return arr;
  }

  function base64url(buf) {
    var bytes = new Uint8Array(buf);
    var str = '';
    bytes.forEach(function (b) { str += String.fromCharCode(b); });
    return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  function sha256(str) {
    var enc = new TextEncoder().encode(str);
    return crypto.subtle.digest('SHA-256', enc);
  }

  async function generatePKCE() {
    var verifier = base64url(randomBytes(32));
    var digest = await sha256(verifier);
    var challenge = base64url(digest);
    return { verifier: verifier, challenge: challenge };
  }

  // ── OIDC redirect ─────────────────────────────────────────────────────────
  async function startLogin() {
    btn.disabled = true;
    if (!config) {
      showError('Identity provider configuration is not available. Contact your administrator.');
      return;
    }

    var pkce = await generatePKCE();
    var state = base64url(randomBytes(16));
    var nonce = base64url(randomBytes(16));

    sessionStorage.setItem('oidc_pkce_verifier', pkce.verifier);
    sessionStorage.setItem('oidc_state', state);
    sessionStorage.setItem('oidc_nonce', nonce);

    var params = new URLSearchParams({
      response_type: 'code',
      client_id: config.client_id,
      redirect_uri: config.redirect_uri || (window.location.origin + '/callback'),
      scope: config.scope || 'openid email profile',
      state: state,
      nonce: nonce,
      code_challenge: pkce.challenge,
      code_challenge_method: 'S256',
    });

    // Authorize endpoint: prefer explicit config.authorize_endpoint, else
    // fall back to the Cognito/standard-OIDC default of /oauth2/authorize.
    // Keycloak operators must set authorize_endpoint explicitly.
    var authorizeEndpoint = config.authorize_endpoint
      || (config.issuer.replace(/\/$/, '') + '/oauth2/authorize');

    window.location.href = authorizeEndpoint + '?' + params.toString();
  }

  btn.addEventListener('click', startLogin);

  // ── Load config ───────────────────────────────────────────────────────────
  fetch('/config.json')
    .then(function (r) {
      if (!r.ok) throw new Error('HTTP ' + r.status);
      return r.json();
    })
    .then(function (cfg) {
      config = cfg;
      // Surface OIDC errors forwarded from the callback redirect.
      var params = new URLSearchParams(window.location.search);
      var err = params.get('error');
      if (err) {
        showError('Sign-in failed: ' + (params.get('error_description') || err));
      }
    })
    .catch(function (e) {
      showError('Failed to load identity provider configuration (' + e.message + ').');
    });
})();
