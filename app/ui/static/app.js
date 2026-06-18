/**
 * Private AI Workspace — main application
 *
 * Vanilla JS, no framework, no build step required.
 * OIDC Authorization Code + PKCE flow for authentication.
 * Bearer token sent on every control-plane API request.
 *
 * Adapted from UI patterns in pewdiepie-archdaemon/odysseus (MIT).
 * Patterns reused: OIDC token lifecycle, chat-message render loop,
 * notification polling, local conversation history.
 * No Odysseus JS code is copied verbatim; only structural patterns and
 * the CSS custom-property system from static/style.css are adopted.
 * Provenance recorded in NOTICE under "M9 UI adaptation".
 *
 * Security model:
 *   - Token stored in sessionStorage only (cleared on tab close).
 *   - All trust decisions (auth, tenant scoping) enforced server-side.
 *   - No dangerouslySetInnerHTML equivalent: message content is set via
 *     textContent or a safe markdown renderer (marked.js if available).
 *   - CSP header enforced by nginx (see nginx.conf).
 */
(function () {
  'use strict';

  // ─── Constants ────────────────────────────────────────────────────────────

  var STORAGE_KEY_TOKEN  = 'pai_access_token';
  var STORAGE_KEY_EMAIL  = 'pai_user_email';
  var STORAGE_KEY_CONVS  = 'pai_conversations';
  var NOTIF_POLL_MS      = 30_000;   // poll for new notifications every 30 s
  var MAX_HISTORY        = 40;       // max messages to keep per conversation
  var DEFAULT_MODEL      = '';       // populated from /config.json

  // ─── State ────────────────────────────────────────────────────────────────

  var state = {
    token:          null,
    email:          '',
    config:         null,
    conversations:  [],   // [{ id, title, messages: [{role,content}] }]
    activeConvId:   null,
    models:         [],
    selectedModel:  '',
    sending:        false,
    notifOpen:      false,
    notifications:  [],   // notification objects from the API
    notifPollTimer: null,
  };

  // ─── DOM refs ─────────────────────────────────────────────────────────────

  var $ = function (id) { return document.getElementById(id); };

  var els = {
    shell:         $('app-shell'),
    sidebar:       $('sidebar'),
    convList:      $('conversation-list'),
    newChatBtn:    $('new-chat-btn'),
    userEmail:     $('user-email'),
    modelSelect:   $('model-select'),
    topbar:        $('topbar'),
    notifBtn:      $('notif-btn'),
    notifBadge:    $('notif-badge'),
    notifDrawer:   $('notif-drawer'),
    notifList:     $('notif-list'),
    notifEmpty:    $('notif-empty'),
    notifCloseBtn: $('notif-close-btn'),
    signoutBtn:    $('signout-btn'),
    errorBanner:   $('error-banner'),
    messageList:   $('message-list'),
    chatInput:     $('chat-input'),
    sendBtn:       $('send-btn'),
  };

  // ─── Utilities ───────────────────────────────────────────────────────────

  function randomBytes(n) {
    return crypto.getRandomValues(new Uint8Array(n));
  }

  function base64url(buf) {
    var bytes = new Uint8Array(buf);
    var str = '';
    bytes.forEach(function (b) { str += String.fromCharCode(b); });
    return btoa(str).replace(/\+/g, '-').replace(/\//g, '_').replace(/=/g, '');
  }

  function sha256(str) {
    return crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
  }

  function genId() {
    return Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function fmtRelTime(iso) {
    var d = new Date(iso);
    var now = Date.now();
    var secs = Math.round((now - d.getTime()) / 1000);
    if (secs < 60)  return 'just now';
    if (secs < 3600) return Math.round(secs / 60) + ' min ago';
    if (secs < 86400) return Math.round(secs / 3600) + ' h ago';
    return d.toLocaleDateString();
  }

  // Safe text node setter — prevents XSS from any source
  function setText(el, text) {
    el.textContent = String(text);
  }

  function showError(msg) {
    setText(els.errorBanner, msg);
    els.errorBanner.classList.add('visible');
    setTimeout(function () { els.errorBanner.classList.remove('visible'); }, 6000);
  }

  // ─── Persistence ─────────────────────────────────────────────────────────

  function saveConversations() {
    try {
      sessionStorage.setItem(STORAGE_KEY_CONVS, JSON.stringify(state.conversations));
    } catch (_) {}
  }

  function loadConversations() {
    try {
      var raw = sessionStorage.getItem(STORAGE_KEY_CONVS);
      if (raw) state.conversations = JSON.parse(raw);
    } catch (_) {
      state.conversations = [];
    }
  }

  // ─── API client ──────────────────────────────────────────────────────────

  function apiFetch(path, opts) {
    opts = opts || {};
    opts.headers = opts.headers || {};
    if (state.token) {
      opts.headers['Authorization'] = 'Bearer ' + state.token;
    }
    opts.headers['Content-Type'] = opts.headers['Content-Type'] || 'application/json';
    return fetch(path, opts);
  }

  // ─── OIDC / Auth ─────────────────────────────────────────────────────────

  function getToken() {
    return sessionStorage.getItem(STORAGE_KEY_TOKEN);
  }

  function setToken(token, email) {
    sessionStorage.setItem(STORAGE_KEY_TOKEN, token);
    sessionStorage.setItem(STORAGE_KEY_EMAIL, email || '');
    state.token = token;
    state.email = email || '';
  }

  function clearToken() {
    sessionStorage.removeItem(STORAGE_KEY_TOKEN);
    sessionStorage.removeItem(STORAGE_KEY_EMAIL);
    sessionStorage.removeItem('oidc_pkce_verifier');
    sessionStorage.removeItem('oidc_state');
    sessionStorage.removeItem('oidc_nonce');
    state.token = null;
    state.email = '';
  }

  function redirectToLogin(errorMsg) {
    clearToken();
    var dest = '/login.html';
    if (errorMsg) {
      dest += '?error=session_expired&error_description=' + encodeURIComponent(errorMsg);
    }
    window.location.href = dest;
  }

  // Handle OIDC callback: client-side PKCE token exchange with the OIDC
  // provider's /token endpoint.  This is the standard OAuth 2.0 Public
  // Client + PKCE flow (RFC 8252; OAuth 2.0 for Browser-Based Apps BCP)
  // and works with Cognito, Okta, Auth0, and Keycloak via CORS on the
  // token endpoint.  No control-plane auth-surface code is touched: the
  // access token returned here is verified on every subsequent API call
  // by the control-plane's OIDCTokenVerifier (see app/control_plane/
  // token_verifier.py).
  function decodeJwtPayload(jwt) {
    try {
      var parts = jwt.split('.');
      if (parts.length < 2) return null;
      var b64 = parts[1].replace(/-/g, '+').replace(/_/g, '/');
      while (b64.length % 4) b64 += '=';
      return JSON.parse(atob(b64));
    } catch (_) {
      return null;
    }
  }

  async function loadConfigEarly() {
    if (state.config) return state.config;
    try {
      var r = await fetch('/config.json');
      if (r.ok) {
        state.config = await r.json();
        return state.config;
      }
    } catch (_) {}
    return null;
  }

  async function handleCallback() {
    var params = new URLSearchParams(window.location.search);
    var code  = params.get('code');
    var state_ = params.get('state');
    var err   = params.get('error');

    if (err) {
      redirectToLogin(params.get('error_description') || err);
      return false;
    }

    if (!code) return false;

    var savedState = sessionStorage.getItem('oidc_state');
    var verifier   = sessionStorage.getItem('oidc_pkce_verifier');
    var nonce      = sessionStorage.getItem('oidc_nonce');

    if (!savedState || state_ !== savedState) {
      redirectToLogin('OAuth state mismatch — possible CSRF attempt.');
      return false;
    }
    if (!verifier) {
      redirectToLogin('Missing PKCE verifier; please sign in again.');
      return false;
    }

    var cfg = await loadConfigEarly();
    if (!cfg || !cfg.issuer || !cfg.client_id) {
      redirectToLogin('Identity provider configuration is not available.');
      return false;
    }

    var tokenEndpoint = cfg.token_endpoint
      || (cfg.issuer.replace(/\/$/, '') + '/oauth2/token');
    var redirectUri = cfg.redirect_uri || (window.location.origin + '/callback');

    var body = new URLSearchParams({
      grant_type:    'authorization_code',
      code:          code,
      redirect_uri:  redirectUri,
      client_id:     cfg.client_id,
      code_verifier: verifier,
    });

    try {
      var resp = await fetch(tokenEndpoint, {
        method:      'POST',
        headers:     { 'Content-Type': 'application/x-www-form-urlencoded' },
        body:        body.toString(),
        credentials: 'omit',
      });

      if (!resp.ok) {
        var errBody = await resp.json().catch(function () { return {}; });
        redirectToLogin(errBody.error_description || errBody.error || 'Token exchange failed.');
        return false;
      }

      var data = await resp.json();
      // The control plane verifies the OIDC token's `aud` and `email` claims.
      // For Cognito those live on the ID token (access tokens carry neither),
      // so the ID token is the bearer we send — falling back to the access
      // token only for providers that put the required claims there.
      if (!data.id_token && !data.access_token) {
        redirectToLogin('Identity provider returned no token.');
        return false;
      }

      // Nonce check on the ID token (replay defence).
      var email = '';
      if (data.id_token) {
        var idClaims = decodeJwtPayload(data.id_token);
        if (idClaims) {
          if (idClaims.nonce && idClaims.nonce !== nonce) {
            redirectToLogin('OIDC nonce mismatch — possible replay attack.');
            return false;
          }
          email = idClaims.email || idClaims.preferred_username || '';
        }
      }
      if (!email && data.access_token) {
        // Try the access token (some providers include email there).
        var atClaims = decodeJwtPayload(data.access_token);
        if (atClaims) email = atClaims.email || atClaims.username || '';
      }

      // Prefer the ID token (carries aud + email for server-side verification);
      // fall back to the access token only if no ID token was issued.
      setToken(data.id_token || data.access_token, email);

      // Clear PKCE artefacts now that the exchange has succeeded.
      sessionStorage.removeItem('oidc_pkce_verifier');
      sessionStorage.removeItem('oidc_state');
      sessionStorage.removeItem('oidc_nonce');

      // Strip OIDC params from URL without adding a history entry.
      window.history.replaceState({}, '', window.location.pathname);
      return true;
    } catch (e) {
      redirectToLogin('Network error during sign-in.');
      return false;
    }
  }

  // ─── Boot ────────────────────────────────────────────────────────────────

  async function boot() {
    // 1. Handle OIDC callback if code param is present.
    if (new URLSearchParams(window.location.search).get('code')) {
      var ok = await handleCallback();
      if (!ok) return;
    }

    // 2. Check token.
    var token = getToken();
    if (!token) {
      redirectToLogin();
      return;
    }
    state.token = token;
    state.email = sessionStorage.getItem(STORAGE_KEY_EMAIL) || '';

    // 3. Load config (handleCallback may have already loaded it).
    await loadConfigEarly();
    if (state.config) {
      DEFAULT_MODEL = state.config.default_model || '';
    }

    // 4. Validate token with a cheap API call (healthz doesn't need auth,
    //    so try the chat endpoint with a minimal preflight instead).
    var valid = await validateToken();
    if (!valid) {
      redirectToLogin('Session expired. Please sign in again.');
      return;
    }

    // 5. Render UI.
    loadConversations();
    renderSidebar();
    renderModelSelect();
    renderUserEmail();
    attachListeners();

    // Activate the most recent conversation (or create a blank one).
    if (state.conversations.length > 0) {
      activateConversation(state.conversations[0].id);
    } else {
      newConversation();
    }

    // 6. Start notification polling.
    pollNotifications();
    state.notifPollTimer = setInterval(pollNotifications, NOTIF_POLL_MS);

    // 7. Register service worker for offline support.
    if ('serviceWorker' in navigator) {
      navigator.serviceWorker.register('/static/sw.js').catch(function () {});
    }
  }

  async function validateToken() {
    try {
      // GET /v1/notifications is cheap and requires auth — use it as a probe.
      var r = await apiFetch('/v1/notifications?limit=1');
      return r.status !== 401;
    } catch (_) {
      return true;   // network error; don't force-logout
    }
  }

  // ─── Conversation management ──────────────────────────────────────────────

  function newConversation() {
    var conv = { id: genId(), title: 'New conversation', messages: [] };
    state.conversations.unshift(conv);
    saveConversations();
    activateConversation(conv.id);
    renderSidebar();
    return conv;
  }

  function activateConversation(id) {
    state.activeConvId = id;
    renderMessages();
    renderSidebar();
    els.chatInput.focus();
  }

  function activeConv() {
    return state.conversations.find(function (c) { return c.id === state.activeConvId; }) || null;
  }

  function appendMessage(role, content) {
    var conv = activeConv();
    if (!conv) return;
    conv.messages.push({ role: role, content: content });
    if (conv.messages.length > MAX_HISTORY) {
      conv.messages = conv.messages.slice(-MAX_HISTORY);
    }
    // Update conversation title from the first user message.
    if (role === 'user' && conv.title === 'New conversation') {
      conv.title = content.slice(0, 50) + (content.length > 50 ? '…' : '');
      renderSidebar();
    }
    saveConversations();
    renderMessages();
  }

  // ─── Render helpers ───────────────────────────────────────────────────────

  function renderUserEmail() {
    setText(els.userEmail, state.email || 'Signed in');
  }

  function renderModelSelect() {
    // Minimal: show a single option pulled from config (or static placeholder).
    clearChildren(els.modelSelect);
    var models = (state.config && state.config.models) || [];
    if (models.length === 0 && DEFAULT_MODEL) models = [DEFAULT_MODEL];
    if (models.length === 0) models = ['default'];
    models.forEach(function (m) {
      var opt = document.createElement('option');
      opt.value = m;
      setText(opt, m);
      els.modelSelect.appendChild(opt);
    });
    state.selectedModel = els.modelSelect.value;
  }

  function renderSidebar() {
    clearChildren(els.convList);
    state.conversations.forEach(function (conv) {
      var item = document.createElement('div');
      item.className = 'conv-item' + (conv.id === state.activeConvId ? ' active' : '');
      item.setAttribute('role', 'button');
      item.setAttribute('tabindex', '0');
      var title = document.createElement('span');
      title.className = 'conv-item-title';
      setText(title, conv.title);
      item.appendChild(title);
      item.addEventListener('click', function () { activateConversation(conv.id); });
      item.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') activateConversation(conv.id);
      });
      els.convList.appendChild(item);
    });
  }

  function clearChildren(el) {
    while (el.firstChild) el.removeChild(el.firstChild);
  }

  function renderMessages() {
    var conv = activeConv();
    clearChildren(els.messageList);

    if (!conv || conv.messages.length === 0) {
      var empty = document.createElement('div');
      empty.id = 'empty-state';
      var h2 = document.createElement('h2');
      setText(h2, 'Private AI Workspace');
      var p = document.createElement('p');
      setText(p, 'Start a conversation using the input below.');
      empty.appendChild(h2);
      empty.appendChild(p);
      els.messageList.appendChild(empty);
      return;
    }

    conv.messages.forEach(function (msg) {
      els.messageList.appendChild(buildMessageEl(msg.role, msg.content));
    });

    els.messageList.scrollTop = els.messageList.scrollHeight;
  }

  function buildMessageEl(role, content) {
    var wrap = document.createElement('div');
    wrap.className = 'msg ' + (role === 'user' ? 'user' : 'assistant');

    var avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    setText(avatar, role === 'user' ? 'U' : 'AI');

    var bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    // User text stays literal; assistant text is rendered as (safe) markdown.
    // Both paths build DOM via createElement/textContent — never innerHTML.
    if (role === 'user') {
      setText(bubble, content);
    } else {
      renderMarkdownInto(bubble, content);
    }

    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    return wrap;
  }

  // ─── Safe markdown rendering (no innerHTML, CSP-clean) ─────────────────────
  // A small, dependency-free renderer. Everything is built with
  // document.createElement + textContent, so model output can never inject HTML.

  function renderMarkdownInto(el, text) {
    clearChildren(el);
    var segments = String(text == null ? '' : text).split('```');
    segments.forEach(function (seg, i) {
      if (i % 2 === 1) {
        // Fenced code block. An optional language hint on the first line is dropped.
        var body = seg;
        var nl = seg.indexOf('\n');
        if (nl !== -1) {
          var first = seg.slice(0, nl);
          if (/^[A-Za-z0-9_+-]*$/.test(first.trim())) body = seg.slice(nl + 1);
        }
        var pre = document.createElement('pre');
        var code = document.createElement('code');
        setText(code, body.replace(/\n$/, ''));
        pre.appendChild(code);
        el.appendChild(pre);
      } else if (seg) {
        renderTextBlocks(el, seg);
      }
    });
  }

  function renderTextBlocks(el, text) {
    // Paragraphs separated by blank lines; consecutive "- "/"* " lines → a list.
    text.split(/\n{2,}/).forEach(function (block) {
      block = block.replace(/^\n+|\n+$/g, '');
      if (!block) return;
      var lines = block.split('\n');
      var isList = lines.every(function (l) { return /^\s*[-*]\s+/.test(l) || !l.trim(); });
      if (isList && lines.some(function (l) { return /^\s*[-*]\s+/.test(l); })) {
        var ul = document.createElement('ul');
        lines.forEach(function (l) {
          var m = l.match(/^\s*[-*]\s+(.*)$/);
          if (!m) return;
          var li = document.createElement('li');
          renderInline(li, m[1]);
          ul.appendChild(li);
        });
        el.appendChild(ul);
      } else {
        var p = document.createElement('p');
        lines.forEach(function (l, idx) {
          if (idx > 0) p.appendChild(document.createElement('br'));
          renderInline(p, l);
        });
        el.appendChild(p);
      }
    });
  }

  // Inline: `code`, **bold**, *italic*, [text](http-url). Tokenised left-to-right;
  // anything unmatched is emitted as a literal text node.
  function renderInline(parent, text) {
    var re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\*[^*]+\*)|(\[[^\]]+\]\((https?:\/\/[^\s)]+)\))/;
    var rest = text;
    var guard = 0;
    while (rest && guard++ < 5000) {
      var m = re.exec(rest);
      if (!m) { parent.appendChild(document.createTextNode(rest)); break; }
      if (m.index > 0) parent.appendChild(document.createTextNode(rest.slice(0, m.index)));
      var tok = m[0];
      if (tok[0] === '`') {
        var c = document.createElement('code'); setText(c, tok.slice(1, -1)); parent.appendChild(c);
      } else if (tok.slice(0, 2) === '**') {
        var b = document.createElement('strong'); setText(b, tok.slice(2, -2)); parent.appendChild(b);
      } else if (tok[0] === '*') {
        var em = document.createElement('em'); setText(em, tok.slice(1, -1)); parent.appendChild(em);
      } else {
        // [label](url) — url already constrained to http(s) by the regex.
        var lm = tok.match(/^\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)$/);
        var a = document.createElement('a');
        a.href = lm[2];
        a.target = '_blank';
        a.rel = 'noopener noreferrer';
        setText(a, lm[1]);
        parent.appendChild(a);
      }
      rest = rest.slice(m.index + tok.length);
    }
  }

  function buildStreamingAssistantEl() {
    var wrap = document.createElement('div');
    wrap.className = 'msg assistant';
    var avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    setText(avatar, 'AI');
    var bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    wrap.appendChild(avatar);
    wrap.appendChild(bubble);
    return { wrap: wrap, bubble: bubble };
  }

  // ─── Chat ─────────────────────────────────────────────────────────────────

  async function sendMessage() {
    var input = els.chatInput.value.trim();
    if (!input || state.sending) return;

    var conv = activeConv() || newConversation();

    appendMessage('user', input);
    els.chatInput.value = '';
    els.chatInput.style.height = '';
    setSendingState(true);

    // A live assistant bubble that fills in as tokens stream from /v1/chat/stream.
    var stream = buildStreamingAssistantEl();
    els.messageList.appendChild(stream.wrap);
    els.messageList.scrollTop = els.messageList.scrollHeight;
    var acc = '';

    try {
      var body = JSON.stringify({
        model: state.selectedModel || 'default',
        messages: conv.messages.map(function (m) {
          return { role: m.role, content: m.content };
        }),
        temperature: 0.2,
      });

      var resp = await apiFetch('/v1/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: body,
      });

      if (resp.status === 401) {
        stream.wrap.remove();
        redirectToLogin('Session expired. Please sign in again.');
        return;
      }

      if (!resp.ok || !resp.body) {
        stream.wrap.remove();
        var errData = await resp.json().catch(function () { return {}; });
        var retryAfter = resp.headers.get('Retry-After');
        var msg = errData.detail || ('API error ' + resp.status);
        if (retryAfter) msg += ' (retry after ' + retryAfter + ' s)';
        showError(msg);
        setSendingState(false);
        return;
      }

      // Read the Server-Sent Events stream and accumulate delta tokens.
      var reader = resp.body.getReader();
      var decoder = new TextDecoder();
      var buf = '';
      while (true) {
        var chunk = await reader.read();
        if (chunk.done) break;
        buf += decoder.decode(chunk.value, { stream: true });
        var lines = buf.split('\n');
        buf = lines.pop();   // keep the trailing partial line
        lines.forEach(function (line) {
          line = line.trim();
          if (line.indexOf('data:') !== 0) return;
          var payload = line.slice(5).trim();
          if (!payload || payload === '[DONE]') return;
          try {
            var j = JSON.parse(payload);
            var delta = ((j.choices || [])[0] || {}).delta || {};
            if (delta.content) {
              acc += delta.content;
              setText(stream.bubble, acc);     // plain text while streaming
              els.messageList.scrollTop = els.messageList.scrollHeight;
            }
          } catch (_) { /* ignore keep-alives / malformed lines */ }
        });
      }

      // Replace the live bubble with the persisted, markdown-rendered message.
      stream.wrap.remove();
      appendMessage('assistant', acc || '[No response]');
    } catch (e) {
      stream.wrap.remove();
      showError('Network error: ' + (e.message || 'unknown'));
    } finally {
      setSendingState(false);
    }
  }

  function setSendingState(sending) {
    state.sending = sending;
    els.sendBtn.disabled = sending;
    els.chatInput.disabled = sending;
  }

  // ─── Notifications ───────────────────────────────────────────────────────

  async function pollNotifications() {
    if (!state.token) return;
    try {
      var resp = await apiFetch('/v1/notifications');
      if (resp.status === 401) return;   // will be caught on next user action
      if (!resp.ok) return;
      var data = await resp.json();
      state.notifications = data.notifications || [];
      updateNotifBadge();
    } catch (_) {}
  }

  function updateNotifBadge() {
    var unread = visibleNotifications().filter(function (n) { return !n.read; });
    var count = unread.length;
    if (count > 0) {
      setText(els.notifBadge, count > 99 ? '99+' : String(count));
      els.notifBadge.classList.add('visible');
    } else {
      els.notifBadge.classList.remove('visible');
    }
    if (state.notifOpen) renderNotifList();
  }

  // Tracks notification IDs the user has dismissed from view in this
  // session.  Dismiss is a client-side gesture: the backend "read" flag
  // remains the source of truth for unread-vs-read state across sessions
  // and devices.  Dismissed items are also marked read on the backend so
  // they don't reappear in unread queries.
  var dismissedIds = new Set(
    (function () {
      try { return JSON.parse(sessionStorage.getItem('pai_dismissed_notifs') || '[]'); }
      catch (_) { return []; }
    })()
  );

  function persistDismissed() {
    try {
      sessionStorage.setItem('pai_dismissed_notifs', JSON.stringify(Array.from(dismissedIds)));
    } catch (_) {}
  }

  function visibleNotifications() {
    return state.notifications.filter(function (n) { return !dismissedIds.has(n.id); });
  }

  function renderNotifList() {
    clearChildren(els.notifList);
    var items = visibleNotifications();
    if (items.length === 0) {
      var empty = document.createElement('p');
      empty.id = 'notif-empty';
      setText(empty, 'No notifications');
      els.notifList.appendChild(empty);
      return;
    }
    items.forEach(function (n) {
      var item = document.createElement('div');
      item.className = 'notif-item ' + (n.read ? 'read' : 'unread');
      item.setAttribute('role', 'listitem');

      var cls = document.createElement('div');
      cls.className = 'notif-class';
      setText(cls, n.event_class.replace(/_/g, ' '));

      var res = document.createElement('div');
      res.className = 'notif-resource';
      setText(res, n.resource_id);

      var tm = document.createElement('div');
      tm.className = 'notif-time';
      setText(tm, fmtRelTime(n.created_at));

      var actions = document.createElement('div');
      actions.className = 'notif-actions';

      if (!n.read) {
        var markBtn = document.createElement('button');
        markBtn.className = 'notif-mark-read';
        markBtn.type = 'button';
        setText(markBtn, 'Mark read');
        markBtn.addEventListener('click', function (e) {
          e.stopPropagation();
          markNotifRead(n.id);
        });
        actions.appendChild(markBtn);
      }

      var dismissBtn = document.createElement('button');
      dismissBtn.className = 'notif-dismiss';
      dismissBtn.type = 'button';
      setText(dismissBtn, 'Dismiss');
      dismissBtn.addEventListener('click', function (e) {
        e.stopPropagation();
        dismissNotif(n.id);
      });
      actions.appendChild(dismissBtn);

      item.appendChild(cls);
      item.appendChild(res);
      item.appendChild(tm);
      item.appendChild(actions);
      els.notifList.appendChild(item);
    });
  }

  function updateLocalRead(id) {
    state.notifications = state.notifications.map(function (n) {
      return n.id === id ? Object.assign({}, n, { read: true, read_at: new Date().toISOString() }) : n;
    });
  }

  async function markNotifRead(id) {
    try {
      var resp = await apiFetch('/v1/notifications/' + encodeURIComponent(id) + '/read', {
        method: 'POST',
        body: '{}',
      });
      // 200 = newly marked read.  404 here means the notification is already
      // read (or not owned).  Either way the local state should reflect read.
      if (resp.ok || resp.status === 404) {
        updateLocalRead(id);
        updateNotifBadge();
        renderNotifList();
      }
    } catch (_) {}
  }

  async function dismissNotif(id) {
    // Hide locally first so the UI feels immediate.
    dismissedIds.add(id);
    persistDismissed();
    renderNotifList();

    // Then best-effort mark-read on the backend so the badge stays accurate
    // and the item doesn't reappear in /v1/notifications?include_read=false.
    var n = state.notifications.find(function (x) { return x.id === id; });
    if (n && !n.read) {
      try {
        await apiFetch('/v1/notifications/' + encodeURIComponent(id) + '/read', {
          method: 'POST',
          body: '{}',
        });
      } catch (_) {}
      updateLocalRead(id);
    }
    updateNotifBadge();
  }

  function openNotifDrawer() {
    state.notifOpen = true;
    els.notifDrawer.classList.add('open');
    els.notifBtn.setAttribute('aria-expanded', 'true');
    renderNotifList();
  }

  function closeNotifDrawer() {
    state.notifOpen = false;
    els.notifDrawer.classList.remove('open');
    els.notifBtn.setAttribute('aria-expanded', 'false');
  }

  // ─── Event listeners ─────────────────────────────────────────────────────

  function attachListeners() {
    // New conversation
    els.newChatBtn.addEventListener('click', function () {
      newConversation();
    });

    // Send message
    els.sendBtn.addEventListener('click', sendMessage);

    els.chatInput.addEventListener('keydown', function (e) {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    // Auto-resize textarea
    els.chatInput.addEventListener('input', function () {
      this.style.height = 'auto';
      this.style.height = Math.min(this.scrollHeight, 200) + 'px';
    });

    // Model select
    els.modelSelect.addEventListener('change', function () {
      state.selectedModel = this.value;
    });

    // Notification bell
    els.notifBtn.addEventListener('click', function () {
      if (state.notifOpen) {
        closeNotifDrawer();
      } else {
        openNotifDrawer();
      }
    });

    // Notification close button
    els.notifCloseBtn.addEventListener('click', closeNotifDrawer);

    // Sign out
    els.signoutBtn.addEventListener('click', function () {
      clearToken();
      if (state.notifPollTimer) clearInterval(state.notifPollTimer);
      var cfg = state.config;
      var issuer = cfg && cfg.issuer;
      var clientId = cfg && cfg.client_id;
      if (issuer && clientId) {
        var logoutUrl = issuer.replace(/\/$/, '') + '/logout?client_id=' + encodeURIComponent(clientId)
          + '&logout_uri=' + encodeURIComponent(window.location.origin + '/login.html');
        window.location.href = logoutUrl;
      } else {
        window.location.href = '/login.html';
      }
    });

    // Close notif drawer when clicking outside on mobile
    document.addEventListener('click', function (e) {
      if (state.notifOpen && !els.notifDrawer.contains(e.target) && e.target !== els.notifBtn) {
        closeNotifDrawer();
      }
    });
  }

  // ─── Entrypoint ───────────────────────────────────────────────────────────

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }

})();
