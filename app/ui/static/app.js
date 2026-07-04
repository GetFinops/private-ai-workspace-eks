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
    toolsLoaded:    false,
    mode:           'chat',   // composer send mode: 'chat' | 'agent'
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
    // Shell chrome: feature rail, theme + sidebar toggles, composer mode switch.
    featureNav:    $('feature-nav'),
    themeBtn:      $('theme-btn'),
    sidebarToggle: $('sidebar-toggle'),
    topbarTitle:   $('topbar-title'),
    composerTools: $('composer-tools'),
    modeChat:      $('mode-chat'),
    modeAgent:     $('mode-agent'),
    // Tools drawer (RAG, memory, agent, media).
    toolsBtn:      $('tools-btn'),
    toolsDrawer:   $('tools-drawer'),
    toolsCloseBtn: $('tools-close-btn'),
    docFile:       $('doc-file'),
    docUploadBtn:  $('doc-upload-btn'),
    docUploadStatus: $('doc-upload-status'),
    docQuery:      $('doc-query'),
    docQueryBtn:   $('doc-query-btn'),
    docResults:    $('doc-results'),
    docEditorTitle: $('doc-editor-title'),
    docEditorBody: $('doc-editor-body'),
    docSaveBtn:    $('doc-save-btn'),
    docNewBtn:     $('doc-new-btn'),
    docInstruction: $('doc-instruction'),
    docAiBtn:      $('doc-ai-btn'),
    docEditorStatus: $('doc-editor-status'),
    docEditorList: $('doc-editor-list'),
    memText:       $('mem-text'),
    memConsent:    $('mem-consent'),
    memSaveBtn:    $('mem-save-btn'),
    memStatus:     $('mem-status'),
    memRefreshBtn: $('mem-refresh-btn'),
    memResults:    $('mem-results'),
    noteKind:      $('note-kind'),
    noteTitle:     $('note-title'),
    noteBody:      $('note-body'),
    noteAddBtn:    $('note-add-btn'),
    noteStatus:    $('note-status'),
    noteRefreshBtn: $('note-refresh-btn'),
    noteResults:   $('note-results'),
    agentTask:     $('agent-task'),
    agentWeb:      $('agent-web'),
    agentRunBtn:   $('agent-run-btn'),
    agentResearchBtn: $('agent-research-btn'),
    agentStatus:   $('agent-status'),
    agentResult:   $('agent-result'),
    mediaSttService: $('media-stt-service'),
    mediaAudio:    $('media-audio'),
    mediaTranscribeBtn: $('media-transcribe-btn'),
    mediaTranscript: $('media-transcript'),
    mediaImgService: $('media-img-service'),
    mediaPrompt:   $('media-prompt'),
    mediaGenerateBtn: $('media-generate-btn'),
    mediaStatus:   $('media-status'),
    mediaImage:    $('media-image'),
    mediaTtsService: $('media-tts-service'),
    mediaTtsText:  $('media-tts-text'),
    mediaSynthesizeBtn: $('media-synthesize-btn'),
    mediaAudioOut: $('media-audio-out'),
    comparePrompt: $('compare-prompt'),
    compareModelA: $('compare-model-a'),
    compareModelB: $('compare-model-b'),
    compareSynth:  $('compare-synth'),
    compareBtn:    $('compare-btn'),
    compareStatus: $('compare-status'),
    compareResults: $('compare-results'),
    // Draft surfaces (escalation-gated: see index.html / docs/13 §7).
    integProvider:  $('integ-provider'),
    integRefreshBtn:$('integ-refresh-btn'),
    integOperation: $('integ-operation'),
    integParams:    $('integ-params'),
    integInvokeBtn: $('integ-invoke-btn'),
    integStatus:    $('integ-status'),
    integResult:    $('integ-result'),
    mcpServer:      $('mcp-server'),
    mcpListBtn:     $('mcp-list-btn'),
    mcpTool:        $('mcp-tool'),
    mcpArgs:        $('mcp-args'),
    mcpInvokeBtn:   $('mcp-invoke-btn'),
    mcpStatus:      $('mcp-status'),
    mcpResult:      $('mcp-result'),
  };

  // ─── Utilities ───────────────────────────────────────────────────────────

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

  // ─── Persistence (server-backed; threads survive tab close / device switch) ──

  async function loadConversations() {
    try {
      var resp = await apiFetch('/v1/conversations');
      if (resp.status === 401) { redirectToLogin('Session expired. Please sign in again.'); return; }
      if (!resp.ok) { state.conversations = []; return; }
      var data = await resp.json();
      // Summaries only (no messages until a conversation is opened).
      state.conversations = (data.conversations || []).map(function (c) {
        return { id: c.id, title: c.title, messages: null };
      });
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
    // 0. Apply the saved colour theme before anything renders (avoids a flash).
    initTheme();

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
    await loadConversations();
    renderSidebar();
    await loadModels();
    renderModelSelect();
    renderUserEmail();
    attachListeners();
    setMode(state.mode);

    // The sidebar is an overlay on small screens — start it collapsed there.
    if (window.innerWidth <= 700 && els.sidebar) {
      els.sidebar.classList.add('collapsed');
      if (els.sidebarToggle) els.sidebarToggle.setAttribute('aria-expanded', 'false');
    }

    // Activate the most recent conversation (or create a blank one).
    if (state.conversations.length > 0) {
      activateConversation(state.conversations[0].id);
    } else {
      newConversation();
    }

    // 6. Notifications: real-time SSE push, with a slow poll as a backstop for
    //    the initial load and for when the stream is briefly unavailable.
    pollNotifications();
    state.notifPollTimer = setInterval(pollNotifications, NOTIF_POLL_MS);
    runNotificationStream();

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

  async function newConversation() {
    var conv = { id: genId(), title: 'New conversation', messages: [] };
    try {
      var resp = await apiFetch('/v1/conversations', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      if (resp.ok) { var c = await resp.json(); conv.id = c.id; conv.title = c.title; }
    } catch (_) { /* fall back to a local-only conversation */ }
    state.conversations.unshift(conv);
    state.activeConvId = conv.id;
    renderSidebar();
    renderMessages();
    els.chatInput.focus();
    return conv;
  }

  async function activateConversation(id) {
    state.activeConvId = id;
    renderSidebar();
    var conv = activeConv();
    // Lazy-load messages the first time a thread is opened.
    if (conv && conv.messages === null) {
      try {
        var resp = await apiFetch('/v1/conversations/' + encodeURIComponent(id));
        if (resp.status === 401) { redirectToLogin('Session expired. Please sign in again.'); return; }
        conv.messages = resp.ok ? ((await resp.json()).messages || []) : [];
      } catch (_) { conv.messages = []; }
    }
    renderMessages();
    els.chatInput.focus();
  }

  function activeConv() {
    return state.conversations.find(function (c) { return c.id === state.activeConvId; }) || null;
  }

  async function deleteConversation(id) {
    try { await apiFetch('/v1/conversations/' + encodeURIComponent(id), { method: 'DELETE' }); } catch (_) {}
    state.conversations = state.conversations.filter(function (c) { return c.id !== id; });
    if (state.activeConvId === id) {
      if (state.conversations.length > 0) { activateConversation(state.conversations[0].id); }
      else { newConversation(); }
    } else {
      renderSidebar();
    }
  }

  function appendMessage(role, content) {
    var conv = activeConv();
    if (!conv) return;
    if (conv.messages === null) conv.messages = [];
    conv.messages.push({ role: role, content: content });
    if (conv.messages.length > MAX_HISTORY) {
      conv.messages = conv.messages.slice(-MAX_HISTORY);
    }
    // Update title locally from the first user message (server seeds it too).
    if (role === 'user' && conv.title === 'New conversation') {
      conv.title = content.slice(0, 50) + (content.length > 50 ? '…' : '');
      renderSidebar();
    }
    // Persist the message server-side (best-effort; never blocks the UI).
    apiFetch('/v1/conversations/' + encodeURIComponent(conv.id) + '/messages', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role: role, content: content }),
    }).catch(function () {});
    renderMessages();
  }

  // ─── Render helpers ───────────────────────────────────────────────────────

  function renderUserEmail() {
    setText(els.userEmail, state.email || 'Signed in');
  }

  // Dynamic model list from the control plane (single source of truth, served
  // GPU-independently). Falls back silently to the config.json list on error.
  async function loadModels() {
    try {
      var resp = await fetch('/v1/models');
      if (!resp.ok) return;
      var data = await resp.json();
      if (data && Array.isArray(data.models) && data.models.length) {
        state.models = data.models;
        if (data.default) DEFAULT_MODEL = data.default;
      }
    } catch (_) {}
  }

  function renderModelSelect() {
    clearChildren(els.modelSelect);
    var models = (state.models && state.models.length ? state.models
                  : (state.config && state.config.models)) || [];
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
      var del = document.createElement('button');
      del.className = 'conv-item-delete';
      del.type = 'button';
      del.setAttribute('aria-label', 'Delete conversation');
      del.title = 'Delete conversation';
      setText(del, '×');
      del.addEventListener('click', function (e) {
        e.stopPropagation();
        deleteConversation(conv.id);
      });
      item.appendChild(del);
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
      els.messageList.appendChild(buildHeroEl());
      updateTopbarTitle();
      return;
    }

    conv.messages.forEach(function (msg) {
      els.messageList.appendChild(buildMessageEl(msg.role, msg.content));
    });

    els.messageList.scrollTop = els.messageList.scrollHeight;
    updateTopbarTitle();
  }

  // Centred welcome hero shown when a conversation has no messages. Quick-start
  // chips launch the matching feature panel. Built entirely with createElement
  // (no innerHTML) so it stays within the strict CSP.
  function buildHeroEl() {
    var empty = document.createElement('div');
    empty.id = 'empty-state';

    var mark = document.createElement('div');
    mark.className = 'hero-mark';
    mark.setAttribute('aria-hidden', 'true');
    empty.appendChild(mark);

    var h2 = document.createElement('h2');
    setText(h2, 'Private AI Workspace');
    empty.appendChild(h2);

    var tagline = document.createElement('p');
    tagline.className = 'hero-tagline';
    setText(tagline, 'Private by design — ready when you are.');
    empty.appendChild(tagline);

    var tip = document.createElement('p');
    tip.className = 'hero-tip';
    setText(tip, 'Ask a question, upload a document, run an agent, or compare models — everything stays inside your workspace.');
    empty.appendChild(tip);

    var chips = document.createElement('div');
    chips.className = 'hero-chips';
    [
      { label: 'Upload a document', feature: 'feat-docs' },
      { label: 'Run an agent',      feature: 'feat-agent' },
      { label: 'Compare models',    feature: 'feat-compare' },
      { label: 'Save a note',       feature: 'feat-notes' },
    ].forEach(function (c) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'hero-chip';
      setText(chip, c.label);
      chip.addEventListener('click', function () { openFeature(c.feature, {}, null); });
      chips.appendChild(chip);
    });
    empty.appendChild(chips);
    return empty;
  }

  function updateTopbarTitle() {
    if (!els.topbarTitle) return;
    var c = activeConv();
    setText(els.topbarTitle, (c && c.title) || 'New chat');
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

    var conv = activeConv();
    if (!conv) conv = await newConversation();
    if (!conv.messages) conv.messages = [];

    appendMessage('user', input);
    els.chatInput.value = '';
    els.chatInput.style.height = '';
    setSendingState(true);

    // Agent mode routes the message to the agent run endpoint instead of the
    // streaming chat completion, then renders the result as an assistant turn.
    if (state.mode === 'agent') { await runAgentInline(input); return; }

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

  // Run the composer input as an agent task (Agent mode). Shows a placeholder
  // assistant bubble while the run is in flight, then the final answer.
  async function runAgentInline(input) {
    var stream = buildStreamingAssistantEl();
    els.messageList.appendChild(stream.wrap);
    setText(stream.bubble, 'Working on it… (this can take a while)');
    els.messageList.scrollTop = els.messageList.scrollHeight;
    try {
      var r = await toolJson('/v1/agent/runs', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ task: input }),
      });
      stream.wrap.remove();
      if (!r.ok) {
        showError(r.data.detail || r.data.error || ('Agent failed (' + r.status + ')'));
      } else {
        var answer = r.data.answer || r.data.result || r.data.final || JSON.stringify(r.data);
        appendMessage('assistant', String(answer));
      }
    } catch (e) {
      stream.wrap.remove();
      showError('Agent error: ' + (e.message || 'unknown'));
    } finally {
      setSendingState(false);
    }
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

  function delay(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

  // Merge a single pushed notification into state (dedup / update by id).
  function mergeNotification(ev) {
    if (!ev || !ev.id) return;
    var found = false;
    state.notifications = state.notifications.map(function (n) {
      if (n.id === ev.id) { found = true; return ev; }
      return n;
    });
    if (!found) state.notifications = [ev].concat(state.notifications);
    updateNotifBadge();
  }

  // Consume one SSE connection of notification frames until it closes/errors.
  async function streamNotificationsOnce() {
    var resp = await apiFetch('/v1/notifications/stream');
    if (!resp.ok || !resp.body) return;
    var reader = resp.body.getReader();
    var decoder = new TextDecoder();
    var buf = '';
    while (true) {
      var chunk = await reader.read();
      if (chunk.done) break;
      buf += decoder.decode(chunk.value, { stream: true });
      var parts = buf.split('\n\n');
      buf = parts.pop();                       // keep the incomplete tail
      parts.forEach(function (frame) {
        var lines = frame.split('\n');
        for (var i = 0; i < lines.length; i++) {
          if (lines[i].indexOf('data:') === 0) {
            try { mergeNotification(JSON.parse(lines[i].slice(5).trim())); } catch (_) {}
          }
        }
      });
    }
  }

  // Hold one push stream open, reconnecting with a short backoff. Falls back to
  // the interval poll (already running) whenever the stream is unavailable.
  async function runNotificationStream() {
    while (state.token) {
      try { await streamNotificationsOnce(); } catch (_) {}
      if (!state.token) break;
      await delay(3000);                       // brief backoff before reconnect
    }
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

  // ─── Tools drawer: RAG, memory, agent, media ──────────────────────────────

  function openTools() {
    els.toolsDrawer.classList.add('open');
    els.toolsBtn.setAttribute('aria-expanded', 'true');
    if (!state.toolsLoaded) { state.toolsLoaded = true; loadMediaServices(); refreshMemory(); loadIntegrations(); populateCompareModels(); refreshNotes(); refreshDocs(); }
  }
  function closeTools() {
    els.toolsDrawer.classList.remove('open');
    els.toolsBtn.setAttribute('aria-expanded', 'false');
    setActiveNav(null);
  }

  // ─── Feature rail: launch a panel by its section id ────────────────────────

  // Open the Tools drawer and reveal a specific feature panel. `opts` may carry
  // pre-actions (e.g. preselect the note kind, tick the deep-research box).
  function openFeature(sectionId, opts, navBtn) {
    opts = opts || {};
    openTools();
    if (opts.noteKind && els.noteKind) els.noteKind.value = opts.noteKind;
    if (opts.agentWeb && els.agentWeb) els.agentWeb.checked = true;
    setActiveNav(navBtn || null);
    // Defer the scroll until the drawer has been laid out this frame.
    requestAnimationFrame(function () {
      var section = document.getElementById(sectionId);
      if (!section) return;
      section.scrollIntoView({ block: 'start' });
      section.classList.add('tool-section-flash');
      setTimeout(function () { section.classList.remove('tool-section-flash'); }, 1200);
      var focusable = section.querySelector('input:not([type=file]):not([type=checkbox]), textarea');
      if (focusable) { try { focusable.focus({ preventScroll: true }); } catch (_) { focusable.focus(); } }
    });
  }

  function setActiveNav(navBtn) {
    if (!els.featureNav) return;
    var items = els.featureNav.querySelectorAll('.nav-item');
    for (var i = 0; i < items.length; i++) items[i].classList.remove('active');
    if (navBtn && navBtn.classList && navBtn.classList.contains('nav-item')) {
      navBtn.classList.add('active');
    }
  }

  function handleNavClick(btn) {
    if (!btn || btn.disabled) return;
    var feature = btn.getAttribute('data-feature');
    if (!feature) return;
    openFeature(feature, {
      noteKind: btn.getAttribute('data-note-kind'),
      agentWeb: btn.getAttribute('data-agent-web') === '1',
    }, btn);
    // On mobile the sidebar is an overlay — close it once a feature is chosen.
    if (window.innerWidth <= 700 && els.sidebar) els.sidebar.classList.add('collapsed');
  }

  // ─── Composer send mode (Chat vs Agent) ────────────────────────────────────

  function setMode(mode) {
    state.mode = mode === 'agent' ? 'agent' : 'chat';
    var agent = state.mode === 'agent';
    if (els.modeAgent) {
      els.modeAgent.classList.toggle('active', agent);
      els.modeAgent.setAttribute('aria-selected', agent ? 'true' : 'false');
    }
    if (els.modeChat) {
      els.modeChat.classList.toggle('active', !agent);
      els.modeChat.setAttribute('aria-selected', agent ? 'false' : 'true');
    }
    els.chatInput.placeholder = agent ? 'Describe a task for the agent…' : 'Message Private AI…';
  }

  // ─── Theme + sidebar toggles ───────────────────────────────────────────────

  function applyTheme(theme) {
    var light = theme === 'light';
    document.documentElement.classList.toggle('theme-light', light);
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', light ? '#f6f5f3' : '#1f2229');
    if (els.themeBtn) els.themeBtn.setAttribute('aria-pressed', light ? 'true' : 'false');
  }

  function initTheme() {
    var saved = null;
    try { saved = localStorage.getItem('pai_theme'); } catch (_) {}
    applyTheme(saved === 'light' ? 'light' : 'dark');
  }

  function toggleTheme() {
    var next = document.documentElement.classList.contains('theme-light') ? 'dark' : 'light';
    applyTheme(next);
    try { localStorage.setItem('pai_theme', next); } catch (_) {}
  }

  function toggleSidebar() {
    if (!els.sidebar) return;
    var collapsed = els.sidebar.classList.toggle('collapsed');
    if (els.sidebarToggle) els.sidebarToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
  }
  function setStatus(el, msg, isErr) {
    setText(el, msg || '');
    el.classList.toggle('err', !!isErr);
  }
  async function toolJson(path, opts) {
    var resp = await apiFetch(path, opts);
    if (resp.status === 401) { redirectToLogin('Session expired. Please sign in again.'); throw new Error('401'); }
    var data = await resp.json().catch(function () { return {}; });
    return { ok: resp.ok, status: resp.status, data: data };
  }

  // Documents (RAG)
  async function uploadDocument() {
    var f = els.docFile.files && els.docFile.files[0];
    if (!f) { setStatus(els.docUploadStatus, 'Choose a file first.', true); return; }
    var isPdf = /\.pdf$/i.test(f.name) || f.type === 'application/pdf';
    setStatus(els.docUploadStatus, 'Uploading…');
    try {
      var filename = f.name, ctype = f.type || 'text/plain', bodyData = f;
      if (isPdf) {
        if (!window.pdfjsLib) {
          setStatus(els.docUploadStatus, 'PDF support is coming — upload a .txt or .md file for now.', true);
          return;
        }
        bodyData = await extractPdfText(f);          // client-side text extraction
        filename = f.name.replace(/\.pdf$/i, '.txt');
        ctype = 'text/plain';
      }
      var resp = await apiFetch('/v1/retrieval/upload?filename=' + encodeURIComponent(filename), {
        method: 'POST', headers: { 'Content-Type': ctype }, body: bodyData,
      });
      if (resp.status === 401) { redirectToLogin('Session expired.'); return; }
      var d = await resp.json().catch(function () { return {}; });
      setStatus(els.docUploadStatus, resp.ok
        ? ('Indexed “' + (d.title || filename) + '” (' + (d.chunk_count || 0) + ' chunks).')
        : (d.detail || d.error || ('Upload failed (' + resp.status + ')')), !resp.ok);
      if (resp.ok) els.docFile.value = '';
    } catch (e) { setStatus(els.docUploadStatus, 'Error: ' + (e.message || 'unknown'), true); }
  }
  async function queryDocuments() {
    var q = els.docQuery.value.trim();
    if (!q) return;
    clearChildren(els.docResults);
    try {
      var r = await toolJson('/v1/retrieval/query', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: q, top_k: 5 }),
      });
      var results = (r.data && r.data.results) || [];
      if (!r.ok) { appendResultLine(els.docResults, r.data.detail || 'Query failed', true); return; }
      if (results.length === 0) { appendResultLine(els.docResults, 'No matches.'); return; }
      results.forEach(function (res) {
        var item = document.createElement('div');
        item.className = 'tool-result-item';
        var t = document.createElement('div'); t.className = 'tool-result-title';
        setText(t, res.title || res.document_id || 'document');
        var c = document.createElement('div'); c.className = 'tool-result-snippet';
        setText(c, res.content || res.chunk || '');
        item.appendChild(t); item.appendChild(c);
        els.docResults.appendChild(item);
      });
    } catch (e) { /* 401 handled */ }
  }

  // Memory
  async function saveMemory() {
    var text = els.memText.value.trim();
    if (!text) { setStatus(els.memStatus, 'Enter something to remember.', true); return; }
    if (!els.memConsent.checked) { setStatus(els.memStatus, 'Please tick consent to store.', true); return; }
    try {
      var r = await toolJson('/v1/memory', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: text, consent: true }),
      });
      setStatus(els.memStatus, r.ok ? 'Saved.' : (r.data.detail || 'Save failed'), !r.ok);
      if (r.ok) { els.memText.value = ''; els.memConsent.checked = false; refreshMemory(); }
    } catch (e) {}
  }
  async function refreshMemory() {
    clearChildren(els.memResults);
    try {
      var r = await toolJson('/v1/memory');
      var memories = (r.data && r.data.memories) || [];
      if (memories.length === 0) { appendResultLine(els.memResults, 'No memories yet.'); return; }
      memories.forEach(function (m) {
        var item = document.createElement('div');
        item.className = 'tool-result-item';
        var c = document.createElement('div'); c.className = 'tool-result-snippet';
        setText(c, m.content || '');
        var del = document.createElement('button');
        del.className = 'tool-btn-ghost'; del.type = 'button'; setText(del, 'Delete');
        del.addEventListener('click', function () { deleteMemory(m.id, item); });
        item.appendChild(c); item.appendChild(del);
        els.memResults.appendChild(item);
      });
    } catch (e) {}
  }
  async function deleteMemory(id, itemEl) {
    try {
      var resp = await apiFetch('/v1/memory/' + encodeURIComponent(id), { method: 'DELETE' });
      if (resp.ok && itemEl) itemEl.remove();
    } catch (e) {}
  }

  // Agent
  async function runAgentTask(path, key, extra) {
    var task = els.agentTask.value.trim();
    if (!task) return;
    setStatus(els.agentStatus, 'Working… (this can take a while)');
    clearChildren(els.agentResult);
    var payload = {}; payload[key] = task;
    if (extra) { Object.keys(extra).forEach(function (k) { payload[k] = extra[k]; }); }
    try {
      var r = await toolJson(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!r.ok) { setStatus(els.agentStatus, r.data.detail || r.data.error || ('Failed (' + r.status + ')'), true); return; }
      setStatus(els.agentStatus, '');
      var answer = r.data.answer || r.data.result || r.data.final || JSON.stringify(r.data);
      renderMarkdownInto(els.agentResult, String(answer));
    } catch (e) {}
  }

  // Media
  async function loadMediaServices() {
    try {
      var r = await toolJson('/v1/media/list', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}' });
      var services = (r.data && r.data.services) || [];
      [els.mediaSttService, els.mediaImgService, els.mediaTtsService].forEach(function (sel) {
        clearChildren(sel);
        services.forEach(function (s) {
          var o = document.createElement('option'); o.value = s; setText(o, s); sel.appendChild(o);
        });
      });
    } catch (e) {}
  }
  async function transcribeAudio() {
    var f = els.mediaAudio.files && els.mediaAudio.files[0];
    if (!f) { setStatus(els.mediaStatus, 'Choose an audio file.', true); return; }
    var service = els.mediaSttService.value;
    if (!service) { setStatus(els.mediaStatus, 'No transcription service available.', true); return; }
    clearChildren(els.mediaTranscript);
    setStatus(els.mediaStatus, 'Transcribing…');
    try {
      var resp = await apiFetch('/v1/media/transcribe?service=' + encodeURIComponent(service), {
        method: 'POST', headers: { 'Content-Type': f.type || 'audio/wav' }, body: f,
      });
      if (resp.status === 401) { redirectToLogin('Session expired.'); return; }
      var d = await resp.json().catch(function () { return {}; });
      setStatus(els.mediaStatus, '');
      if (resp.ok) { appendResultLine(els.mediaTranscript, (d.result && d.result.text) || '(empty)'); }
      else { setStatus(els.mediaStatus, d.detail || d.error || 'Transcription failed', true); }
    } catch (e) { setStatus(els.mediaStatus, 'Error: ' + (e.message || 'unknown'), true); }
  }
  async function generateImage() {
    var prompt = els.mediaPrompt.value.trim();
    var service = els.mediaImgService.value;
    if (!prompt) { setStatus(els.mediaStatus, 'Enter an image prompt.', true); return; }
    if (!service) { setStatus(els.mediaStatus, 'No image service available.', true); return; }
    clearChildren(els.mediaImage);
    setStatus(els.mediaStatus, 'Generating…');
    try {
      var r = await toolJson('/v1/media/generate', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: service, prompt: prompt }),
      });
      if (!r.ok) { setStatus(els.mediaStatus, r.data.detail || r.data.error || 'Generation failed', true); return; }
      var artifactId = r.data.result && r.data.result.artifact_id;
      if (!artifactId) { setStatus(els.mediaStatus, 'No artifact returned.', true); return; }
      // Fetch the bytes through the authed same-origin proxy and render as a
      // data: URL — <img src> can't carry the bearer header, and CSP forbids
      // blob:, but allows data:.
      var cResp = await apiFetch('/v1/media/artifacts/' + encodeURIComponent(artifactId) + '/content');
      if (!cResp.ok) { setStatus(els.mediaStatus, 'Image fetch failed.', true); return; }
      var blob = await cResp.blob();
      var reader = new FileReader();
      reader.onload = function () {
        var img = document.createElement('img');
        img.className = 'tool-image';
        img.alt = 'Generated image';
        img.src = reader.result;     // data: URL
        clearChildren(els.mediaImage);
        els.mediaImage.appendChild(img);
        setStatus(els.mediaStatus, '');
      };
      reader.readAsDataURL(blob);
    } catch (e) { setStatus(els.mediaStatus, 'Error: ' + (e.message || 'unknown'), true); }
  }

  async function synthesizeSpeech() {
    var text = els.mediaTtsText.value.trim();
    var service = els.mediaTtsService.value;
    if (!text) { setStatus(els.mediaStatus, 'Enter text to speak.', true); return; }
    if (!service) { setStatus(els.mediaStatus, 'No speech service available.', true); return; }
    clearChildren(els.mediaAudioOut);
    setStatus(els.mediaStatus, 'Synthesising…');
    try {
      var r = await toolJson('/v1/media/synthesize', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service: service, text: text }),
      });
      if (!r.ok) { setStatus(els.mediaStatus, r.data.detail || r.data.error || 'Synthesis failed', true); return; }
      var artifactId = r.data.result && r.data.result.artifact_id;
      if (!artifactId) { setStatus(els.mediaStatus, 'No audio returned.', true); return; }
      // Same pattern as generated images: fetch bytes through the authed proxy,
      // render as a data: URL. <audio src> can't carry the bearer header, and
      // the data: URL is allowed by CSP `media-src 'self' data:`.
      var cResp = await apiFetch('/v1/media/artifacts/' + encodeURIComponent(artifactId) + '/content');
      if (!cResp.ok) { setStatus(els.mediaStatus, 'Audio fetch failed.', true); return; }
      var blob = await cResp.blob();
      var reader = new FileReader();
      reader.onload = function () {
        var audio = document.createElement('audio');
        audio.controls = true;
        audio.className = 'tool-audio';
        audio.src = reader.result;     // data: URL
        clearChildren(els.mediaAudioOut);
        els.mediaAudioOut.appendChild(audio);
        setStatus(els.mediaStatus, '');
      };
      reader.readAsDataURL(blob);
    } catch (e) { setStatus(els.mediaStatus, 'Error: ' + (e.message || 'unknown'), true); }
  }

  // Documents editor (writing-first + AI edit; persisted as kind="doc")
  async function saveDoc() {
    var title = els.docEditorTitle.value.trim();
    var bodyText = els.docEditorBody.value;
    if (!title) { setStatus(els.docEditorStatus, 'Give the document a title.', true); return; }
    setStatus(els.docEditorStatus, 'Saving…');
    try {
      var path = state.currentDocId ? '/v1/notes/' + encodeURIComponent(state.currentDocId) : '/v1/notes';
      var payload = state.currentDocId ? { title: title, body: bodyText } : { kind: 'doc', title: title, body: bodyText };
      var r = await toolJson(path, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      if (!r.ok && r.status !== 201) { setStatus(els.docEditorStatus, r.data.detail || r.data.error || 'Save failed', true); return; }
      if (r.data && r.data.id) state.currentDocId = r.data.id;
      setStatus(els.docEditorStatus, 'Saved.');
      refreshDocs();
    } catch (e) {}
  }
  function newDoc() {
    state.currentDocId = null;
    els.docEditorTitle.value = ''; els.docEditorBody.value = ''; els.docInstruction.value = '';
    setStatus(els.docEditorStatus, '');
  }
  function openDoc(doc) {
    state.currentDocId = doc.id;
    els.docEditorTitle.value = doc.title || '';
    els.docEditorBody.value = doc.body || '';
    setStatus(els.docEditorStatus, 'Loaded “' + (doc.title || 'document') + '”.');
  }
  async function aiEditDoc() {
    var content = els.docEditorBody.value;
    var instruction = els.docInstruction.value.trim();
    if (!content.trim()) { setStatus(els.docEditorStatus, 'Write something first.', true); return; }
    if (!instruction) { setStatus(els.docEditorStatus, 'Enter an edit instruction.', true); return; }
    setStatus(els.docEditorStatus, 'AI editing… (this can take a while)');
    try {
      var r = await toolJson('/v1/documents/edit', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: content, instruction: instruction, model: state.selectedModel || 'default' }),
      });
      if (!r.ok) { setStatus(els.docEditorStatus, r.data.detail || r.data.error || 'AI edit failed', true); return; }
      els.docEditorBody.value = r.data.result || content;   // apply the revision
      setStatus(els.docEditorStatus, 'AI edit applied — Save to keep it.');
    } catch (e) {}
  }
  async function refreshDocs() {
    clearChildren(els.docEditorList);
    try {
      var r = await toolJson('/v1/notes?kind=doc');
      var docs = (r.data && r.data.notes) || [];
      if (docs.length === 0) { appendResultLine(els.docEditorList, 'No saved documents yet.'); return; }
      docs.forEach(function (d) {
        var item = document.createElement('div');
        item.className = 'tool-result-item';
        var open = document.createElement('button');
        open.className = 'tool-btn-ghost'; open.type = 'button'; setText(open, d.title || '(untitled)');
        open.addEventListener('click', function () { openDoc(d); });
        var del = document.createElement('button');
        del.className = 'tool-btn-ghost'; del.type = 'button'; setText(del, 'Delete');
        del.addEventListener('click', function () {
          deleteNote(d.id, item);
          if (state.currentDocId === d.id) newDoc();
        });
        item.appendChild(open); item.appendChild(del);
        els.docEditorList.appendChild(item);
      });
    } catch (e) {}
  }

  // Notes & Tasks (per-user; private)
  async function createNote() {
    var title = els.noteTitle.value.trim();
    if (!title) { setStatus(els.noteStatus, 'Enter a title.', true); return; }
    try {
      var r = await toolJson('/v1/notes', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: els.noteKind.value, title: title, body: els.noteBody.value.trim() }),
      });
      if (r.status !== 201) { setStatus(els.noteStatus, r.data.detail || r.data.error || 'Add failed', true); return; }
      setStatus(els.noteStatus, '');
      els.noteTitle.value = ''; els.noteBody.value = '';
      refreshNotes();
    } catch (e) {}
  }
  async function refreshNotes() {
    clearChildren(els.noteResults);
    try {
      var r = await toolJson('/v1/notes');
      // Documents (kind="doc") live in the Editor panel, not here.
      var notes = ((r.data && r.data.notes) || []).filter(function (n) { return n.kind !== 'doc'; });
      if (notes.length === 0) { appendResultLine(els.noteResults, 'No notes or tasks yet.'); return; }
      notes.forEach(function (n) { els.noteResults.appendChild(renderNote(n)); });
    } catch (e) {}
  }
  function renderNote(n) {
    var item = document.createElement('div');
    item.className = 'tool-result-item';
    var head = document.createElement('div'); head.className = 'tool-result-title';
    if (n.kind === 'task') {
      var cb = document.createElement('input');
      cb.type = 'checkbox'; cb.checked = !!n.done; cb.setAttribute('aria-label', 'Done');
      cb.addEventListener('change', function () { toggleNote(n.id, cb.checked, head); });
      head.appendChild(cb);
    }
    var t = document.createElement('span');
    setText(t, ' ' + n.title);
    if (n.kind === 'task' && n.done) t.style.textDecoration = 'line-through';
    head.appendChild(t);
    item.appendChild(head);
    if (n.body) {
      var b = document.createElement('div'); b.className = 'tool-result-snippet';
      setText(b, n.body); item.appendChild(b);
    }
    var del = document.createElement('button');
    del.className = 'tool-btn-ghost'; del.type = 'button'; setText(del, 'Delete');
    del.addEventListener('click', function () { deleteNote(n.id, item); });
    item.appendChild(del);
    return item;
  }
  async function toggleNote(id, done, headEl) {
    try {
      var r = await toolJson('/v1/notes/' + encodeURIComponent(id), {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ done: done }),
      });
      if (r.ok) {
        var span = headEl.querySelector('span');
        if (span) span.style.textDecoration = done ? 'line-through' : 'none';
      }
    } catch (e) {}
  }
  async function deleteNote(id, itemEl) {
    try {
      var resp = await apiFetch('/v1/notes/' + encodeURIComponent(id), { method: 'DELETE' });
      if (resp.ok && itemEl) itemEl.remove();
    } catch (e) {}
  }

  // Compare (blind A/B across two models + optional synthesis)
  function populateCompareModels() {
    var models = (state.models && state.models.length ? state.models : ['default']);
    [els.compareModelA, els.compareModelB].forEach(function (sel, idx) {
      clearChildren(sel);
      models.forEach(function (m) {
        var o = document.createElement('option'); o.value = m; setText(o, m); sel.appendChild(o);
      });
      if (idx === 1 && models.length > 1) sel.value = models[1];  // default B to 2nd model
    });
  }
  async function runCompare() {
    var prompt = els.comparePrompt.value.trim();
    var a = els.compareModelA.value, b = els.compareModelB.value;
    if (!prompt) { setStatus(els.compareStatus, 'Enter a prompt.', true); return; }
    if (!a || !b) { setStatus(els.compareStatus, 'Pick two models.', true); return; }
    clearChildren(els.compareResults);
    setStatus(els.compareStatus, 'Comparing… (this can take a while)');
    try {
      var r = await toolJson('/v1/compare', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt: prompt, models: [a, b], synthesize: els.compareSynth.checked }),
      });
      if (!r.ok) { setStatus(els.compareStatus, r.data.detail || r.data.error || 'Compare failed', true); return; }
      setStatus(els.compareStatus, '');
      renderCompareResults(r.data);
    } catch (e) {}
  }
  function renderCompareResults(data) {
    clearChildren(els.compareResults);
    ((data && data.results) || []).forEach(function (res) {
      var item = document.createElement('div');
      item.className = 'tool-result-item';
      var head = document.createElement('div'); head.className = 'tool-result-title';
      setText(head, 'Answer ' + res.label);                 // blind — model hidden
      var reveal = document.createElement('button');
      reveal.className = 'tool-btn-ghost'; reveal.type = 'button'; setText(reveal, 'reveal model');
      reveal.addEventListener('click', function () {
        setText(head, 'Answer ' + res.label + ' · ' + res.model); reveal.remove();
      });
      var body = document.createElement('div'); body.className = 'tool-result-snippet';
      if (res.error) { setText(body, '(' + res.error + ')'); }
      else { renderMarkdownInto(body, String(res.content || '')); }
      item.appendChild(head); item.appendChild(reveal); item.appendChild(body);
      els.compareResults.appendChild(item);
    });
    if (data && data.synthesis) {
      var syn = document.createElement('div'); syn.className = 'tool-result-item';
      var t = document.createElement('div'); t.className = 'tool-result-title'; setText(t, 'Synthesis');
      var c = document.createElement('div'); c.className = 'tool-result-snippet';
      renderMarkdownInto(c, String(data.synthesis));
      syn.appendChild(t); syn.appendChild(c);
      els.compareResults.appendChild(syn);
    }
  }

  function appendResultLine(container, text, isErr) {
    var p = document.createElement('div');
    p.className = 'tool-result-item' + (isErr ? ' err' : '');
    setText(p, text);
    container.appendChild(p);
  }

  // Render an arbitrary JSON value into a <pre> via textContent — never
  // innerHTML, so structured tool/integration output can't inject markup.
  function renderJsonInto(container, value) {
    clearChildren(container);
    var pre = document.createElement('pre');
    pre.className = 'tool-json';
    var text;
    try { text = JSON.stringify(value, null, 2); } catch (e) { text = String(value); }
    setText(pre, text === undefined ? '(no result)' : text);
    container.appendChild(pre);
  }

  // ─── Integrations + MCP (escalation-reviewed; sign-off recorded in NOTICE) ──
  // Backend enforces deny-by-default per-tenant authz, rate limits, and the
  // AGENT_TOOLS_ENABLED / MCP_ENABLED / INTEGRATIONS_ENABLED kill-switches; the
  // UI is a content-safe caller only (JSON rendered via <pre>, never innerHTML).

  async function loadIntegrations() {
    clearChildren(els.integProvider);
    try {
      var r = await toolJson('/v1/integrations/list', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
      });
      var provs = (r.ok && r.data && r.data.integrations) || [];
      if (provs.length === 0) {
        var none = document.createElement('option');
        none.value = ''; setText(none, 'none available'); els.integProvider.appendChild(none);
        return;
      }
      provs.forEach(function (p) {
        var o = document.createElement('option'); o.value = p; setText(o, p);
        els.integProvider.appendChild(o);
      });
    } catch (e) {}
  }
  function parseJsonField(raw, statusEl, label) {
    if (!raw) return {};
    try { return JSON.parse(raw); }
    catch (e) { setStatus(statusEl, label + ' must be valid JSON.', true); return undefined; }
  }
  async function invokeIntegration() {
    var integration = els.integProvider.value;
    var operation = els.integOperation.value.trim();
    if (!integration) { setStatus(els.integStatus, 'No integration selected.', true); return; }
    if (!operation) { setStatus(els.integStatus, 'Enter an operation.', true); return; }
    var params = parseJsonField(els.integParams.value.trim(), els.integStatus, 'Params');
    if (params === undefined) return;
    clearChildren(els.integResult);
    setStatus(els.integStatus, 'Invoking…');
    try {
      var r = await toolJson('/v1/integrations/invoke', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ integration: integration, operation: operation, params: params }),
      });
      if (!r.ok) { setStatus(els.integStatus, r.data.detail || r.data.error || ('Failed (' + r.status + ')'), true); return; }
      setStatus(els.integStatus, r.data.status ? ('status: ' + r.data.status) : '');
      renderJsonInto(els.integResult, r.data.result);
    } catch (e) {}
  }

  async function mcpListTools() {
    var server = els.mcpServer.value.trim();
    if (!server) { setStatus(els.mcpStatus, 'Enter a server name.', true); return; }
    clearChildren(els.mcpTool);
    setStatus(els.mcpStatus, 'Listing…');
    try {
      var r = await toolJson('/v1/mcp/tools/list', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server: server }),
      });
      if (!r.ok) { setStatus(els.mcpStatus, r.data.detail || r.data.error || ('Failed (' + r.status + ')'), true); return; }
      var tools = (r.data && r.data.tools) || [];
      if (tools.length === 0) { setStatus(els.mcpStatus, 'No tools on this server.', true); return; }
      tools.forEach(function (t) {
        var o = document.createElement('option'); o.value = t.name || ''; setText(o, t.name || '(unnamed)');
        els.mcpTool.appendChild(o);
      });
      setStatus(els.mcpStatus, tools.length + ' tool(s).');
    } catch (e) {}
  }
  async function mcpInvoke() {
    var server = els.mcpServer.value.trim();
    var tool = els.mcpTool.value;
    if (!server) { setStatus(els.mcpStatus, 'Enter a server name.', true); return; }
    if (!tool) { setStatus(els.mcpStatus, 'List and select a tool first.', true); return; }
    var args = parseJsonField(els.mcpArgs.value.trim(), els.mcpStatus, 'Arguments');
    if (args === undefined) return;
    clearChildren(els.mcpResult);
    setStatus(els.mcpStatus, 'Invoking…');
    try {
      var r = await toolJson('/v1/mcp/invoke', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ server: server, tool: tool, arguments: args }),
      });
      if (!r.ok) { setStatus(els.mcpStatus, r.data.detail || r.data.error || ('Failed (' + r.status + ')'), true); return; }
      setStatus(els.mcpStatus, '');
      renderJsonInto(els.mcpResult, r.data.result);
    } catch (e) {}
  }

  // PDF text extraction (client-side, via the vendored pdf.js at /static/vendor/).
  // The worker is served same-origin so it stays within the strict CSP; eval is
  // disabled because the CSP has no 'unsafe-eval'. Text extraction never renders
  // glyphs, so no external cmap/standard-font fetch is needed.
  var _pdfWorkerReady = false;
  async function extractPdfText(file) {
    if (!_pdfWorkerReady) {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdf.worker.min.js';
      _pdfWorkerReady = true;
    }
    var buf = await file.arrayBuffer();
    var pdf = await window.pdfjsLib.getDocument({ data: buf, isEvalSupported: false }).promise;
    var out = [];
    for (var i = 1; i <= pdf.numPages; i++) {
      var page = await pdf.getPage(i);
      var content = await page.getTextContent();
      out.push(content.items.map(function (it) { return it.str; }).join(' '));
    }
    return out.join('\n\n');
  }

  // ─── Event listeners ─────────────────────────────────────────────────────

  function attachListeners() {
    // New conversation
    els.newChatBtn.addEventListener('click', function () {
      newConversation();
    });

    // Tools drawer
    els.toolsBtn.addEventListener('click', function () {
      if (els.toolsDrawer.classList.contains('open')) closeTools(); else openTools();
    });
    els.toolsCloseBtn.addEventListener('click', closeTools);

    // Feature rail — delegate clicks to the launcher buttons.
    if (els.featureNav) {
      els.featureNav.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('.nav-item') : null;
        if (btn) handleNavClick(btn);
      });
    }
    // Composer quick-tool buttons (search → Documents; wrench → Tools drawer).
    if (els.composerTools) {
      els.composerTools.addEventListener('click', function (e) {
        var btn = e.target.closest ? e.target.closest('.composer-tool') : null;
        if (!btn) return;
        var feature = btn.getAttribute('data-feature');
        if (feature) openFeature(feature, {}, null); else openTools();
      });
    }
    // Composer send-mode toggle.
    if (els.modeChat) els.modeChat.addEventListener('click', function () { setMode('chat'); });
    if (els.modeAgent) els.modeAgent.addEventListener('click', function () { setMode('agent'); });
    // Theme + sidebar toggles.
    if (els.themeBtn) els.themeBtn.addEventListener('click', toggleTheme);
    if (els.sidebarToggle) els.sidebarToggle.addEventListener('click', toggleSidebar);

    els.docUploadBtn.addEventListener('click', uploadDocument);
    els.docQueryBtn.addEventListener('click', queryDocuments);
    els.docQuery.addEventListener('keydown', function (e) { if (e.key === 'Enter') queryDocuments(); });
    els.memSaveBtn.addEventListener('click', saveMemory);
    els.memRefreshBtn.addEventListener('click', refreshMemory);
    els.agentRunBtn.addEventListener('click', function () { runAgentTask('/v1/agent/runs', 'task'); });
    els.agentResearchBtn.addEventListener('click', function () {
      runAgentTask('/v1/agent/research', 'question', { web: els.agentWeb.checked });
    });
    els.mediaTranscribeBtn.addEventListener('click', transcribeAudio);
    els.mediaGenerateBtn.addEventListener('click', generateImage);
    els.mediaSynthesizeBtn.addEventListener('click', synthesizeSpeech);
    els.compareBtn.addEventListener('click', runCompare);
    els.noteAddBtn.addEventListener('click', createNote);
    els.noteRefreshBtn.addEventListener('click', refreshNotes);
    els.docSaveBtn.addEventListener('click', saveDoc);
    els.docNewBtn.addEventListener('click', newDoc);
    els.docAiBtn.addEventListener('click', aiEditDoc);
    // Draft surfaces (escalation-gated).
    els.integRefreshBtn.addEventListener('click', loadIntegrations);
    els.integInvokeBtn.addEventListener('click', invokeIntegration);
    els.mcpListBtn.addEventListener('click', mcpListTools);
    els.mcpInvokeBtn.addEventListener('click', mcpInvoke);

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
