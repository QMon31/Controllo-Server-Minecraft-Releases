// Global fetch interceptor: aggiunge X-Admin-Token e, su 401, chiede il PIN UNA sola volta per tutte le richieste in attesa
const _origFetch = window.fetch.bind(window);
let _loginPromise = null;

// Valore proposto in un campo (es. motivo del ban) nella lingua del pannello: i campi non vengono tradotti
function trDefault(value) {
  return (typeof value === 'string' && window.i18n) ? window.i18n.t(value) : value;
}

function ensureAdminLogin() {
  if (_loginPromise) return _loginPromise;
  _loginPromise = (async () => {
    try {
      const pin = prompt(t('msg.inserisci_pin_amministratore_pannello'));
      if (!pin) return false;
      const r = await _origFetch('/api/auth/verify', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ pin: pin.trim() })
      });
      if (!r.ok) {
        let detail = '';
        try { detail = (await r.json()).detail || ''; } catch (e) {}
        if (r.status === 403 && detail.includes('PIN predefinito')) {
          alert('' + detail);  // accesso da internet bloccato finché sul PC Host il PIN è 1234
        } else if (typeof showToast === 'function') {
          // 429: il PC dice quanto manca alla fine del blocco (prima il messaggio diceva sempre "10 minuti")
          showToast(r.status === 429 ? (detail || t('actions.troppi_pin_errati_riprova_piu')) : t('actions.pin_errato_o_autorizzato'), 'error');
        }
        return false;
      }
      const authData = await r.json();
      if (!authData || !authData.token) return false;
      localStorage.setItem('mc_admin_token', authData.token);
      return true;
    } catch (e) {
      return false;
    } finally {
      // permette un nuovo tentativo alla prossima richiesta rifiutata
      setTimeout(() => { _loginPromise = null; }, 0);
    }
  })();
  return _loginPromise;
}

window.fetch = async (input, init = {}) => {
  const url = typeof input === 'string' ? input : (input && input.url ? input.url : '');
  const isApi = url.startsWith('/api/') && !url.startsWith('/api/auth/');
  const withToken = () => {
    if (!isApi) return init;
    const opts = Object.assign({}, init);
    opts.headers = new Headers(init.headers || {});
    const tok = localStorage.getItem('mc_admin_token');
    if (tok) opts.headers.set('X-Admin-Token', tok);
    return opts;
  };
  let res = await _origFetch(input, withToken());
  if (res.status === 401 && isApi) {
    localStorage.removeItem('mc_admin_token');
    if (await ensureAdminLogin()) {
      res = await _origFetch(input, withToken());
    }
  }
  if (res.status === 403 && isApi && !window._defaultPinBlockShown) {
    // Accesso da internet bloccato perché sul PC Host il PIN è ancora 1234: lo si spiega una volta sola
    res.clone().json().then(d => {
      if (d && d.detail && d.detail.includes('PIN predefinito') && !window._defaultPinBlockShown) {
        window._defaultPinBlockShown = true;
        alert('' + d.detail);
      }
    }).catch(() => {});
  }
  return res;
};

function escapeJsString(str) {
  if (!str) return '';
  return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '\\"');
}

function escapeJsAttr(str) {
  return escapeHtml(escapeJsString(str));
}

// Minecraft Server Control Center Frontend Logic v3.0 (Rust Native Edition)

let ws = null;
let currentStatus = null;
let currentServerState = 'STOPPED';
let currentBackupData = null;
let commandHistory = [];
let historyIndex = -1;
let metricsHistory = [];
let networkInfo = null;
// Console senza WebSocket: numero dell'ultima riga ricevuta e ora dell'ultimo messaggio dal WebSocket
let lastLogSeq = 0;
let lastWsMessageAt = 0;
let serverStartTimeUnix = null;
let scheduledRemainingSeconds = null;
let scheduledTotalSeconds = 14400;
let scheduledTimerActive = false;
let unreadChatCount = 0;

function formatSecondsToUptime(seconds) {
  if (!seconds || seconds < 0) seconds = 0;
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  const pad = (n) => String(n).padStart(2, '0');
  if (h > 0) {
    return `${pad(h)}h ${pad(m)}m ${pad(s)}s`;
  }
  return `${pad(m)}m ${pad(s)}s`;
}

// Live 1-second interval ticker for smooth second-by-second uptime & scheduled restart countdown
setInterval(() => {
  if (currentStatus && (currentStatus.state === 'ONLINE' || currentStatus.online) && serverStartTimeUnix) {
    const elapsed = Math.max(0, Math.floor((Date.now() / 1000) - serverStartTimeUnix));
    const formatted = formatSecondsToUptime(elapsed);
    updateText('uptime-display', formatted);
    updateText('subbar-uptime-display', formatted);
  }

  // Smooth live ticking for scheduled restart countdown
  if (scheduledTimerActive && scheduledRemainingSeconds !== null && scheduledRemainingSeconds > 0) {
    scheduledRemainingSeconds--;
    const h = Math.floor(scheduledRemainingSeconds / 3600);
    const m = Math.floor((scheduledRemainingSeconds % 3600) / 60);
    const s = scheduledRemainingSeconds % 60;
    const pad = (n) => String(n).padStart(2, '0');
    const timeFormatted = `${pad(h)}h ${pad(m)}m ${pad(s)}s`;
    updateText('restart-countdown', timeFormatted);
    const progBar = document.getElementById('restart-progress-bar');
    if (progBar && scheduledTotalSeconds > 0) {
      const pct = Math.max(0, Math.min(100, (1 - (scheduledRemainingSeconds / scheduledTotalSeconds)) * 100));
      progBar.style.width = `${pct.toFixed(1)}%`;
    }
  }
}, 1000);

// Fast DOM Memoization to minimize reflows, CPU and battery consumption
function updateText(id, newText) {
  const elements = document.querySelectorAll(`[id="${id}"]`);
  if (elements && elements.length > 0) {
    const s = String(newText !== undefined && newText !== null ? newText : '');
    elements.forEach(el => {
      if (el.textContent !== s) {
        el.textContent = s;
      }
    });
  }
}

function updateWidth(id, pct) {
  const el = document.getElementById(id);
  if (el) {
    const w = `${Math.min(100, Math.max(0, pct))}%`;
    if (el.style.width !== w) {
      el.style.width = w;
    }
  }
}

// =========================================================
// MODAL SCROLL LOCK & OVERLAY MANAGEMENT
// =========================================================
function syncModalScrollLock() {
  const backdrops = document.querySelectorAll('.modal-backdrop');
  let isAnyModalVisible = false;
  backdrops.forEach(m => {
    if (m.style.display && m.style.display !== 'none') {
      isAnyModalVisible = true;
    }
  });

  if (isAnyModalVisible) {
    document.body.classList.add('modal-open');
    document.documentElement.classList.add('modal-open');
  } else {
    document.body.classList.remove('modal-open');
    document.documentElement.classList.remove('modal-open');
  }
}

function openModal(modalId) {
  if (modalId === 'modal-forge-installer' && !isLocalHostClient()) {
    showToast(t('msg.creazione_installazione_nuovo_server_disponibile'), 'warning');
    return;
  }
  const el = document.getElementById(modalId);
  if (el) {
    el.style.display = 'flex';
    syncModalScrollLock();
    updateHostOnlyVisibility();
  }
}

function closeModal(modalId) {
  const el = document.getElementById(modalId);
  if (el) {
    el.style.display = 'none';
    syncModalScrollLock();
  }
}

// =========================================================
// DROPDOWN MENUS & INTERACTION HANDLERS
// =========================================================
function toggleToolsDropdown(e) {
  if (e) e.stopPropagation();
  const menu = document.getElementById('tools-dropdown-menu');
  if (!menu) return;
  const isShown = menu.style.display !== 'none';
  closeAllDropdowns();
  if (!isShown) {
    menu.style.display = 'flex';
  }
}

function closeAllDropdowns() {
  const toolsMenu = document.getElementById('tools-dropdown-menu');
  if (toolsMenu) toolsMenu.style.display = 'none';
  const instMenu = document.getElementById('instance-dropdown-menu');
  if (instMenu) instMenu.style.display = 'none';
}

window.addEventListener('click', (e) => {
  if (!e.target.closest('.dropdown-popover') && !e.target.closest('.dropdown-btn') && !e.target.closest('#btn-instance-selector') && !e.target.closest('.btn-instance-pill')) {
    closeAllDropdowns();
  }
});

function toggleLiveChart() {
  const box = document.getElementById('collapsible-chart-box');
  const arrow = document.getElementById('chart-toggle-arrow');
  if (!box) return;
  const isHidden = box.style.display === 'none';
  if (isHidden) {
    box.style.display = 'block';
    if (arrow) arrow.textContent = '▴';
    localStorage.setItem('mc_live_chart_visible', 'true');
    resizeCanvas();
    drawChart();
  } else {
    box.style.display = 'none';
    if (arrow) arrow.textContent = '▾';
    localStorage.setItem('mc_live_chart_visible', 'false');
  }
}

async function openBackupFolderOnPc() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  try {
    const res = await fetch('/api/backup/open_folder', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      if (typeof showToast === 'function') {
        showToast(t('actions.cartella_backup_aperta_esplora_file'), 'success');
      }
    } else {
      showToast(`${data.detail || t('msg.impossibile_aprire_cartella_backup')}`, 'error');
    }
  } catch (err) {
    showToast(t('msg.errore_connessione', err.message), 'error');
  }
}

function isLocalHostClient() {
  const h = (window.location.hostname || '').toLowerCase();
  return h === 'localhost' || h === '127.0.0.1' || h === '::1';
}

function updateHostOnlyVisibility(isLocal) {
  // If the browser URL is remote (public IP from internet, mobile IP, LAN IP from another machine), it can NEVER be host client!
  const isHostUrl = isLocalHostClient();
  const local = isHostUrl && (typeof isLocal === 'boolean' ? isLocal : true);
  if (!local) {
    document.body.classList.add('is-remote-client');
  } else {
    document.body.classList.remove('is-remote-client');
  }
  document.querySelectorAll('.host-only-item').forEach(el => {
    // Non resettare il display per i backdrop delle modali: la loro visibilità
    // è gestita esclusivamente da openModal / closeModal. Resettare style.display
    // sul PC host causerebbe la comparsa indesiderata della modale sopra le altre.
    if (el.classList.contains('modal-backdrop')) {
      if (!local) el.style.display = 'none';
      return;
    }
    el.style.display = local ? '' : 'none';
  });
}

function showToast(message, type = 'info') {
  let container = document.getElementById('global-toast-container');
  if (!container) {
    container = document.createElement('div');
    container.id = 'global-toast-container';
    container.className = 'global-toast-container';
    document.body.appendChild(container);
  }
  const toast = document.createElement('div');
  toast.className = `global-toast toast-${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => {
    toast.classList.add('fade-out');
    setTimeout(() => { try { toast.remove(); } catch(e){} }, 400);
  }, 3500);
}

async function checkPcUpdate(force = false) {
  try {
    const res = await fetch(force ? '/api/pc-update/check' : '/api/pc-update/status', { method: force ? 'POST' : 'GET' });
    if (!res.ok) return;
    const st = await res.json();
    renderPcUpdateBanner(st);
    if (force && st.error) showToast((t('msg.controllo_aggiornamenti_riuscito') + " ") + st.error, 'error');
    else if (force && !st.available) showToast(t('msg.programma_aggiornato_vx_build', st.current_version, st.current_build), 'success');
  } catch (e) {}
}

function renderPcUpdateBanner(st) {
  let bar = document.getElementById('pc-update-banner');
  if (!st || !st.available) {
    if (bar) bar.remove();
    return;
  }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'pc-update-banner';
    bar.style.cssText = 'position:sticky;top:0;z-index:900;display:flex;gap:12px;align-items:center;justify-content:center;' +
      'flex-wrap:wrap;padding:10px 16px;background:#0c4a6e;color:#e0f2fe;font-weight:600;font-size:0.9rem;';
    document.body.prepend(bar);
  }
  const label = t('msg.nuova_versione_programma_pc_vx', escapeHtml(String(st.latest_version)), escapeHtml(String(st.latest_build)));
  if (st.downloading) {
    bar.innerHTML = t('msg.download', label, st.progress);
  } else if (isLocalHostClient()) {
    bar.innerHTML = `${label} <button class="btn btn-xs btn-emerald" onclick="installPcUpdate()">${th('msg.aggiorna_ora')}</button>`;
  } else {
    bar.innerHTML = `${label} <span style="font-weight:400;">${th('msg.installare_sul_pc_host')}</span>`;
  }
}

function renderDefaultPinBanner(isDefault) {
  let bar = document.getElementById('default-pin-banner');
  if (!isDefault) {
    if (bar) bar.remove();
    return;
  }
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'default-pin-banner';
    bar.style.cssText = 'position:sticky;top:0;z-index:901;display:flex;gap:12px;align-items:center;justify-content:center;' +
      'flex-wrap:wrap;padding:10px 16px;background:#7c2d12;color:#ffedd5;font-weight:600;font-size:0.9rem;';
    document.body.prepend(bar);
  }
  bar.innerHTML = (t('msg.pin_predefinito_1234_l_accesso') + " ") +
    `<button class="btn btn-xs btn-emerald" onclick="openSettingsModal()">${th('msg.cambia_pin')}</button>`;
}

async function installPcUpdate() {
  if (!confirm((t('msg.scaricare_installare_l_aggiornamento_programma') + " ") +
               t('msg.pannello_si_chiudera_durante_l'))) return;
  const res = await fetch('/api/pc-update/install', { method: 'POST' });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    showToast('' + (data.detail || t('msg.aggiornamento_avviato')), 'error');
    return;
  }
  showToast(t('msg.download_dell_aggiornamento_corso'), 'info');
  const timer = setInterval(async () => {
    let s = null;
    try { s = await (await fetch('/api/pc-update/status')).json(); } catch (e) { return; } // pannello in chiusura
    renderPcUpdateBanner(s);
    if (!s.downloading) {
      clearInterval(timer);
      if (s.error) showToast('' + s.error, 'error');
      else showToast(t('msg.installer_avviato_completa_finestra_installazion'), 'success');
    }
  }, 1500);
}

async function openServerFolderOnPc() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  if (window.chrome && window.chrome.webview) {
    try {
      window.chrome.webview.postMessage(JSON.stringify({
        action: 'open_folder',
        folder_type: 'server'
      }));
      showToast(t('msg.cartella_server_aperta_esplora_file'), 'success');
      return;
    } catch (e) {
      console.warn('Errore postMessage open_folder server:', e);
    }
  }

  try {
    const res = await fetch('/api/server/open_folder', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(t('msg.cartella_server_aperta_esplora_file'), 'success');
    } else {
      showToast(`${data.detail || data.message || t('msg.impossibile_aprire_cartella')}`, 'error');
    }
  } catch (e) {
    showToast((t('msg.errore_connessione_2') + " ") + e.message, 'error');
  }
}

// Canvas chart context
const canvas = document.getElementById('resourceCanvas');
const ctx = canvas ? canvas.getContext('2d') : null;

// Initialize on DOM loaded
document.addEventListener('DOMContentLoaded', () => {
  updateHostOnlyVisibility(isLocalHostClient());
  initWebSocket();
  fetch('/api/config').then(r => r.ok ? r.json() : null).then(cfg => {
    if (!cfg) return;
    renderDefaultPinBanner(cfg.admin_pin_is_default);
    if (!cfg.server_dir) {
      showToast(t('msg.primo_avvio_scegli_cartella_server'), 'info');
      openSettingsModal();
    }
  }).catch(() => {});
  checkPcUpdate(false);
  setInterval(() => checkPcUpdate(false), 30 * 60 * 1000);
  setupCommandHistory();
  const modSearch = document.getElementById('mods-search-input');
  if (modSearch) {
    modSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        clearModSearch();
      }
    });
  }
  // Global Escape key listener to close active modals
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const crashModal = document.getElementById('modal-crash-view');
      if (crashModal && crashModal.style.display !== 'none') {
        closeCrashViewModal();
        return;
      }
      const openModals = document.querySelectorAll('.modal-backdrop');
      for (let m of openModals) {
        if (m.style.display && m.style.display !== 'none') {
          m.style.display = 'none';
          syncModalScrollLock();
          break;
        }
      }
    }
  });
  initCanvas();
  fetchInitialNetworkInfo();
  fetchErrors();
  fetchChatHistory();
  loadInstancesList(false);
  const senderInput = document.getElementById('chat-sender-name');
  if (senderInput) {
    try {
      const savedSender = localStorage.getItem('mc_chat_sender');
      if (savedSender) senderInput.value = savedSender;
      senderInput.addEventListener('input', () => {
        try { localStorage.setItem('mc_chat_sender', senderInput.value.trim() || 'Host'); } catch(e){}
      });
    } catch(e){}
  }

  window.addEventListener('resize', resizeCanvas);
  document.addEventListener('visibilitychange', () => {
    if (!document.hidden) drawChart();
  });
  resizeCanvas();

  // Restore active tab if previously selected (default to home)
  let savedTab = localStorage.getItem('mc_active_tab') || 'home';
  if (savedTab === 'tab-map') savedTab = 'home';
  if (savedTab === 'home') {
    goHome();
  } else {
    selectSidebarNav(savedTab);
  }
  renderCommands();
  initModsDropzone();
  loadModsList();

  // Check if a backup is currently active on server startup/refresh
  fetch('/api/backup/status')
    .then(r => r.json())
    .then(b => {
      renderBackupStatus(b);
      if (b && b.active) startBackupPolling();
    })
    .catch(() => {});

  // Setup modal touch/wheel containment & scroll lock listeners
  document.querySelectorAll('.modal-backdrop').forEach(backdrop => {
    backdrop.addEventListener('wheel', (e) => {
      const scrollable = e.target.closest('.modal-body, .terminal-box, .chat-messages-container');
      if (!scrollable) {
        e.preventDefault();
      }
    }, { passive: false });

    backdrop.addEventListener('touchmove', (e) => {
      const scrollable = e.target.closest('.modal-body, .terminal-box, .chat-messages-container');
      if (!scrollable) {
        e.preventDefault();
      }
    }, { passive: false });
  });

  const modalObserver = new MutationObserver(() => {
    syncModalScrollLock();
  });
  document.querySelectorAll('.modal-backdrop').forEach(m => {
    modalObserver.observe(m, { attributes: true, attributeFilter: ['style', 'class'] });
  });

  // Polling fallback every 3s in case WS fails
  setInterval(() => {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      fetchStatusHttp();
    }
  }, 3000);

  // Console senza WebSocket (non si apre, o resta aperto ma non riceve più niente): prima la console restava
  // ferma e ciò che si scriveva compariva solo riavviando il programma
  setInterval(() => {
    const open = ws && ws.readyState === WebSocket.OPEN;
    if (open && lastWsMessageAt && Date.now() - lastWsMessageAt > 8000) {
      // Lo stato arriva ogni 2 s: 8 s di silenzio vogliono dire collegamento bloccato, lo si riapre
      console.warn('[WS] Nessun messaggio da 8 s: riconnessione.');
      lastWsMessageAt = 0;
      try { ws.close(); } catch (e) {}
    }
    if (!open || !lastWsMessageAt) {
      pollConsoleLogs();
      pollChatIfVisible();
    }
  }, 2000);

  // Background idle check for backup (every 2.5s) to detect backups launched from other devices
  setInterval(() => {
    if (!backupPollingTimer) {
      fetch('/api/backup/status')
        .then(r => r.json())
        .then(b => {
          if (b && b.active) {
            renderBackupStatus(b);
            startBackupPolling();
          }
        })
        .catch(() => {});
    }
  }, 2500);
});

// =========================================================
// WEBSOCKET & NETWORK
// =========================================================
function initWebSocket() {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
  const tok = localStorage.getItem('mc_admin_token');
  const wsUrl = `${protocol}//${window.location.host}/ws` + (tok ? `?token=${encodeURIComponent(tok)}` : '');

  ws = new WebSocket(wsUrl);

  ws.onopen = () => {
    console.log('[WS] Connesso al server di controllo.');
  };

  ws.onmessage = (event) => {
    lastWsMessageAt = Date.now();
    try {
      const msg = JSON.parse(event.data);
      if (msg.type === 'init') {
        renderFullStatus(msg.data);
        if (msg.logs && Array.isArray(msg.logs)) {
          const terminal = document.getElementById('console-terminal');
          terminal.innerHTML = '';
          cachedConsoleLogs = [];
          msg.logs.forEach(appendLogLine);
          lastLogSeq = msg.log_seq || 0;
        }
        if (msg.chat && Array.isArray(msg.chat)) {
          const chatBox = document.getElementById('chat-stream-box');
          chatBox.innerHTML = '';
          msg.chat.forEach(renderChatMessage);
        }
        if (msg.errors && Array.isArray(msg.errors)) {
          renderErrorsFeed(msg.errors);
        }
        if (msg.crash_reports && Array.isArray(msg.crash_reports)) {
          renderCrashReportsList(msg.crash_reports);
        }
        if (msg.network) {
          updateNetworkInfo(msg.network);
        }
        if (msg.remote) {
          renderRemoteStatus(msg.remote);
        }
        if (msg.backup) {
          renderBackupStatus(msg.backup);
        }
      } else if (msg.type === 'status') {
        renderFullStatus(msg.data);
      } else if (msg.type === 'remote') {
        renderRemoteStatus(msg.data);
      } else if (msg.type === 'network') {
        // Porta o router cambiati: l'IP da copiare si aggiorna subito (prima solo riavviando il programma)
        updateNetworkInfo(msg.data);
      } else if (msg.type === 'backup') {
        renderBackupStatus(msg.data);
      } else if (msg.type === 'toast_error') {
        if (typeof showToast === 'function') {
          showToast(msg.message || t('msg.errore'), 'error');
        } else {
          alert(msg.message || t('msg.errore'));
        }
      } else if (msg.type === 'toast_info') {
        if (typeof showToast === 'function') {
          showToast(msg.message, 'success');
        }
      } else if (msg.type === 'versions_updated') {
        handleVersionsUpdated(msg.data);
      } else if (msg.type === 'mod_updates_detected') {
        handleModUpdatesDetected(msg.data);
      } else if (msg.type === 'log') {
        // Già mostrata dalla lettura senza WebSocket: non la si ripete
        if (msg.seq && msg.seq <= lastLogSeq) return;
        if (msg.seq) lastLogSeq = msg.seq;
        appendLogLine(msg.data !== undefined ? msg.data : msg.line);
      } else if (msg.type === 'chat') {
        renderChatMessage(msg.data);
      } else if (msg.type === 'clear_chat') {
        clearChatUi();
      } else if (msg.type === 'whitelist_status') {
        if (typeof msg.enabled === 'boolean') {
          updateWhitelistPill(msg.enabled);
        }
      } else if (msg.type === 'error' || msg.type === 'error_item') {
        appendErrorItem(msg.data);
      } else if (msg.type === 'clear_errors') {
        clearErrorsUi();
      }
    } catch (e) {
      console.error('[WS] Errore parsing messaggio:', e);
    }
  };

  ws.onclose = (event) => {
    if (event && event.code === 4401) {
      ensureAdminLogin().then(ok => { if (ok) setTimeout(initWebSocket, 300); });
      return;
    }
    console.warn('[WS] Connessione chiusa. Riconnessione tra 2.5 secondi...');
    setTimeout(initWebSocket, 2500);
  };

  ws.onerror = (err) => {
    console.error('[WS] Errore socket:', err);
  };
}

async function fetchStatusHttp() {
  try {
    const res = await fetch('/api/status');
    if (res.ok) {
      const data = await res.json();
      renderFullStatus(data);
    }
  } catch (e) {}
}

async function fetchInitialNetworkInfo() {
  try {
    const res = await fetch('/api/network');
    if (res.ok) {
      networkInfo = await res.json();
      updateNetworkInfo(networkInfo);
    }
  } catch (e) {}

  try {
    const resRemote = await fetch('/api/remote/status');
    if (resRemote.ok) {
      renderRemoteStatus(await resRemote.json());
    }
  } catch (e) {}
}

let currentRemote = { active: false, url: null };

function updateNetworkInfo(net) {
  networkInfo = net;
  
  // Minecraft Server IPs & UPnP Direct Connection
  const mcPort = net.minecraft ? net.minecraft.port : (net.port || 32769);
  const mcPub = net.minecraft ? net.minecraft.public_address : (net.public_ip ? `${net.public_ip}:${mcPort}` : '--');
  const mcLan = net.minecraft ? net.minecraft.lan_address : (net.local_ip ? `${net.local_ip}:${mcPort}` : '--');
  // Stato reale dell'apertura automatica della porta sul router (prima era sempre mostrato come attivo)
  const upnp = net.upnp || (net.minecraft && net.minecraft.upnp) || {};
  const upnpOk = !!upnp.active;
  const mcLocal = net.minecraft ? net.minecraft.local_address : `localhost:${mcPort}`;

  const mcStaticBadge = document.getElementById('subbar-mc-static-badge');

  const headerIpEl = document.getElementById('header-ip-display');
  if (headerIpEl) {
    headerIpEl.textContent = mcPub;
  }
  if (mcStaticBadge) {
    mcStaticBadge.textContent = upnpOk ? t('serverbar.connessione_diretta_upnp') : t('serverbar.porta_aperta');
    mcStaticBadge.style.color = upnpOk ? '#9ccc65' : '#e3b35a';
    mcStaticBadge.title = upnp.message || upnp.last_error || (upnpOk ? '' : t('serverbar.router_ha_aperto_porta_automatico'));
  }
  // Update elements in mtab-mc modal
  const modeActiveBadge = document.getElementById('mc-mode-active-badge');
  if (modeActiveBadge) {
    modeActiveBadge.textContent = upnpOk ? t('ui.upnp_attivo') : t('msg.upnp_attivo');
    modeActiveBadge.className = upnpOk ? 'badge badge-emerald' : 'badge badge-secondary';
  }
  const mcPublicFull = document.getElementById('mc-public-ip-full');
  if (mcPublicFull) mcPublicFull.textContent = mcPub;

  const upnpMappedPort = document.getElementById('upnp-mapped-port');
  // Se il router non concede la porta del server se ne apre un'altra che porta al server: si mostrano entrambe
  const extPort = upnp.external_port || mcPort;
  if (upnpMappedPort) upnpMappedPort.textContent = extPort !== mcPort ? `${extPort} → ${mcPort} TCP` : `${mcPort} TCP`;

  const upnpExtIp = document.getElementById('upnp-external-ip');
  if (upnpExtIp) upnpExtIp.textContent = net.public_ip || '--';

  const mcLanDisplay = document.getElementById('mc-lan-addr-display');
  if (mcLanDisplay) mcLanDisplay.textContent = mcLan;


  // Porta del server configurato (da server.properties); senza server non si mostra una porta predefinita
  const portDisplayEl = document.getElementById('subbar-server-port-display');
  const hasServer = (currentInstancesData.instances || []).length > 0;
  if (portDisplayEl) portDisplayEl.textContent = hasServer ? mcPort : '--';

  // Web LAN URLs
  if (net.lan_url) {
    const qrModalUrl = document.getElementById('qr-modal-url');
    if (qrModalUrl) qrModalUrl.textContent = net.lan_url;
  }
  const lanIpEl = document.getElementById('lan-ip-display');
  if (lanIpEl && (net.local_ip || net.lan_url)) {
    lanIpEl.textContent = net.local_ip || (net.lan_url ? net.lan_url.replace('http://', '').split(':')[0] : '--');
  }
  const lanPortEl = document.getElementById('lan-port-display');
  if (lanPortEl) {
    lanPortEl.textContent = `${net.web_port || 8080} TCP`;
  }

  // QR Code Local
  const localQr = net.qr_code || net.lan_qr || (net.apk && net.apk.lan_qr) || '';
  if (localQr) {
    const qrLanImg = document.getElementById('qr-lan-img');
    if (qrLanImg) qrLanImg.src = localQr;
  }

  // Accesso da internet, se presente nei dati di rete
  if (net.remote) {
    renderRemoteStatus(net.remote);
  }

  // UPnP status if provided in network payload
  if (net.upnp) {
    renderUPnPStatus(net.upnp);
  }
}

// =========================================================
// ACCESSO DA INTERNET (HTTPS SULL'IP PUBBLICO, PORTA APERTA CON UPNP)
// =========================================================
function renderRemoteStatus(remote) {
  if (!remote) return;
  currentRemote = remote;

  const modalBadge = document.getElementById('remote-status-badge');
  const portPill = document.getElementById('remote-port-pill');
  const inactiveView = document.getElementById('remote-inactive-view');
  const inactiveText = document.getElementById('remote-inactive-text');
  const activeView = document.getElementById('remote-active-view');
  const urlDisplay = document.getElementById('remote-url-display');
  const qrRemoteImg = document.getElementById('qr-remote-img');
  const fpEl = document.getElementById('remote-fingerprint');

  if (portPill && remote.port) portPill.textContent = t('msg.porta', remote.port);
  if (fpEl) fpEl.textContent = remote.fingerprint ? t('msg.impronta_sha_256', remote.fingerprint) : '';

  const active = remote.active && remote.url;
  if (modalBadge) {
    modalBadge.className = active ? 'badge badge-emerald' : 'badge badge-secondary';
    modalBadge.textContent = active ? t('msg.attivo_https') : t('msg.attivo');
  }
  if (inactiveView) inactiveView.style.display = active ? 'none' : 'block';
  if (activeView) activeView.style.display = active ? 'block' : 'none';
  if (inactiveText) {
    inactiveText.textContent = remote.error
      ? t('msg.accesso_internet_disponibile', remote.error)
      : t('ui.porta_l_accesso_internet_aperta');
  }
  if (active) {
    if (urlDisplay) urlDisplay.textContent = remote.url;
    if (qrRemoteImg && remote.qr_code) qrRemoteImg.src = remote.qr_code;
  }
}

async function _remoteAction(path, okMsg) {
  try {
    const res = await fetch(path, { method: 'POST' });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderRemoteStatus(data);
    if (data.active && data.url) {
      showToast(okMsg, 'success');
    } else if (data.error) {
      showToast((t('msg.accesso_internet_disponibile_2') + " ") + data.error, 'warning');
    }
    return data;
  } catch (err) {
    showToast((t('msg.errore_accesso_internet') + " ") + err.message, 'error');
    return null;
  }
}

async function startRemoteAccess() {
  showToast(t('msg.apertura_porta_sul_router_corso'), 'info');
  await _remoteAction('/api/remote/start', t('msg.accesso_internet_attivo'));
}

async function restartRemoteAccess() {
  showToast(t('msg.riapertura_porta_controllo_dell_ip'), 'info');
  await _remoteAction('/api/remote/start', t('msg.porta_riaperta_indirizzo_aggiornato'));
}

async function stopRemoteAccess() {
  const data = await _remoteAction('/api/remote/stop', '');
  if (data) showToast(t('msg.accesso_internet_disattivato_porta_chiusa'), 'info');
}

async function openUrlInBrowser(url) {
  if (!url) return;
  try {
    await fetch('/api/open-browser', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    });
    showToast(t('msg.apertura_nel_browser_predefinito'), 'info');
  } catch (_) {
    window.open(url, '_blank');
  }
}

async function openRemoteUrlInBrowser() {
  const urlEl = document.getElementById('remote-url-display');
  const url = urlEl ? urlEl.textContent.trim() : null;
  if (!url || !url.startsWith('http')) {
    showToast(t('msg.nessun_indirizzo_internet_attivo_aprire'), 'warning');
    return;
  }
  await openUrlInBrowser(url);
}

async function openLanUrlInBrowser() {
  const urlEl = document.getElementById('qr-modal-url');
  const url = urlEl ? urlEl.textContent.trim() : null;
  if (!url || !url.startsWith('http')) {
    showToast(t('msg.nessun_indirizzo_lan_valido_aprire'), 'warning');
    return;
  }
  await openUrlInBrowser(url);
}

async function refreshAllNetworkInfo() {
  showToast(t('msg.aggiornamento_parametri_rete_corso'), 'info');
  try {
    const [netRes, remoteRes, upnpRes] = await Promise.all([
      fetch('/api/network').then(r => r.json()).catch(() => null),
      fetch('/api/remote/status').then(r => r.json()).catch(() => null),
      fetch('/api/upnp/status').then(r => r.json()).catch(() => null)
    ]);
    if (netRes) updateNetworkInfo(netRes);
    if (remoteRes) renderRemoteStatus(remoteRes);
    if (upnpRes) renderUPnPStatus(upnpRes);
    showToast(t('msg.stato_rete_connessioni_aggiornato'), 'success');
  } catch (err) {
    showToast((t('msg.errore_durante_l_aggiornamento_rete') + " ") + err.message, 'error');
  }
}

// =========================================================
// UPNP DIRECT MINECRAFT PORT FORWARDING
// =========================================================
let currentUPnP = null;

function renderUPnPStatus(upnp) {
  if (!upnp) return;
  currentUPnP = upnp;

  const badgeStatus = document.getElementById('upnp-badge-status');
  const descText = document.getElementById('upnp-desc-text');
  const routerIpEl = document.getElementById('upnp-router-ip');
  const mappedPortEl = document.getElementById('upnp-mapped-port');
  const extIpEl = document.getElementById('upnp-external-ip');
  const publicIpFull = document.getElementById('mc-public-ip-full');

  if (routerIpEl && upnp.router_ip) routerIpEl.textContent = upnp.router_ip;
  if (mappedPortEl && upnp.port) mappedPortEl.textContent = `${upnp.port} TCP`;
  if (extIpEl && upnp.external_ip) extIpEl.textContent = upnp.external_ip;
  if (publicIpFull && upnp.display_address) publicIpFull.textContent = upnp.display_address;

  if (badgeStatus) {
    if (upnp.active) {
      badgeStatus.textContent = t('ui.porta_aperta_sul_router');
      badgeStatus.className = 'badge badge-emerald';
    } else if (upnp.supported) {
      badgeStatus.textContent = t('msg.router_upnp_rilevato');
      badgeStatus.className = 'badge badge-sapphire';
    } else {
      badgeStatus.textContent = t('msg.upnp_attesa');
      badgeStatus.className = 'badge badge-amber';
    }
  }

  if (descText && upnp.message) {
    descText.textContent = upnp.message;
  }
}

async function fetchUPnPStatus() {
  try {
    const res = await fetch('/api/upnp/status');
    const data = await res.json();
    renderUPnPStatus(data);
  } catch (err) {
    console.error('Errore recupero stato UPnP:', err);
  }
}

async function refreshUPnPPort() {
  try {
    showToast(t('msg.rinnovo_apertura_porta_upnp_sul'), 'info');
    const res = await fetch('/api/upnp/refresh', { method: 'POST' });
    const data = await res.json();
    renderUPnPStatus(data);
    if (data.success !== false) {
      showToast(t('msg.porta_aperta_successo_sul_router'), 'success');
    } else {
      showToast((t('msg.avviso_upnp') + " ") + (data.message || t('msg.verifica_modem_router')), 'warning');
    }
    try {
      const netRes = await fetch('/api/network');
      const netData = await netRes.json();
      updateNetworkInfo(netData);
    } catch (_) {}
  } catch (err) {
    showToast((t('msg.errore_rinnovo_upnp') + " ") + err.message, 'error');
  }
}

// =========================================================
// MODAL TABS & CONTROL
// =========================================================
function ensureLanQrRendered() {
  const qrLanImg = document.getElementById('qr-lan-img');
  const qrModalUrl = document.getElementById('qr-modal-url');
  const lanIpEl = document.getElementById('lan-ip-display');
  const lanPortEl = document.getElementById('lan-port-display');

  if (networkInfo) {
    if (networkInfo.lan_url && qrModalUrl) {
      qrModalUrl.textContent = networkInfo.lan_url;
    }
    if (lanIpEl && (networkInfo.local_ip || networkInfo.lan_url)) {
      lanIpEl.textContent = networkInfo.local_ip || (networkInfo.lan_url ? networkInfo.lan_url.replace('http://', '').split(':')[0] : '--');
    }
    if (lanPortEl) {
      lanPortEl.textContent = `${networkInfo.web_port || 8080} TCP`;
    }
    const localQr = networkInfo.qr_code || networkInfo.lan_qr || (networkInfo.apk && networkInfo.apk.lan_qr);
    if (localQr && qrLanImg) {
      qrLanImg.src = localQr;
      return;
    }
  }

  fetch('/api/network')
    .then(r => r.json())
    .then(net => {
      if (net) {
        updateNetworkInfo(net);
        const qr = net.qr_code || net.lan_qr || (net.apk && net.apk.lan_qr);
        if (qr && qrLanImg) {
          qrLanImg.src = qr;
        }
      }
    })
    .catch(() => {
      if (qrLanImg && !qrLanImg.src) {
        const fallbackUrl = (networkInfo && networkInfo.lan_url) || window.location.origin;
        qrLanImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=4&data=${encodeURIComponent(fallbackUrl)}`;
      }
    });
}

function switchModalTab(tabId) {
  document.querySelectorAll('.modal-tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('.modal-pane').forEach(pane => pane.style.display = 'none');

  const btn = document.getElementById(tabId.replace('mtab-', 'mtab-btn-'));
  if (btn) btn.classList.add('active');

  const pane = document.getElementById(tabId);
  if (pane) pane.style.display = 'block';

  if (tabId === 'mtab-lan') {
    ensureLanQrRendered();
  }
}

function openQrModal(defaultTab = 'mtab-remote') {
  ensureLanQrRendered();
  if (networkInfo) {
    if (networkInfo.minecraft && networkInfo.minecraft.lan_address) {
      const mcLanDisplay = document.getElementById('mc-lan-addr-display');
      if (mcLanDisplay) mcLanDisplay.textContent = networkInfo.minecraft.lan_address;
    }
  }
  fetchUPnPStatus();
  fetch('/api/remote/status').then(r => r.json()).then(renderRemoteStatus).catch(() => {});
  switchModalTab(defaultTab);
  openModal('modal-qr');
}

function closeQrModal() {
  closeModal('modal-qr');
}

// =========================================================
// APK QR CODE MODAL FOR PHONE DOWNLOAD (FALLBACK SE OTA NON FUNZIONA)
// =========================================================
let currentApkQrSource = 'lan'; // 'lan' or 'remote'

function openApkQrModal() {
  updateApkQrDisplay();
  openModal('modal-apk-qr');
}

function closeApkQrModal() {
  closeModal('modal-apk-qr');
}

function toggleApkQrSource(source) {
  currentApkQrSource = source;
  const btnLan = document.getElementById('btn-apk-qr-lan');
  const btnRemote = document.getElementById('btn-apk-qr-remote');
  if (btnLan) {
    btnLan.className = source === 'lan' ? 'btn btn-xs btn-sapphire active' : 'btn btn-xs btn-secondary';
  }
  if (btnRemote) {
    btnRemote.className = source === 'remote' ? 'btn btn-xs btn-sapphire active' : 'btn btn-xs btn-secondary';
  }
  updateApkQrDisplay();
}

function updateApkQrDisplay() {
  const qrImg = document.getElementById('apk-qr-image');
  const badge = document.getElementById('apk-qr-net-badge');
  const inputUrl = document.getElementById('apk-download-url-input');

  let targetUrl = '';
  let targetQr = '';

  if (networkInfo && networkInfo.apk) {
    const apkData = networkInfo.apk;
    if (currentApkQrSource === 'remote' && apkData.remote_url) {
      targetUrl = apkData.remote_url;
      targetQr = apkData.remote_qr || apkData.preferred_qr;
    } else {
      targetUrl = apkData.lan_url || apkData.preferred_url;
      targetQr = apkData.lan_qr || apkData.preferred_qr;
    }
  }

  // Fallback if networkInfo is not fully loaded
  if (!targetUrl) {
    const origin = window.location.origin;
    targetUrl = `${origin}/api/app/download`;
  }

  if (qrImg) {
    if (targetQr) {
      qrImg.src = targetQr;
    } else if (targetUrl) {
      qrImg.src = `https://api.qrserver.com/v1/create-qr-code/?size=220x220&margin=4&data=${encodeURIComponent(targetUrl)}`;
    }
  }
  if (inputUrl) {
    inputUrl.value = targetUrl;
  }
  if (badge) {
    if (currentApkQrSource === 'remote') {
      badge.textContent = t('msg.download_internet_certificato_pc');
      badge.className = 'badge badge-sapphire';
    } else {
      badge.textContent = t('msg.connessione_wi_fi_rete_locale');
      badge.className = 'badge badge-emerald';
    }
  }
}

function copyApkDownloadUrl() {
  const inputUrl = document.getElementById('apk-download-url-input');
  if (!inputUrl) return;
  copyRawText(inputUrl.value, null, t('header.copiato'));
  showToast(t('msg.link_download_apk_copiato_negli'), 'success');
}

// =========================================================
// SIDEBAR DRAWER & HOME NAVIGATION
// =========================================================
function toggleSidebarDrawer(forceOpen) {
  const drawer = document.getElementById('sidebar-drawer');
  const backdrop = document.getElementById('sidebar-backdrop');
  if (!drawer || !backdrop) return;

  const isOpen = drawer.classList.contains('open');
  const shouldOpen = typeof forceOpen === 'boolean' ? forceOpen : !isOpen;

  if (shouldOpen) {
    drawer.classList.add('open');
    backdrop.classList.add('open');
    drawer.setAttribute('aria-hidden', 'false');
    document.body.classList.add('drawer-open');
    document.documentElement.classList.add('drawer-open');
  } else {
    drawer.classList.remove('open');
    backdrop.classList.remove('open');
    drawer.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('drawer-open');
    document.documentElement.classList.remove('drawer-open');
  }
}

// Global scroll prevention when sidebar drawer is open on PC / mobile
// Keeps the background completely static while allowing smooth scroll inside the sidebar menu
window.addEventListener('wheel', (e) => {
  if (document.body.classList.contains('drawer-open')) {
    const drawerNav = e.target.closest('.sidebar-nav') || e.target.closest('#sidebar-drawer');
    if (!drawerNav) {
      e.preventDefault();
    }
  }
}, { passive: false });

window.addEventListener('touchmove', (e) => {
  if (document.body.classList.contains('drawer-open')) {
    const drawerNav = e.target.closest('.sidebar-nav') || e.target.closest('#sidebar-drawer');
    if (!drawerNav) {
      e.preventDefault();
    }
  }
}, { passive: false });

function goHome() {
  localStorage.setItem('mc_active_tab', 'home');

  // Highlight snav-home in drawer, remove from others
  document.querySelectorAll('.sidebar-nav-item').forEach(btn => btn.classList.remove('active'));
  const homeNav = document.getElementById('snav-home');
  if (homeNav) homeNav.classList.add('active');

  // Deactivate all tab-panes
  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
  
  // Hide tabs container, show overview dashboard & connection banner
  const tabsContainer = document.getElementById('tabs-container');
  if (tabsContainer) tabsContainer.style.display = 'none';

  const overviewGrid = document.querySelector('.overview-dashboard-grid');
  if (overviewGrid) overviewGrid.style.display = '';

  resizeCanvas();
  drawChart();

  toggleSidebarDrawer(false);
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function selectSidebarNav(tabId) {
  if (tabId === 'home') {
    goHome();
    return;
  }
  switchMainTab(tabId);
  toggleSidebarDrawer(false);
}

// =========================================================
// TABS SWITCHING
// =========================================================
function switchMainTab(tabId) {
  if (tabId === 'home') {
    goHome();
    return;
  }
  localStorage.setItem('mc_active_tab', tabId);

  // Update sidebar navigation active item
  document.querySelectorAll('.sidebar-nav-item').forEach(btn => btn.classList.remove('active'));
  const snav = document.getElementById(`snav-${tabId}`);
  if (snav) snav.classList.add('active');

  // Show tabs container, hide overview dashboard so the active tool has full visibility
  const tabsContainer = document.getElementById('tabs-container');
  if (tabsContainer) tabsContainer.style.display = 'block';

  const overviewGrid = document.querySelector('.overview-dashboard-grid');
  if (overviewGrid) overviewGrid.style.display = 'none';

  // Panes
  document.querySelectorAll('.tab-pane').forEach(pane => pane.classList.remove('active'));
  const pane = document.getElementById(tabId);
  if (pane) pane.classList.add('active');

  // Scroll smoothly to top
  window.scrollTo({ top: 0, behavior: 'smooth' });

  // Auto-scroll on tab switch
  if (tabId === 'tab-chat') {
    unreadChatCount = 0;
    const badge = document.getElementById('snav-chat-count');
    if (badge) badge.style.display = 'none';
    const chatBox = document.getElementById('chat-stream-box');
    if (chatBox) {
      if (chatBox.querySelectorAll('.chat-msg-row, .chat-system-line').length === 0) {
        fetchChatHistory();
      }
      chatBox.scrollTop = chatBox.scrollHeight;
    }
  } else if (tabId === 'tab-console') {
    const terminal = document.getElementById('console-terminal');
    if (terminal) {
      if (terminal.childElementCount === 0) {
        fetchTerminalLogs();
      }
      terminal.scrollTop = terminal.scrollHeight;
    }
    const input = document.getElementById('cmd-input');
    if (input) input.focus();
  } else if (tabId === 'tab-players') {
    loadWhitelist();
    loadBannedPlayers();
    fetchStatusHttp();
  } else if (tabId === 'tab-commands') {
    renderCommands();
  } else if (tabId === 'tab-errors') {
    fetchErrors();
  } else if (tabId === 'tab-mods') {
    loadModsList();
  } else if (tabId === 'tab-properties') {
    if (typeof metrics24hAutoRefreshTimer !== 'undefined' && metrics24hAutoRefreshTimer) {
      clearInterval(metrics24hAutoRefreshTimer);
      metrics24hAutoRefreshTimer = null;
    }
    loadServerProperties();
  } else if (tabId === 'tab-metrics-24h') {
    loadMetrics24h();
    if (typeof metrics24hAutoRefreshTimer !== 'undefined' && !metrics24hAutoRefreshTimer) {
      metrics24hAutoRefreshTimer = setInterval(() => loadMetrics24h(false), 60000);
    }
  } else if (tabId === 'tab-instances') {
    if (typeof metrics24hAutoRefreshTimer !== 'undefined' && metrics24hAutoRefreshTimer) {
      clearInterval(metrics24hAutoRefreshTimer);
      metrics24hAutoRefreshTimer = null;
    }
    loadInstancesList(true);
  } else if (tabId === 'tab-files') {
    if (typeof metrics24hAutoRefreshTimer !== 'undefined' && metrics24hAutoRefreshTimer) {
      clearInterval(metrics24hAutoRefreshTimer);
      metrics24hAutoRefreshTimer = null;
    }
    loadDirectoryFiles(currentFileManagerPath || '');
  } else if (tabId === 'tab-store') {
    if (typeof metrics24hAutoRefreshTimer !== 'undefined' && metrics24hAutoRefreshTimer) {
      clearInterval(metrics24hAutoRefreshTimer);
      metrics24hAutoRefreshTimer = null;
    }
    executeStoreSearch(0);
  } else {
    if (typeof metrics24hAutoRefreshTimer !== 'undefined' && metrics24hAutoRefreshTimer) {
      clearInterval(metrics24hAutoRefreshTimer);
      metrics24hAutoRefreshTimer = null;
    }
  }
}

// =========================================================
// STATUS RENDERING
// =========================================================
function renderFullStatus(data) {
  if (!data) return;
  currentStatus = data;
  const stateChanged = currentServerState !== data.state;
  currentServerState = data.state;
  // L'etichetta "IN ESECUZIONE"/"ATTIVA" della scheda istanza dipende dallo stato del server
  if (stateChanged && currentInstancesData && (currentInstancesData.instances || []).length &&
      document.getElementById('instances-cards-grid')) renderInstancesGrid();
  // Auto-hide mods restart banner on server stop/restart
  if (modsNeedRestart && (data.state === 'RESTARTING' || data.state === 'STARTING' || data.state === 'STOPPED')) {
    modsNeedRestart = false;
    dismissModsRestartBanner();
  }

  if (typeof data.is_local_client === 'boolean') {
    updateHostOnlyVisibility(data.is_local_client);
  }

  // Sync quick commands bar server state pill
  const quickStatus = document.getElementById('cmd-quick-server-status');
  if (quickStatus) {
    if (data.state === 'ONLINE') {
      quickStatus.textContent = 'Server Online';
      quickStatus.className = 'badge badge-emerald';
    } else if (data.state === 'STARTING') {
      quickStatus.textContent = t('msg.avvio_corso');
      quickStatus.className = 'badge badge-amber';
    } else {
      quickStatus.textContent = `Server ${data.state}`;
      quickStatus.className = 'badge badge-secondary';
    }
  }

  // Sync backup modal warning if open
  const backupServerBadge = document.getElementById('backup-server-state-badge');
  const backupRunningWarn = document.getElementById('backup-server-running-warn');
  const backupStartBtn = document.getElementById('btn-backup-start-now');
  if (backupServerBadge) {
    backupServerBadge.textContent = currentServerState;
    backupServerBadge.className = currentServerState === 'STOPPED' ? 'badge badge-emerald' : 'badge badge-amber';
  }
  if (backupRunningWarn && (!currentBackupData || !currentBackupData.active)) {
    if (currentServerState !== 'STOPPED') {
      backupRunningWarn.style.display = 'flex';
      if (backupStartBtn) backupStartBtn.disabled = true;
    } else {
      backupRunningWarn.style.display = 'none';
      if (backupStartBtn) backupStartBtn.disabled = false;
    }
  }

  // 1. Status Pill in Header
  const pill = document.getElementById('server-status-pill');
  const pillText = document.getElementById('server-status-text');
  const btnStart = document.getElementById('btn-start');
  const btnStop = document.getElementById('btn-stop');
  const btnRestart = document.getElementById('btn-restart');

  pill.className = 'status-pill';
  if (data.state === 'ONLINE') {
    pill.classList.add('status-online');
    pillText.textContent = 'ONLINE';
    btnStart.disabled = true;
    btnStop.disabled = false;
    btnStop.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12"></rect></svg><span>${th('serverbar.arresta')}</span>`;
    btnStop.className = 'btn btn-ruby';
    btnStop.onclick = openStopModal;
    btnRestart.disabled = false;
    btnRestart.onclick = () => restartServer(10);
  } else if (data.state === 'STARTING') {
    pill.classList.add('status-starting');
    pillText.textContent = t('header.avvio');
    btnStart.disabled = true;
    btnStop.disabled = false;
    btnStop.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12"></rect></svg><span>${th('serverbar.forza_arresto')}</span>`;
    btnStop.className = 'btn btn-ruby';
    btnStop.onclick = forceKillServer;
    btnRestart.disabled = true;
  } else if (data.state === 'STOPPING') {
    pill.classList.add('status-starting');
    pillText.textContent = t('header.arresto');
    btnStart.disabled = true;
    btnStop.disabled = false;
    btnStop.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12"></rect></svg><span>${th('serverbar.forza_arresto')}</span>`;
    btnStop.className = 'btn btn-ruby';
    btnStop.onclick = forceKillServer;
    btnRestart.disabled = false;
    btnRestart.onclick = () => restartServer(0);
  } else if (data.state === 'RESTARTING') {
    pill.classList.add('status-starting');
    pillText.textContent = t('header.riavvio');
    btnStart.disabled = true;
    btnStop.disabled = true;
    btnRestart.disabled = true;
  } else if (data.state === 'CRASHED') {
    pill.classList.add('status-crashed');
    pillText.textContent = t('header.crashato');
    btnStart.disabled = false;
    btnStop.disabled = false;
    btnStop.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12"></rect></svg><span>${th('serverbar.forza_arresto')}</span>`;
    btnStop.className = 'btn btn-ruby';
    btnStop.onclick = forceKillServer;
    btnRestart.disabled = false;
    btnRestart.onclick = () => restartServer(0);
  } else {
    pill.classList.add('status-stopped');
    pillText.textContent = 'OFFLINE';
    btnStart.disabled = false;
    btnStop.disabled = true;
    btnStop.innerHTML = `<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><rect x="6" y="6" width="12" height="12"></rect></svg><span>${th('serverbar.arresta')}</span>`;
    btnStop.className = 'btn btn-ruby-outline';
    btnStop.onclick = openStopModal;
    btnRestart.disabled = true;
  }

  // Manage header Kill button state (disable when server is OFFLINE)
  const btnKillAction = document.getElementById('btn-kill-action');
  if (btnKillAction) {
    btnKillAction.disabled = (data.state !== 'ONLINE' && data.state !== 'STARTING' && data.state !== 'STOPPING' && data.state !== 'RESTARTING');
  }

  // 1b. Server TPS Pill in Header
  const tpsPill = document.getElementById('server-tps-pill');
  const tpsText = document.getElementById('server-tps-text');
  if (tpsPill && tpsText) {
    const tpsVal = typeof data.tps === 'number' ? data.tps : 0;
    if (data.state === 'ONLINE') {
      tpsText.textContent = `${tpsVal.toFixed(1)} TPS`;
      tpsPill.className = 'status-pill status-tps ' + (tpsVal >= 19.0 ? 'tps-good' : (tpsVal >= 15.0 ? 'tps-warn' : 'tps-bad'));
    } else {
      tpsText.textContent = '-- TPS';
      tpsPill.className = 'status-pill status-stopped';
    }
  }

  // 2. Crash Banner
  const crashBanner = document.getElementById('crash-banner');
  if (data.state === 'CRASHED' || (data.crash_protection && data.crash_protection.countdown > 0)) {
    crashBanner.style.display = 'flex';
    const countdownVal = document.getElementById('crash-countdown-val');
    const crashDesc = document.getElementById('crash-desc');
    if (data.crash_protection.paused) {
      document.getElementById('crash-title').textContent = t('banner.crash_loop_rilevato_auto_riavvio');
      crashDesc.textContent = t('banner.troppi_crash_consecutivi_controlla_sezione');
    } else {
      document.getElementById('crash-title').textContent = t('banner.server_si_chiuso_inaspettatamente_crash');
      countdownVal.textContent = data.crash_protection.countdown;
      crashDesc.innerHTML = `${th('banner.riavvio_automatico_emergenza_tra')} <span id="crash-countdown-val">${data.crash_protection.countdown}</span> ${th('banner.secondi')}`;
    }
  } else {
    crashBanner.style.display = 'none';
  }

  // 2b. Server Dir Warning Banner
  const dirWarningBanner = document.getElementById('banner-server-dir-warning');
  if (dirWarningBanner) {
    if (data.server_dir_exists === false) {
      dirWarningBanner.style.display = 'flex';
    } else {
      dirWarningBanner.style.display = 'none';
    }
  }

  // 3. Card Giocatori
  const players = data.players || { count: 0, max: 20, list: [] };
  updateText('players-count', players.count);
  updateText('subbar-players-count', players.count);
  updateText('players-max', players.max || 20);
  updateText('subbar-players-max', players.max || 20);
  updateText('players-list-count', players.count);
  updateText('players-nav-count', players.count);
  updateText('snav-players-count', players.count);

  const pingBadge = document.getElementById('slp-ping-badge');
  if (data.slp && data.slp.latency_ms !== null && data.slp.latency_ms !== undefined) {
    updateText('slp-ping-badge', `${data.slp.latency_ms} ms`);
    if (pingBadge && pingBadge.style.display !== 'inline-block') pingBadge.style.display = 'inline-block';
  } else {
    updateText('slp-ping-badge', '- ms');
  }

  const pBadge = document.getElementById('players-status-badge');
  if (pBadge) {
    if (players.count > 0) {
      updateText('players-status-badge', t('tpl.nel_mondo', players.count));
      if (pBadge.className !== 'badge badge-emerald') pBadge.className = 'badge badge-emerald';
    } else {
      updateText('players-status-badge', data.state === 'ONLINE' ? t('status.nessun_giocatore_online') : t('status.server_raggiungibile'));
      if (pBadge.className !== 'badge') pBadge.className = 'badge';
    }
  }

  renderPlayersList(players.list);
  updateOnlinePlayerNames(players.list);

  // 4. Card Uptime
  const isCrashed = (data.state === 'CRASHED') || (data.crash && data.crash.is_crashed);
  const uptimeElem = document.getElementById('uptime-display');
  const subbarUptimeElem = document.getElementById('subbar-uptime-display');
  const startTimeElem = document.getElementById('start-time-display');

  if (isCrashed) {
    updateText('uptime-display', data.uptime_formatted || '00m 00s');
    updateText('subbar-uptime-display', data.uptime_formatted || '00m 00s');
    updateText('start-time-display', t('status.server_crashato_bloccato'));
    if (uptimeElem) uptimeElem.style.color = '#e5566f';
    if (subbarUptimeElem) subbarUptimeElem.style.color = '#e5566f';
    if (startTimeElem) startTimeElem.style.color = '#e5566f';
    serverStartTimeUnix = null;
  } else if (data.state === 'ONLINE' || data.state === 'STARTING') {
    updateText('uptime-display', data.uptime_formatted || '00m 00s');
    updateText('subbar-uptime-display', data.uptime_formatted || '00m 00s');
    updateText('start-time-display', data.start_time_str ? t('tpl.avviato', data.start_time_str) : t('status.esecuzione'));
    if (uptimeElem) uptimeElem.style.color = '';
    if (subbarUptimeElem) subbarUptimeElem.style.color = '';
    if (startTimeElem) startTimeElem.style.color = '';
    if (data.start_time) {
      serverStartTimeUnix = data.start_time;
    } else if (data.uptime_seconds) {
      serverStartTimeUnix = Math.floor(Date.now() / 1000) - data.uptime_seconds;
    }
  } else {
    updateText('uptime-display', '00m 00s');
    updateText('subbar-uptime-display', '00m 00s');
    updateText('start-time-display', t('status.server_attivo'));
    if (uptimeElem) uptimeElem.style.color = '';
    if (subbarUptimeElem) subbarUptimeElem.style.color = '';
    if (startTimeElem) startTimeElem.style.color = '';
    serverStartTimeUnix = null;
  }

  // 5. Card Timer Riavvio 4 Ore (Dedicated Module)
  const sched = data.scheduled_restart || {};
  const isEnabled = sched.enabled !== false;
  scheduledTimerActive = isEnabled && (data.state === 'ONLINE');
  if (typeof sched.remaining_seconds === 'number') {
    scheduledRemainingSeconds = sched.remaining_seconds;
  }
  if (typeof sched.interval_minutes === 'number') {
    scheduledTotalSeconds = sched.interval_minutes * 60;
  }
  const timeFormatted = sched.formatted || sched.remaining_formatted || (isEnabled ? '04h 00m 00s' : t('status.disattivato'));
  const progressPct = sched.progress_pct !== undefined ? sched.progress_pct : (sched.progress_percent || 0);

  const countdownEl = document.getElementById('restart-countdown');
  const statusPill = document.getElementById('restart-status-pill');
  const toggleSched = document.getElementById('toggle-scheduled-restart');
  const progBar = document.getElementById('restart-progress-bar');

  if (countdownEl) {
    if (!isEnabled) {
      countdownEl.textContent = t('status.pausa_disattivato');
      countdownEl.style.color = '#9a9ca2';
    } else {
      countdownEl.textContent = timeFormatted;
      countdownEl.style.color = '#fcd34d';
    }
  }

  if (statusPill) {
    if (!isEnabled) {
      statusPill.textContent = t('status.disattivato');
      statusPill.className = 'badge badge-secondary';
    } else if (data.state !== 'ONLINE') {
      statusPill.textContent = t('status.server_offline_2');
      statusPill.className = 'badge badge-secondary';
    } else {
      statusPill.textContent = t('status.esecuzione_2');
      statusPill.className = 'badge badge-emerald';
    }
  }

  if (progBar) {
    progBar.style.width = isEnabled ? `${Math.min(100, Math.max(0, progressPct))}%` : '0%';
  }

  if (toggleSched && toggleSched.checked !== isEnabled) {
    toggleSched.checked = isEnabled;
  }

  // 6. Card Errori & Crash
  const errorsCount = (data.errors_count !== undefined)
    ? data.errors_count
    : ((data.errors && (data.errors.total !== undefined ? data.errors.total : (Array.isArray(data.errors) ? data.errors.length : 0))) || 0);
  const crashProt = data.crash || data.crash_protection || {};
  const totalErrors = errorsCount;

  updateText('server-errors-badge', totalErrors);
  updateText('snav-errors-count', totalErrors);

  const errorsBadge = document.getElementById('server-errors-badge');
  if (errorsBadge) {
    if (isCrashed) {
      errorsBadge.className = 'badge badge-ruby';
    } else if (totalErrors > 0) {
      errorsBadge.className = 'badge badge-amber';
    } else {
      errorsBadge.className = 'badge badge-emerald';
    }
  }

  const crashStatusText = document.getElementById('crash-status-text');
  if (isCrashed) {
    updateText('crash-status-text', t('status.server_andato_crash'));
    if (crashStatusText) crashStatusText.style.color = '#e5566f';
  } else if (crashProt.paused) {
    updateText('crash-status-text', t('status.bloccato_crash_loop'));
    if (crashStatusText) crashStatusText.style.color = '#e5566f';
  } else if (crashProt.countdown && crashProt.countdown > 0) {
    updateText('crash-status-text', t('tpl.riavvio_xs', crashProt.countdown));
    if (crashStatusText) crashStatusText.style.color = '#d9a441';
  } else if (totalErrors > 0) {
    updateText('crash-status-text', (totalErrors === 1 ? t('tpl.errore_rilevato', totalErrors) : t('tpl.errori_rilevati', totalErrors)));
    if (crashStatusText) crashStatusText.style.color = '#e3b35a';
  } else if (data.crash_reports_count && data.crash_reports_count > 0) {
    updateText('crash-status-text', t('tpl.crash_report_archiviati', data.crash_reports_count));
    if (crashStatusText) crashStatusText.style.color = '#9a9ca2';
  } else {
    updateText('crash-status-text', t('status.nessun_errore_critico'));
    if (crashStatusText) crashStatusText.style.color = '#9ccc65';
  }

  // 7. Eco Mode & Anti-Lag Watchdog Status Badges
  if (data.eco_mode) {
    const ecoBadge = document.getElementById('overview-eco-badge');
    const cfgEcoBadge = document.getElementById('cfg-eco-badge');
    const isEco = data.eco_mode.enabled !== false;
    const mins = data.eco_mode.minutes || 30;
    if (ecoBadge) {
      ecoBadge.textContent = isEco ? t('tpl.attivo_xm', mins) : t('status.disattivato');
      ecoBadge.className = isEco ? 'badge badge-emerald' : 'badge badge-secondary';
    }
    if (cfgEcoBadge) {
      cfgEcoBadge.textContent = isEco ? t('tpl.attivo_xm', mins) : t('status.disattivato');
      cfgEcoBadge.className = isEco ? 'badge badge-emerald' : 'badge badge-secondary';
    }
  }

  if (data.anti_lag) {
    const alBadge = document.getElementById('overview-antilag-badge');
    const cfgAlBadge = document.getElementById('cfg-antilag-badge');
    const isAl = data.anti_lag.enabled !== false;
    if (alBadge) {
      alBadge.textContent = isAl ? t('status.attivo_13_tps') : t('status.disattivato');
      alBadge.className = isAl ? 'badge badge-emerald' : 'badge badge-secondary';
    }
    if (cfgAlBadge) {
      cfgAlBadge.textContent = isAl ? t('status.attivo_13_tps') : t('status.disattivato');
      cfgAlBadge.className = isAl ? 'badge badge-amber' : 'badge badge-secondary';
    }
  }

  // 8. Sezione Risorse
  renderMetrics(data.metrics);

  // 9. Update Metrics Chart
  if (data.metrics_history && Array.isArray(data.metrics_history)) {
    metricsHistory = data.metrics_history;
    drawChart();
  }
}

function renderMetrics(m) {
  if (!m) return;

  const hostCpu = Math.round(m.host_cpu_pct !== undefined ? m.host_cpu_pct : (m.host_cpu !== undefined ? m.host_cpu : 0));
  updateText('host-cpu-val', `${hostCpu}%`);
  updateWidth('host-cpu-bar', hostCpu);

  const hostRamPct = Math.round(m.host_ram_pct !== undefined ? m.host_ram_pct : (m.host_ram_percent !== undefined ? m.host_ram_percent : 0));
  const hostRamUsed = Number(m.host_ram_used_gb || 0).toFixed(1);
  const hostRamTotal = Number(m.host_ram_total_gb || 32).toFixed(1);
  updateText('host-ram-val', `${hostRamPct}%`);
  updateWidth('host-ram-bar', hostRamPct);
  updateText('host-ram-detail', `${hostRamUsed} GB / ${hostRamTotal} GB`);

  const mcRamGbNum = Number(m.mc_ram_gb !== undefined ? m.mc_ram_gb : (m.mc_ram_mb ? m.mc_ram_mb / 1024 : 0));
  const mcRamGb = mcRamGbNum.toFixed(1);
  const mcRamMaxNum = Number((m.mc_ram_max_gb && m.mc_ram_max_gb > 0) ? m.mc_ram_max_gb : ((currentStatus && currentStatus.server_ram_max_gb) || 16.0));
  const calcPct = mcRamMaxNum > 0 ? Math.min(100, Math.round((mcRamGbNum / mcRamMaxNum) * 100)) : 0;
  const mcRamPct = m.mc_ram_pct !== undefined ? Math.round(m.mc_ram_pct) : (m.mc_ram_percent !== undefined ? Math.round(m.mc_ram_percent) : calcPct);

  const mcRssGb = m.mc_rss_gb ? Number(m.mc_rss_gb).toFixed(1) : null;
  const rssSuffix = mcRssGb ? ` ${t('tpl.memoria_fisica_so_gb', mcRssGb)}` : '';

  updateText('mc-ram-val', `${mcRamGb} GB`);
  updateWidth('mc-ram-bar', mcRamPct);
  updateText('mc-ram-detail', t('tpl.gb_usati_gb_assegnati', mcRamGb, mcRamMaxNum.toFixed(1), mcRamPct, rssSuffix));

  const mcBar = document.getElementById('mc-ram-bar');
  const mcVal = document.getElementById('mc-ram-val');
  if (mcBar) {
    mcBar.className = mcRamPct >= 90 ? 'res-fill fill-ruby' : (mcRamPct >= 75 ? 'res-fill fill-amber' : 'res-fill fill-emerald');
  }
  if (mcVal) {
    mcVal.className = mcRamPct >= 90 ? 'res-bar-val res-ruby' : (mcRamPct >= 75 ? 'res-bar-val res-amber' : 'res-bar-val res-emerald');
  }

  const mcCpu = Math.round(m.mc_cpu_pct !== undefined ? m.mc_cpu_pct : (m.mc_cpu !== undefined ? m.mc_cpu : 0));
  updateText('mc-cpu-val', `${mcCpu}%`);
  updateWidth('mc-cpu-bar', mcCpu);

  const diskPct = Math.round(m.host_disk_pct !== undefined ? m.host_disk_pct : (m.disk_percent !== undefined ? m.disk_percent : 0));
  const diskFree = Number(m.host_disk_free_gb !== undefined ? m.host_disk_free_gb : (m.disk_free_gb || 0)).toFixed(1);
  const diskTotal = Number(m.host_disk_total_gb !== undefined ? m.host_disk_total_gb : (m.disk_total_gb || 0)).toFixed(1);
  updateText('disk-val', `${diskPct}%`);
  updateWidth('disk-bar', diskPct);
  updateText('disk-detail', `${t('tpl.gb_liberi_gb_clicca_pulizia', diskFree, diskTotal)} `);

  // Update client-side live metrics rolling history for the chart
  metricsHistory.push({
    host_cpu: hostCpu,
    host_ram_percent: hostRamPct,
    mc_ram_gb: mcRamGbNum,
    mc_ram_pct: mcRamPct,
    mc_ram_percent: mcRamPct,
    mc_ram_max_gb: mcRamMaxNum,
    timestamp: Date.now()
  });
  if (metricsHistory.length > 60) {
    metricsHistory.shift();
  }
  drawChart();
}

// =========================================================
// TAB 1: IN-GAME CHAT & TALK ("PARLARCI DENTRO")
// =========================================================
function formatMcText(rawText) {
  if (!rawText) return '';
  const escaped = escapeHtml(rawText);
  const mcColors = {
    '0': '#000000', '1': '#0000aa', '2': '#00aa00', '3': '#00aaaa',
    '4': '#aa0000', '5': '#aa00aa', '6': '#ffaa00', '7': '#aaaaaa',
    '8': '#555555', '9': '#5555ff', 'a': '#55ff55', 'b': '#55ffff',
    'c': '#ff5555', 'd': '#ff55ff', 'e': '#ffff55', 'f': '#ffffff'
  };
  return escaped.replace(/§([0-9a-fk-or])/gi, (match, code) => {
    const c = code.toLowerCase();
    if (mcColors[c]) {
      return `</span><span style="color: ${mcColors[c]};">`;
    } else if (c === 'l') {
      return '<span style="font-weight: bold;">';
    } else if (c === 'r') {
      return '</span><span>';
    }
    return '';
  });
}

async function fetchChatHistory(isManual = false) {
  try {
    const res = await fetch('/api/chat');
    if (res.ok) {
      const data = await res.json();
      if (data && data.messages && Array.isArray(data.messages)) {
        const chatBox = document.getElementById('chat-stream-box');
        if (chatBox) {
          chatBox.innerHTML = '';
          if (data.messages.length === 0) {
            chatBox.innerHTML = `<div class="chat-empty"><p>${th('ui.nessun_messaggio_recente_invia_saluto')}</p></div>`;
          } else {
            data.messages.forEach(renderChatMessage);
          }
          chatBox.scrollTop = chatBox.scrollHeight;
        }
      }
      if (isManual && typeof showToast === 'function') {
        showToast(t('msg.cronologia_chat_ricaricata'), 'info');
      }
    }
  } catch (e) {
    if (isManual && typeof showToast === 'function') {
      showToast(t('msg.errore_nel_caricamento_chat'), 'error');
    }
  }
}

function handleTalkSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('talk-input');
  const text = input.value.trim();
  if (!text) return;

  const sender = document.getElementById('chat-sender-name').value.trim() || 'Host';
  try { localStorage.setItem('mc_chat_sender', sender); } catch(err){}
  sendTalkMessage(text, sender);
  input.value = '';
  input.focus();
}

function quickTalk(text) {
  const sender = document.getElementById('chat-sender-name').value.trim() || 'Host';
  sendTalkMessage(text, sender);
}

async function sendTalkMessage(text, sender) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: 'talk', message: text, sender: sender }));
    if (typeof showToast === 'function') {
      const preview = text.length > 25 ? text.substring(0, 22) + '...' : text;
      showToast(t('msg.inviato_gioco', preview), 'info');
    }
  } else {
    try {
      const res = await fetch('/api/chat/talk', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: text, sender: sender })
      });
      if (res.ok) {
        if (typeof showToast === 'function') {
          const preview = text.length > 25 ? text.substring(0, 22) + '...' : text;
          showToast(t('msg.inviato_gioco', preview), 'info');
        }
      } else {
        if (typeof showToast === 'function') {
          showToast(t('msg.impossibile_inviare_messaggio_server'), 'error');
        }
      }
    } catch (e) {
      if (typeof showToast === 'function') {
        showToast(t('msg.errore_invio', e.message), 'error');
      }
    }
  }
}

function prefillTalkReply(sender) {
  const input = document.getElementById('talk-input');
  if (!input) return;
  input.value = `@${sender} `;
  input.focus();
}

async function clearChatView() {
  if (confirm(t('msg.sei_sicuro_voler_ripulire_cronologia'))) {
    try {
      await fetch('/api/chat/clear', { method: 'POST' });
    } catch (e) {}
    clearChatUi();
    if (typeof showToast === 'function') {
      showToast(t('msg.cronologia_chat_ripulita'), 'info');
    }
  }
}

function clearChatUi() {
  const chatBox = document.getElementById('chat-stream-box');
  if (chatBox) {
    chatBox.innerHTML = `<div class="chat-empty"><p>${th('ui.nessun_messaggio_recente_invia_saluto')}</p></div>`;
  }
}

function renderChatMessage(msg) {
  const chatBox = document.getElementById('chat-stream-box');
  if (!chatBox) return;
  const empty = chatBox.querySelector('.chat-empty');
  if (empty) empty.remove();

  const div = document.createElement('div');

  if (msg.type === 'system') {
    div.className = 'chat-system-line';
    div.innerHTML = `[${escapeHtml(msg.time)}] ${formatMcText(msg.text)}`;
  } else {
    div.className = 'chat-msg-row';
    const isHost = msg.type === 'host' || msg.sender === 'Host' || msg.sender === 'Server';
    const avatar = isHost ? 'minecraft_block.png' : (msg.avatar_url || `https://mc-heads.net/avatar/${encodeURIComponent(msg.sender)}/32`);

    let replyHtml = '';
    if (msg.reply) {
      replyHtml = `<div class="chat-reply-bubble">${th('msg.assistente', formatMcText(msg.reply))}</div>`;
    }

    const replyBtn = !isHost ? `<button type="button" class="chat-reply-btn" onclick="prefillTalkReply('${escapeJsAttr(msg.sender)}')" title="${th('msg.rispondi', escapeHtml(msg.sender))}">${th('msg.rispondi_2')}</button>` : '';

    div.innerHTML = `
      <img class="chat-avatar" src="${avatar}" alt="${escapeHtml(msg.sender)}" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'32\\' height=\\'32\\'><rect width=\\'32\\' height=\\'32\\' fill=\\'%2310b981\\'/></svg>'">
      <div class="chat-bubble-wrap">
        <div class="chat-sender-line">
          <span class="chat-sender-name ${isHost ? 'chat-sender-host' : ''}">${escapeHtml(msg.sender)}</span>
          <span class="chat-time">${escapeHtml(msg.time)}</span>
          ${replyBtn}
        </div>
        <div class="chat-bubble ${isHost ? 'chat-bubble-host' : ''}">
          ${formatMcText(msg.text)}
          ${replyHtml}
        </div>
      </div>
    `;
  }

  chatBox.appendChild(div);

  const autoScroll = document.getElementById('chk-chat-autoscroll');
  if (!autoScroll || autoScroll.checked) {
    chatBox.scrollTop = chatBox.scrollHeight;
  }

  if (chatBox.childElementCount > 300) {
    chatBox.removeChild(chatBox.firstElementChild);
  }

  // Track unread if not currently on tab-chat
  const activeTab = localStorage.getItem('mc_active_tab') || 'home';
  if (activeTab !== 'tab-chat' && msg.type !== 'host') {
    unreadChatCount++;
    const badge = document.getElementById('snav-chat-count');
    if (badge) {
      badge.textContent = unreadChatCount;
      badge.style.display = 'inline-block';
    }
  }
}

// =========================================================
// TAB 2: ERRORI DEL SERVER ("MOSTRA GLI ERRORI")
// =========================================================
let cachedServerErrors = [];
let serverErrorsSearchQuery = '';

async function fetchErrors(isManual = false) {
  try {
    const res = await fetch('/api/errors');
    if (res.ok) {
      const data = await res.json();
      cachedServerErrors = data.errors || [];
      renderErrorsFeed(cachedServerErrors);
      renderCrashReportsList(data.crash_reports);
      const count = (data.errors && Array.isArray(data.errors)) ? data.errors.length : 0;
      const crashCount = (data.crash_reports && Array.isArray(data.crash_reports)) ? data.crash_reports.length : 0;
      updateText('server-errors-badge', count);
      updateText('snav-errors-count', count);
      updateText('errors-feed-count', count);
      updateText('crash-reports-count', crashCount);
      const badge = document.getElementById('server-errors-badge');
      if (badge) {
        badge.className = count > 0 ? 'badge badge-amber' : 'badge badge-emerald';
      }
      const crashText = document.getElementById('crash-status-text');
      if (crashText && !crashText.textContent.includes('Crash')) {
        if (count > 0) {
          updateText('crash-status-text', (count === 1 ? t('tpl.errore_rilevato', count) : t('tpl.errori_rilevati', count)));
          crashText.style.color = '#e3b35a';
        } else {
          updateText('crash-status-text', t('status.nessun_errore_critico'));
          crashText.style.color = '#9ccc65';
        }
      }
      if (isManual && typeof showToast === 'function') {
        showToast(t('msg.diagnostica_errori_crash_reports_aggiornati'), 'info');
      }
    }
  } catch (e) {
    if (isManual && typeof showToast === 'function') {
      showToast((t('msg.errore_durante_caricamento_errori') + " ") + e.message, 'error');
    }
  }
}

function filterServerErrors(val) {
  serverErrorsSearchQuery = (val || '').toLowerCase().trim();
  const clearBtn = document.getElementById('errors-search-clear');
  if (clearBtn) clearBtn.style.display = serverErrorsSearchQuery ? 'inline-block' : 'none';
  renderErrorsFeed(cachedServerErrors);
}

function clearErrorsSearch() {
  const input = document.getElementById('errors-search-input');
  if (input) {
    input.value = '';
    input.focus();
  }
  filterServerErrors('');
}

function copyAllErrorsText() {
  if (!cachedServerErrors || cachedServerErrors.length === 0) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_errore_copiare'), 'warning');
    return;
  }
  const text = cachedServerErrors.map(e => `[${e.time || ''}] [${e.level || 'ERROR'}] ${e.message || e.raw || ''}`).join('\n');
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      if (typeof showToast === 'function') showToast(t('msg.errori_copiati_negli_appunti', cachedServerErrors.length), 'success');
    }).catch(() => {
      copyRawText(text, null, t('msg.errori_copiati'));
    });
  } else {
    copyRawText(text, null, t('msg.errori_copiati'));
  }
}

function renderErrorsFeed(errors) {
  const container = document.getElementById('errors-feed-container');
  if (!container) return;
  if (!errors || errors.length === 0) {
    container.innerHTML = `<div class="empty-state">${th('msg.nessun_errore_recente_rilevato_server')}</div>`;
    return;
  }

  let list = errors;
  if (serverErrorsSearchQuery) {
    list = list.filter(e => {
      const m = (e.message || '').toLowerCase();
      const r = (e.raw || '').toLowerCase();
      return m.includes(serverErrorsSearchQuery) || r.includes(serverErrorsSearchQuery);
    });
  }

  if (list.length === 0) {
    container.innerHTML = `<div class="empty-state">${th('msg.nessun_errore_corrisponde_filtro_2')}<strong>${escapeHtml(serverErrorsSearchQuery)}</strong>".</div>`;
    return;
  }

  container.innerHTML = '';
  // Show most recent on top
  list.slice().reverse().forEach(err => {
    const item = document.createElement('div');
    const lvl = (err.level || 'ERROR').toUpperCase();
    const isCritical = lvl === 'CRITICAL' || lvl === 'FATAL';
    const isWarn = lvl === 'WARN' || lvl === 'WARNING';

    item.className = `error-item ${isCritical ? 'error-item-critical' : isWarn ? 'error-item-warn' : ''}`;
    const pillClass = isCritical ? 'pill-critical' : isWarn ? 'pill-warn' : 'pill-error';

    item.innerHTML = `
      <div class="error-header-row">
        <div style="display:flex; align-items:center; gap:6px;">
          <span class="error-level-pill ${pillClass}">${escapeHtml(lvl)}</span>
          <span class="error-time">${escapeHtml(err.time || '')}</span>
        </div>
        <button class="btn btn-xs btn-ghost" style="padding: 2px 6px; font-size: 0.72rem; color: #9a9ca2;" title="${th('ui.copia_testo_errore')}" onclick="copyErrorItemText(event, this)">${th('header.copia')}</button>
      </div>
      <div class="error-msg-text">${escapeHtml(err.message || err.raw || t('msg.errore'))}</div>
      ${err.raw && err.raw !== err.message ? `<div class="error-raw-preview">${escapeHtml(err.raw)}</div>` : ''}
    `;
    container.appendChild(item);
  });
}

function copyErrorItemText(e, btn) {
  if (e) e.stopPropagation();
  const item = btn ? btn.closest('.error-item') : null;
  if (!item) return;
  const msgEl = item.querySelector('.error-msg-text');
  const rawEl = item.querySelector('.error-raw-preview');
  const textToCopy = rawEl ? rawEl.textContent : (msgEl ? msgEl.textContent : '');
  if (!textToCopy) return;
  copyRawText(textToCopy, btn, t('msg.copiato'));
  if (typeof showToast === 'function') {
    showToast(t('msg.testo_errore_copiato'), 'info');
  }
}

function appendErrorItem(err) {
  const container = document.getElementById('errors-feed-container');
  if (!container) return;
  const empty = container.querySelector('.empty-state');
  if (empty) empty.remove();

  cachedServerErrors.push(err);
  updateText('errors-feed-count', cachedServerErrors.length);

  const item = document.createElement('div');
  const lvl = (err.level || 'ERROR').toUpperCase();
  const isCritical = lvl === 'CRITICAL' || lvl === 'FATAL';
  const isWarn = lvl === 'WARN' || lvl === 'WARNING';

  item.className = `error-item ${isCritical ? 'error-item-critical' : isWarn ? 'error-item-warn' : ''}`;
  const pillClass = isCritical ? 'pill-critical' : isWarn ? 'pill-warn' : 'pill-error';

  item.innerHTML = `
    <div class="error-header-row">
      <div style="display:flex; align-items:center; gap:6px;">
        <span class="error-level-pill ${pillClass}">${escapeHtml(lvl)}</span>
        <span class="error-time">${escapeHtml(err.time || new Date().toLocaleTimeString('it-IT'))}</span>
      </div>
      <button class="btn btn-xs btn-ghost" style="padding: 2px 6px; font-size: 0.72rem; color: #9a9ca2;" title="${th('ui.copia_testo_errore')}" onclick="copyErrorItemText(event, this)">${th('header.copia')}</button>
    </div>
    <div class="error-msg-text">${escapeHtml(err.message || err.raw || t('msg.errore'))}</div>
    ${err.raw && err.raw !== err.message ? `<div class="error-raw-preview">${escapeHtml(err.raw)}</div>` : ''}
  `;
  container.insertBefore(item, container.firstChild);

  // Prevent memory leak by keeping at most 200 items in DOM
  while (container.children.length > 200) {
    container.lastElementChild.remove();
  }

  // Update badge counter
  const badge = document.getElementById('server-errors-badge');
  if (badge) {
    const cur = parseInt(badge.textContent, 10) || 0;
    const next = cur + 1;
    badge.textContent = next;
    badge.className = isCritical ? 'badge badge-ruby' : 'badge badge-amber';
    updateText('snav-errors-count', next);
    const crashText = document.getElementById('crash-status-text');
    if (crashText && !crashText.textContent.includes('Crash')) {
      updateText('crash-status-text', (next === 1 ? t('tpl.errore_rilevato', next) : t('tpl.errori_rilevati', next)));
      crashText.style.color = isCritical ? '#e5566f' : '#e3b35a';
    }
  }
}

function renderCrashReportsList(reports) {
  const container = document.getElementById('crash-reports-container');
  if (!container) return;
  const count = reports && Array.isArray(reports) ? reports.length : 0;
  updateText('crash-reports-count', count);

  if (!reports || reports.length === 0) {
    container.innerHTML = `<div class="empty-state">${th('msg.nessun_file_crash_report_presente')}</div>`;
    return;
  }

  container.innerHTML = '';
  reports.forEach(r => {
    const card = document.createElement('div');
    card.className = 'crash-file-card';
    card.onclick = () => openCrashReport(r.filename);
    const sizeBadge = r.size_formatted ? `<span class="badge badge-ruby" style="font-size:0.65rem; padding:1px 5px; margin-left:6px;">${escapeHtml(r.size_formatted)}</span>` : '';
    const summaryHtml = (r.error_summary && r.error_summary !== r.description)
      ? `<div class="crash-file-summary" style="font-size:0.70rem; color:#fca5a5; font-family:var(--font-mono); margin-top:3px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis;" title="${escapeHtml(r.error_summary)}">${escapeHtml(r.error_summary)}</div>`
      : '';
    card.innerHTML = `
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <div class="crash-file-name" style="display:flex; align-items:center;">${escapeHtml(r.filename)} ${sizeBadge}</div>
        <span style="font-size:0.70rem; color:#9a9ca2; font-family:var(--font-mono);">${escapeHtml(r.time || '')}</span>
      </div>
      <div class="crash-file-desc" style="margin-top:2px;"><b>${th('msg.descrizione')}</b> ${escapeHtml(r.description || t('msg.crash_generato'))}</div>
      ${summaryHtml}
    `;
    container.appendChild(card);
  });
}

let currentViewingCrashFilename = null;

async function openCrashReport(filename) {
  try {
    currentViewingCrashFilename = filename;
    const titleEl = document.getElementById('crash-view-title');
    const metaEl = document.getElementById('crash-view-meta');
    const textEl = document.getElementById('crash-report-full-text');
    const triageEl = document.getElementById('crash-triage-callout');
    if (titleEl) titleEl.textContent = `Crash Report: ${filename}`;
    if (metaEl) metaEl.textContent = t('msg.caricamento_corso');
    if (textEl) textEl.textContent = t('msg.caricamento_dettagli_crash_report');
    if (triageEl) triageEl.style.display = 'none';
    openModal('modal-crash-view');

    const res = await fetch(`/api/crash-report/${encodeURIComponent(filename)}`);
    if (res.ok) {
      const text = await res.text();
      if (textEl) textEl.textContent = text;
      if (metaEl) {
        const lines = text.split('\n').length;
        const sizeKb = (new Blob([text]).size / 1024).toFixed(1);
        metaEl.textContent = t('msg.righe_kb', lines, sizeKb);
      }

      // Smart Crash Triage
      if (triageEl) {
        let desc = '';
        let exc = '';
        let suspectedMod = '';
        const lines = text.split('\n');
        for (let i = 0; i < Math.min(lines.length, 120); i++) {
          const l = lines[i].trim();
          if (l.startsWith('Description:')) {
            desc = l.replace('Description:', '').trim();
          } else if (!exc && (l.includes('Exception:') || l.includes('Error:') || l.includes('Exception in thread'))) {
            exc = l;
          } else if (!suspectedMod && (l.startsWith('Suspected Mod') || l.includes('Suspected Mods:') || l.includes('Mod File:'))) {
            suspectedMod = l;
          }
        }
        if (desc || exc || suspectedMod) {
          triageEl.style.display = 'block';
          triageEl.innerHTML = `
            <div class="crash-triage-title">${th('msg.diagnostica_rapida_crash')}</div>
            <div class="crash-triage-detail">
              ${desc ? `<div><b>${th('msg.descrizione')}</b> ${escapeHtml(desc)}</div>` : ''}
              ${exc ? `<div style="color: #f0788c; margin-top: 2px;"><b>Eccezione:</b> ${escapeHtml(exc)}</div>` : ''}
              ${suspectedMod ? `<div style="color: #e3b35a; margin-top: 2px;"><b>${escapeHtml(suspectedMod)}</b></div>` : ''}
            </div>
          `;
        }
      }
    } else {
      if (textEl) textEl.textContent = t('msg.errore_durante_caricamento_report_http', res.status);
      if (metaEl) metaEl.textContent = t('msg.errore');
      if (typeof showToast === 'function') {
        showToast(t('msg.impossibile_leggere_file', res.statusText), 'error');
      }
    }
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(t('msg.impossibile_leggere_file', e.message), 'error');
    }
  }
}

function closeCrashViewModal() {
  closeModal('modal-crash-view');
  currentViewingCrashFilename = null;
  const triageEl = document.getElementById('crash-triage-callout');
  if (triageEl) triageEl.style.display = 'none';
}

function copyCrashReportContent(btn) {
  const el = document.getElementById('crash-report-full-text');
  const text = el ? el.textContent : '';
  if (!text) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_testo_copiare'), 'warning');
    return;
  }
  copyRawText(text, btn, t('header.copiato'));
  if (typeof showToast === 'function') {
    showToast(t('msg.crash_report_copiato_negli_appunti'), 'success');
  }
}

function downloadCrashReportFile() {
  const textEl = document.getElementById('crash-report-full-text');
  const text = textEl ? textEl.textContent : '';
  if (!text) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_report_caricato_scaricare'), 'warning');
    return;
  }
  const fname = currentViewingCrashFilename || 'crash-report.txt';
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = fname;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  if (typeof showToast === 'function') showToast(t('msg.scaricato', fname), 'success');
}

async function deleteCurrentCrashReport() {
  if (!currentViewingCrashFilename) return;
  const fname = currentViewingCrashFilename;
  if (!confirm(t('msg.sei_sicuro_voler_eliminare_definitivamente', fname))) {
    return;
  }
  try {
    const res = await fetch(`/api/crash-report/${encodeURIComponent(fname)}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      if (typeof showToast === 'function') {
        showToast(t('msg.crash_report_eliminato', fname), 'info');
      }
      closeCrashViewModal();
      fetchErrors();
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.detail || t('msg.errore_durante_eliminazione')}`, 'error');
      }
    }
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast((t('msg.errore_eliminazione') + " ") + e.message, 'error');
    }
  }
}

async function deleteAllCrashReports() {
  const container = document.getElementById('crash-reports-container');
  const cards = container ? container.querySelectorAll('.crash-file-card') : [];
  if (cards.length === 0) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_crash_report_eliminare'), 'info');
    return;
  }
  if (!confirm(t('msg.sei_sicuro_voler_eliminare_definitivamente_2', cards.length))) {
    return;
  }
  try {
    const res = await fetch('/api/crash-reports/clear-all', { method: 'DELETE' });
    const data = await res.json();
    if (res.ok) {
      if (typeof showToast === 'function') showToast(t('msg.crash_report_eliminati_successo', data.deleted_count || 0), 'success');
      fetchErrors();
    } else {
      if (typeof showToast === 'function') showToast(t('msg.errore_durante_l_eliminazione_crash'), 'error');
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(t('msg.errore_rete', e.message), 'error');
  }
}

async function openCrashFolder() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  try {
    const res = await fetch('/api/crash-reports/open_folder', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      if (typeof showToast === 'function') {
        showToast(t('msg.cartella_crash_reports_aperta_sul'), 'success');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.detail || t('msg.impossibile_aprire_cartella')}`, 'error');
      }
    }
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast((t('msg.errore_apertura_cartella') + " ") + e.message, 'error');
    }
  }
}

async function clearServerErrors() {
  try {
    await fetch('/api/errors/clear', { method: 'POST' });
    clearErrorsUi();
    if (typeof showToast === 'function') {
      showToast(t('msg.errori_diagnostici_cancellati'), 'info');
    }
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast((t('msg.errore_durante_pulizia_errori') + " ") + e.message, 'error');
    }
  }
}

function clearErrorsUi() {
  cachedServerErrors = [];
  const container = document.getElementById('errors-feed-container');
  if (container) {
    container.innerHTML = `<div class="empty-state">${th('msg.nessun_errore_recente_rilevato_server')}</div>`;
  }
  updateText('errors-feed-count', 0);
  const badge = document.getElementById('server-errors-badge');
  if (badge) {
    badge.textContent = '0';
    badge.className = 'badge badge-emerald';
  }
  updateText('snav-errors-count', 0);
  updateText('crash-status-text', t('status.nessun_errore_critico'));
  const crashText = document.getElementById('crash-status-text');
  if (crashText) crashText.style.color = '#9ccc65';
}

// =========================================================
// TAB 3: CONSOLE & LOGS
// =========================================================
let cachedConsoleLogs = [];
let activeConsoleLevelFilter = 'all'; // 'all', 'errors', 'chat'
let consoleSearchQuery = '';

function updateConsoleCounters() {
  let countAll = cachedConsoleLogs.length;
  let countErrors = 0;
  let countChat = 0;
  for (let i = 0; i < cachedConsoleLogs.length; i++) {
    const l = cachedConsoleLogs[i];
    if (l.includes('[Pannello/ALLARME]') || l.includes('[Pannello/CRITICO]') || l.includes('/ERROR]') || l.includes('Exception:') || l.includes('/WARN]') || l.includes('WARNING')) {
      countErrors++;
    }
    if (l.includes('<') && l.includes('>')) {
      countChat++;
    }
  }
  const elAll = document.getElementById('cfilter-count-all');
  const elErrors = document.getElementById('cfilter-count-errors');
  const elChat = document.getElementById('cfilter-count-chat');
  if (elAll) elAll.textContent = countAll;
  if (elErrors) elErrors.textContent = countErrors;
  if (elChat) elChat.textContent = countChat;
}

function lineMatchesConsoleFilter(line, type) {
  if (activeConsoleLevelFilter === 'errors') {
    if (type !== 'error' && type !== 'warn') return false;
  } else if (activeConsoleLevelFilter === 'chat') {
    if (type !== 'chat') return false;
  }
  if (consoleSearchQuery) {
    if (!line.toLowerCase().includes(consoleSearchQuery)) return false;
  }
  return true;
}

function appendLogLine(line) {
  const terminal = document.getElementById('console-terminal');
  if (!terminal) return;

  cachedConsoleLogs.push(line);
  if (cachedConsoleLogs.length > 1000) {
    cachedConsoleLogs.shift();
  }

  const div = document.createElement('div');
  div.className = 'log-line';

  let type = 'info';
  if (line.includes('[Pannello/ALLARME]') || line.includes('[Pannello/CRITICO]') || line.includes('/ERROR]') || line.includes('Exception:')) {
    div.classList.add('log-error');
    type = 'error';
  } else if (line.includes('/WARN]') || line.includes('WARNING')) {
    div.classList.add('log-warn');
    type = 'warn';
  } else if (line.includes('[Pannello]')) {
    div.classList.add('log-panel');
    type = 'panel';
  } else if (line.startsWith('>')) {
    div.classList.add('log-cmd');
    type = 'cmd';
  } else if (line.includes('<') && line.includes('>')) {
    div.classList.add('log-chat');
    type = 'chat';
  } else {
    div.classList.add('log-info');
    type = 'info';
  }
  div.dataset.logType = type;
  div.textContent = line;

  if (!lineMatchesConsoleFilter(line, type)) {
    div.style.display = 'none';
  }

  terminal.appendChild(div);

  const autoScrollEl = document.getElementById('chk-autoscroll');
  const autoScroll = autoScrollEl ? autoScrollEl.checked : true;
  if (autoScroll) {
    terminal.scrollTop = terminal.scrollHeight;
  }

  if (terminal.childElementCount > 1000) {
    terminal.removeChild(terminal.firstElementChild);
  }

  updateConsoleCounters();
}

function setConsoleLevelFilter(filter, btn) {
  activeConsoleLevelFilter = filter;
  document.querySelectorAll('.console-filter-chip').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');

  const terminal = document.getElementById('console-terminal');
  if (!terminal) return;
  const lines = terminal.querySelectorAll('.log-line');
  lines.forEach(div => {
    const text = div.textContent;
    const type = div.dataset.logType || 'info';
    div.style.display = lineMatchesConsoleFilter(text, type) ? 'block' : 'none';
  });

  const autoScrollEl = document.getElementById('chk-autoscroll');
  if (autoScrollEl && autoScrollEl.checked) {
    terminal.scrollTop = terminal.scrollHeight;
  }
}

function onConsoleSearchInput(val) {
  consoleSearchQuery = (val || '').toLowerCase().trim();
  const clearBtn = document.getElementById('console-search-clear');
  if (clearBtn) clearBtn.style.display = consoleSearchQuery ? 'inline-block' : 'none';

  const terminal = document.getElementById('console-terminal');
  if (!terminal) return;
  const lines = terminal.querySelectorAll('.log-line');
  lines.forEach(div => {
    const text = div.textContent;
    const type = div.dataset.logType || 'info';
    div.style.display = lineMatchesConsoleFilter(text, type) ? 'block' : 'none';
  });
}

function clearConsoleSearch() {
  const input = document.getElementById('console-search-input');
  if (input) {
    input.value = '';
    input.focus();
  }
  onConsoleSearchInput('');
}

function onAutoScrollToggle(enabled) {
  if (enabled) {
    const terminal = document.getElementById('console-terminal');
    if (terminal) terminal.scrollTop = terminal.scrollHeight;
    const btn = document.getElementById('btn-scroll-bottom');
    if (btn) btn.style.display = 'none';
  }
}

function onConsoleScroll() {
  const terminal = document.getElementById('console-terminal');
  const btn = document.getElementById('btn-scroll-bottom');
  if (!terminal || !btn) return;
  const isNearBottom = terminal.scrollHeight - terminal.scrollTop - terminal.clientHeight < 50;
  btn.style.display = isNearBottom ? 'none' : 'block';
}

function scrollConsoleToBottom() {
  const terminal = document.getElementById('console-terminal');
  if (terminal) {
    terminal.scrollTop = terminal.scrollHeight;
  }
  const btn = document.getElementById('btn-scroll-bottom');
  if (btn) btn.style.display = 'none';
}

function copyConsoleLogs() {
  const terminal = document.getElementById('console-terminal');
  if (!terminal || !terminal.innerText.trim()) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_log_copiare'), 'warning');
    return;
  }
  const text = terminal.innerText;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      if (typeof showToast === 'function') showToast(t('msg.log_console_copiati_negli_appunti'), 'success');
    }).catch(() => {
      copyRawText(text, null, t('msg.log_copiati'));
    });
  } else {
    copyRawText(text, null, t('msg.log_copiati'));
  }
}

function downloadConsoleLogs() {
  const terminal = document.getElementById('console-terminal');
  if (!terminal || !terminal.innerText.trim()) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_log_scaricare'), 'warning');
    return;
  }
  const text = terminal.innerText;
  const blob = new Blob([text], { type: 'text/plain;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  const dateStr = new Date().toISOString().slice(0, 10);
  a.download = `minecraft_console_${dateStr}.log`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
  if (typeof showToast === 'function') showToast(t('msg.file_log_scaricato_successo'), 'success');
}

async function fetchTerminalLogs() {
  try {
    const res = await fetch('/api/logs?tail=200');
    if (res.ok) {
      const data = await res.json();
      const logs = data.lines || data.logs || [];
      if (Array.isArray(logs) && logs.length > 0) {
        const terminal = document.getElementById('console-terminal');
        if (terminal && terminal.childElementCount === 0) {
          cachedConsoleLogs = [];
          logs.forEach(appendLogLine);
          terminal.scrollTop = terminal.scrollHeight;
        }
      }
    }
  } catch (err) {
    console.warn('Errore recupero log console:', err);
  }
}

async function pollConsoleLogs() {
  try {
    const res = await fetch(`/api/logs?since=${lastLogSeq}`);
    if (!res.ok) return;
    const data = await res.json();
    const terminal = document.getElementById('console-terminal');
    if (!terminal || !Array.isArray(data.lines)) return;
    if (data.reset) {
      terminal.innerHTML = '';
      cachedConsoleLogs = [];
    }
    data.lines.forEach(appendLogLine);
    lastLogSeq = data.seq || lastLogSeq;
  } catch (e) {}
}

// Chat senza WebSocket: si ridisegna solo se è aperta e se è arrivato qualcosa di nuovo
let lastChatSignature = '';
async function pollChatIfVisible() {
  const pane = document.getElementById('tab-chat');
  if (!pane || !pane.classList.contains('active')) return;
  try {
    const res = await fetch('/api/chat');
    if (!res.ok) return;
    const msgs = (await res.json()).messages || [];
    const last = msgs[msgs.length - 1] || {};
    const sig = `${msgs.length}:${last.id || last.time || ''}`;
    if (sig !== lastChatSignature) {
      lastChatSignature = sig;
      fetchChatHistory();
    }
  } catch (e) {}
}

function clearConsole() {
  fetch('/api/logs/clear', { method: 'POST' });
  cachedConsoleLogs = [];
  const terminal = document.getElementById('console-terminal');
  if (terminal) terminal.innerHTML = '';
  updateConsoleCounters();
  if (typeof showToast === 'function') showToast(t('msg.console_ripulita'), 'info');
}

function handleCommandSubmit(e) {
  e.preventDefault();
  const input = document.getElementById('cmd-input');
  const cmd = input.value.trim();
  if (!cmd) return;

  if (currentServerState !== 'ONLINE') {
    if (typeof showToast === 'function') {
      showToast(t('msg.server_online_stato_avvia_server', currentServerState || 'OFFLINE'), 'warning');
    }
    return;
  }

  commandHistory.push(cmd);
  historyIndex = commandHistory.length;

  sendServerCommand(cmd);
  input.value = '';
}

function sendQuickCommand(cmd) {
  if (currentServerState !== 'ONLINE') {
    if (typeof showToast === 'function') {
      showToast(t('msg.server_online_stato_avvia_server_2', currentServerState || 'OFFLINE'), 'warning');
    }
    return;
  }
  sendServerCommand(cmd);
  if (typeof showToast === 'function') {
    showToast(t('msg.inviato', cmd), 'info');
  }
}

function sendServerCommand(cmd) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ action: 'command', command: cmd }));
  } else {
    fetch('/api/server/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd })
    }).then(async r => {
      const data = await r.json();
      if (!r.ok || data.status === 'error') {
        if (typeof showToast === 'function') {
          showToast(`${data.message || t('msg.errore_esecuzione_comando')}`, 'warning');
        }
      }
    }).catch(e => {
      if (typeof showToast === 'function') {
        showToast(t('msg.errore_connessione_backend'), 'error');
      }
    });
  }
}

function setupCommandHistory() {
  const input = document.getElementById('cmd-input');
  if (!input) return;
  input.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowUp') {
      e.preventDefault();
      if (historyIndex > 0) {
        historyIndex--;
        input.value = commandHistory[historyIndex] || '';
        input.setSelectionRange(input.value.length, input.value.length);
      }
    } else if (e.key === 'ArrowDown') {
      e.preventDefault();
      if (historyIndex < commandHistory.length - 1) {
        historyIndex++;
        input.value = commandHistory[historyIndex] || '';
        input.setSelectionRange(input.value.length, input.value.length);
      } else {
        historyIndex = commandHistory.length;
        input.value = '';
      }
    }
  });
}

// =========================================================
// TAB 4: PLAYERS LIST
// =========================================================
function renderPlayersList(players) {
  const container = document.getElementById('players-list-container');
  const validPlayers = (players || []).filter(p => p && p.name && !['anonymous', 'anonymous player', 'anonimo', 'null', 'undefined', ''].includes(p.name.trim().toLowerCase()));
  if (!validPlayers || validPlayers.length === 0) {
    container.innerHTML = `
      <div class="empty-players">
        <div class="empty-icon"></div>
        <h3>${th('status.nessun_giocatore_online')}</h3>
        <p>${th('ui.giocatori_connessi_server_appariranno_qui')}</p>
      </div>
    `;
    return;
  }

  let html = '';
  validPlayers.forEach(p => {
    const joinedInfo = p.joined_str ? ` ${th('msg.entrato_alle', escapeHtml(p.joined_str))}` : '';
    html += `
      <div class="player-card">
        <div class="player-left">
          <img class="player-avatar" src="${p.avatar_url}" alt="${p.name}" onerror="this.src='data:image/svg+xml;utf8,<svg xmlns=\\'http://www.w3.org/2000/svg\\' width=\\'40\\' height=\\'40\\'><rect width=\\'40\\' height=\\'40\\' fill=\\'%232563eb\\'/></svg>'">
          <div class="player-info">
            <h4>${escapeHtml(p.name)}</h4>
            <p>${th('msg.connesso')} <b>${p.online_formatted}</b>${joinedInfo}</p>
          </div>
        </div>
        <div class="player-actions">
          <button class="btn btn-xs btn-emerald" onclick="openPlayerActionsModal('${escapeJsAttr(p.name)}', '${escapeJsAttr(p.avatar_url)}')" title="${th('ui.apri_pannello_azioni_rapide_cura')}">${th('ui.azioni')}</button>
          <button class="btn btn-xs btn-secondary" onclick="actionMsgPlayer('${escapeJsAttr(p.name)}')" title="${th('ui.invia_messaggio_privato')}">Msg</button>
          <button class="btn btn-xs btn-secondary" onclick="actionOpPlayer('${escapeJsAttr(p.name)}')" title="${th('ui.dai_privilegi_op')}">OP</button>
          <button class="btn btn-xs btn-ruby-outline" onclick="actionKickPlayer('${escapeJsAttr(p.name)}')" title="${th('ui.espelli_dal_server')}">${th('ui.espelli')}</button>
          <button class="btn btn-xs btn-ruby" onclick="actionQuickBanPlayer('${escapeJsAttr(p.name)}')" title="${th('ui.banna_permanentemente_dal_server')}">${th('ui.banna')}</button>
        </div>
      </div>
    `;
  });
  container.innerHTML = html;
}

function refreshPlayers() {
  fetchStatusHttp();
  loadWhitelist();
  loadBannedPlayers();
}

function actionKickPlayer(name) {
  const reason = prompt(t('msg.espellere_motivo_opzionale', name), "Espulso dall'amministratore");
  if (reason !== null) {
    sendServerCommand(`kick ${name} ${reason}`);
    if (typeof showToast === 'function') showToast(t('msg.espulsione_inviata', name), 'info');
    setTimeout(refreshPlayers, 1200);
  }
}

function actionQuickBanPlayer(name) {
  const reason = prompt(t('msg.bannare_permanentemente_dal_server_motivo', name), trDefault(t('pc.eco.violazione_regole_server')));
  if (reason !== null) {
    sendServerCommand(`ban ${name} ${reason}`);
    if (typeof showToast === 'function') showToast(t('msg.bannato_dal_server', name), 'warning');
    setTimeout(refreshPlayers, 1200);
  }
}

function actionOpPlayer(name) {
  if (confirm(t('msg.rendere_op', name))) {
    sendServerCommand(`op ${name}`);
    if (typeof showToast === 'function') showToast(t('msg.privilegi_op_assegnati', name), 'info');
  }
}

function actionMsgPlayer(name) {
  const msg = prompt(t('msg.messaggio_privato', name));
  if (msg) {
    sendServerCommand(`msg ${name} ${msg}`);
    if (typeof showToast === 'function') showToast(t('msg.messaggio_inviato', name), 'info');
  }
}

// =========================================================
// =========================================================
// SAFE API FETCH HELPER (PREVENTS HTML / DOCTYPE SYNTAX ERRORS)
// =========================================================
async function safeJsonFetch(url, options = {}) {
  const res = await fetch(url, options);
  const text = await res.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (err) {
    if (text.includes('<!DOCTYPE') || text.includes('<html')) {
      throw new Error(t('msg.server_o_connessione_internet_si'));
    }
    throw new Error(t('msg.risposta_valida_dal_server', res.status, text.slice(0, 80)));
  }
  return { ok: res.ok, status: res.status, data };
}

// =========================================================
// SERVER ACTIONS
// =========================================================
async function startServer() {
  clearErrorsUi();
  try {
    const { ok, data } = await safeJsonFetch('/api/server/start', { method: 'POST' });
    if (!ok || data.status === 'error') {
      alert(t('tpl.impossibile_avviare_server', data.message || data.detail || t('actions.errore_sconosciuto')));
    } else {
      if (typeof showToast === 'function') {
        showToast(t('actions.avvio_server_minecraft_corso'), 'info');
      }
      fetchStatusHttp();
    }
  } catch (e) {
    alert(t('tpl.errore', e.message || e));
  }
}

function openStopModal() {
  openModal('modal-stop');
}

function closeStopModal() {
  closeModal('modal-stop');
}

async function confirmStop(force = false) {
  closeStopModal();
  try {
    const endpoint = force ? '/api/server/kill' : '/api/server/stop';
    const { ok, data } = await safeJsonFetch(endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ force: force, reason: t('pc.console.arresto_pannello_controllo') })
    });
    if (!ok || data.status === 'error') {
      alert(t('tpl.errore_arresto_server', data.message || data.detail || t('actions.errore_sconosciuto')));
    } else {
      if (typeof showToast === 'function') {
        showToast(force ? t('actions.arresto_forzato_inviato') : t('actions.arresto_server_inviato'), 'info');
      }
      fetchStatusHttp();
    }
  } catch (e) {
    alert(t('tpl.errore', e.message || e));
  }
}

async function forceKillServer() {

  if (confirm(t('actions.sei_sicuro_voler_forzare_l'))) {

    try {

      await safeJsonFetch('/api/server/kill', { method: 'POST' });

      if (typeof showToast === 'function') {

        showToast(t('actions.arresto_forzato_inviato_2'), 'warning');

      }

      fetchStatusHttp();

    } catch (e) {

      safeJsonFetch('/api/server/stop', {

        method: 'POST',

        headers: { 'Content-Type': 'application/json' },

        body: JSON.stringify({ force: true, reason: t('pc.console.kill_forzato_pannello') })

      }).catch(() => {});

    }

  }

}

async function restartServer(countdown = 0) {
  clearErrorsUi();
  try {
    const { ok, data } = await safeJsonFetch('/api/server/restart', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ countdown: countdown, reason: t('pc.console.riavvio_manuale_pannello') })
    });
    if (!ok || data.status === 'error') {
      alert(t('tpl.errore_nel_riavvio', data.message || data.detail || t('actions.errore_sconosciuto')));
    } else {
      if (typeof showToast === 'function') {
        showToast(t('actions.riavvio_server_programmato'), 'info');
      }
      fetchStatusHttp();
    }
  } catch (e) {
    alert(t('tpl.errore_nel_riavvio', e.message || e));
  }
}

// Timer actions
async function postponeRestart(minutes = 30) {
  try {
    const res = await fetch('/api/timer/postpone', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ minutes: minutes })
    });
    if (res.ok) {
      if (typeof showToast === 'function') {
        showToast(t('tpl.riavvio_programmato_posticipato_minuti', minutes), 'success');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('actions.impossibile_posticipare_riavvio'), 'error');
      }
    }
    fetchStatusHttp();
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(t('actions.errore_connessione'), 'error');
    }
  }
}

async function resetTimer() {
  try {
    const res = await fetch('/api/timer/reset', { method: 'POST' });
    if (res.ok) {
      if (typeof showToast === 'function') {
        showToast(t('actions.timer_riavvio_reimpostato_ciclo_iniziale'), 'success');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('actions.impossibile_reimpostare_timer'), 'error');
      }
    }
    fetchStatusHttp();
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(t('actions.errore_connessione'), 'error');
    }
  }
}

async function toggleScheduledRestart(enabled) {
  try {
    const res = await fetch('/api/timer/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: enabled })
    });
    if (res.ok) {
      if (typeof showToast === 'function') {
        showToast(enabled ? t('actions.riavvio_programmato_ogni_4_ore') : t('actions.riavvio_programmato_disattivato'), 'info');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('actions.impossibile_modificare_stato_timer'), 'error');
      }
    }
    fetchStatusHttp();
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(t('actions.errore_connessione'), 'error');
    }
  }
}

async function resetCrashLock() {
  try {
    const banner = document.getElementById('crash-banner');
    if (banner) banner.style.display = 'none';
    const res = await fetch('/api/crash/reset-lock', { method: 'POST' });
    if (res.ok) {
      if (typeof showToast === 'function') {
        showToast(t('actions.protezione_sbloccata_successo'), 'success');
      }
    }
    fetchStatusHttp();
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(t('actions.errore_sblocco_protezione'), 'error');
    }
  }
}

// =========================================================
// SETTINGS MODAL
// =========================================================
async function openSettingsModal() {
  // Mobile / phone detection: hide Windows startup row on phones
  const isMobileClient = window.innerWidth <= 768 || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);
  const autoRow = document.getElementById('setting-row-autostart');
  if (autoRow) {
    autoRow.style.display = isMobileClient ? 'none' : 'block';
  }

  try {
    const res = await fetch('/api/config');
    if (res.ok) {
      const cfg = await res.json();
      document.getElementById('cfg-server-dir').value = cfg.server_dir || '';
      document.getElementById('cfg-server-run-bat').value = cfg.server_run_bat || '';
      document.getElementById('cfg-server-port').value = cfg.server_port || '';
      document.getElementById('cfg-restart-interval').value = cfg.scheduled_restart_interval_minutes || 240;
      if (document.getElementById('cfg-scheduled-restart')) {
        document.getElementById('cfg-scheduled-restart').checked = cfg.scheduled_restart_enabled !== false;
      }
      document.getElementById('cfg-chat-warnings').checked = cfg.chat_warnings_enabled !== false;
      document.getElementById('cfg-auto-restart-crash').checked = cfg.auto_restart_crash !== false;

      // Admin PIN & Security
      const pinInput = document.getElementById('cfg-admin-pin');
      const reqPinChk = document.getElementById('cfg-require-pin');
      const pinBadge = document.getElementById('cfg-pin-status-badge');
      if (pinInput) {
        pinInput.value = '';
        pinInput.placeholder = t('msg.lascia_vuoto_cambiarlo');
      }
      if (reqPinChk) reqPinChk.checked = cfg.require_pin_for_remote !== false;
      if (pinBadge) {
        if (!cfg.admin_pin_is_default && cfg.admin_pin_set) {
          pinBadge.textContent = t('msg.pin_personalizzato_protetto');
          pinBadge.className = 'badge badge-emerald';
        } else {
          pinBadge.textContent = t('msg.pin_predefinito_1234');
          pinBadge.className = 'badge badge-amber';
        }
      }
      // Push Notification Topic
      const pushTopicInput = document.getElementById('cfg-push-topic');
      if (pushTopicInput) pushTopicInput.value = cfg.push_topic || '';
    }
  } catch (e) {}

  // Fetch Windows autostart state only on PC
  if (!isMobileClient) {
    try {
      const resAuto = await fetch('/api/autostart');
      if (resAuto.ok) {
        const dataAuto = await resAuto.json();
        const chkAuto = document.getElementById('cfg-autostart-boot');
        if (chkAuto) chkAuto.checked = !!dataAuto.autostart;
      }
    } catch (e) {}
  }

  try {
    // Load JVM Tuning
    loadJvmTuning();

    // Load Eco Mode & Anti-Lag Watchdog
    if (currentStatus) {
      const eco = currentStatus.eco_mode || {};
      const chkEco = document.getElementById('cfg-eco-enabled');
      const selEco = document.getElementById('cfg-eco-minutes');
      if (chkEco) chkEco.checked = eco.enabled !== false;
      if (selEco) selEco.value = eco.minutes || 30;

      const al = currentStatus.anti_lag || {};
      const chkAl = document.getElementById('cfg-antilag-enabled');
      const chkClean = document.getElementById('cfg-antilag-autoclean');
      if (chkAl) chkAl.checked = al.enabled !== false;
      if (chkClean) chkClean.checked = al.auto_clean !== false;
    }

    openModal('modal-settings');
  } catch (e) {
    openModal('modal-settings');
  }
}

function closeSettingsModal() {
  closeModal('modal-settings');
}

async function saveSettings(e) {
  e.preventDefault();
  const isMobileClient = window.innerWidth <= 768 || /Android|iPhone|iPad|iPod|Mobile/i.test(navigator.userAgent);

  const schedChk = document.getElementById('cfg-scheduled-restart');
  const pinInput = document.getElementById('cfg-admin-pin');
  const reqPinChk = document.getElementById('cfg-require-pin');
  const pushTopicInput = document.getElementById('cfg-push-topic');

  const payload = {
    server_dir: document.getElementById('cfg-server-dir').value.trim(),
    server_run_bat: document.getElementById('cfg-server-run-bat').value.trim(),
    server_port: parseInt(document.getElementById('cfg-server-port').value, 10),
    web_port: 8080,
    scheduled_restart_enabled: schedChk ? schedChk.checked : true,
    scheduled_restart_interval_minutes: parseInt(document.getElementById('cfg-restart-interval').value, 10),
    chat_warnings_enabled: document.getElementById('cfg-chat-warnings').checked,
    auto_restart_crash: document.getElementById('cfg-auto-restart-crash').checked,
    crash_retry_delay_seconds: 8,
    max_crashes_in_window: 3,
    crash_window_seconds: 180,
    require_pin_for_remote: reqPinChk ? reqPinChk.checked : true,
    // Vuoto = lascia quello attuale (undefined non viene inviato)
    push_topic: (pushTopicInput && pushTopicInput.value.trim()) ? pushTopicInput.value.trim() : undefined
  };

  const newPinVal = pinInput ? pinInput.value.trim() : "";
  if (newPinVal) {
    // Stesse regole del PC (pin_rules.py): almeno 6 cifre, solo numeri
    if (!/^[0-9]{6,12}$/.test(newPinVal)) {
      showToast(t('msg.pin_deve_avere_6_12'), 'error');
      if (pinInput) pinInput.focus();
      return;
    }
    payload.admin_pin = newPinVal;
  }
  try {
    const res = await fetch('/api/config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const savedCfg = await res.clone().json().catch(() => null);
    if (!res.ok) {
      showToast('' + ((savedCfg && savedCfg.detail) || t('msg.impostazioni_salvate')), 'error');
      return;
    }
    if (savedCfg && savedCfg.config) renderDefaultPinBanner(savedCfg.config.admin_pin_is_default);

    // Save autostart state only if on PC
    if (!isMobileClient) {
      const autoEl = document.getElementById('cfg-autostart-boot');
      if (autoEl) {
        try {
          await fetch('/api/autostart', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ enabled: autoEl.checked })
          });
        } catch (ae) {}
      }
    }

    // Save Eco Mode
    const chkEco = document.getElementById('cfg-eco-enabled');
    const selEco = document.getElementById('cfg-eco-minutes');
    if (chkEco && selEco) {
      try {
        await fetch('/api/eco-mode', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enabled: chkEco.checked,
            minutes: parseInt(selEco.value, 10) || 30
          })
        });
      } catch (ee) {}
    }

    // Save Anti-Lag Watchdog
    const chkAl = document.getElementById('cfg-antilag-enabled');
    const chkClean = document.getElementById('cfg-antilag-autoclean');
    if (chkAl && chkClean) {
      try {
        await fetch('/api/anti-lag', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            enabled: chkAl.checked,
            auto_clean: chkClean.checked
          })
        });
      } catch (ale) {}
    }

    // Save JVM Tuning (se la RAM è rifiutata la finestra resta aperta con l'errore)
    if (!(await saveJvmTuningFromSettings())) return;

    if (res.ok) {
      closeSettingsModal();
      showToast(t('msg.impostazioni_configurazione_salvate_successo'), 'success');
      if (savedCfg && savedCfg.port_restart_needed) {
        showToast(t('msg.porta_cambiata_riavvia_server_minecraft'), 'info');
      }
      fetchStatusHttp();
      fetchInitialNetworkInfo();   // nuova porta nell'IP da copiare (il router la apre subito dopo)
    } else {
      const errData = await res.json().catch(() => ({}));
      showToast((t('msg.errore_salvataggio') + " ") + (errData.detail || res.statusText || res.status), 'error');
    }
  } catch (e) {
    showToast(t('msg.errore_salvataggio_impostazioni', e), 'error');
  }
}

function togglePinVisibility() {
  const pinInput = document.getElementById('cfg-admin-pin');
  if (!pinInput) return;
  pinInput.type = pinInput.type === 'password' ? 'text' : 'password';
}

async function sendTestPushNotification() {
  showToast(t('msg.invio_notifica_prova_corso'), 'info');
  try {
    const res = await fetch('/api/notifications/test', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'success') {
      showToast(t('msg.notifica_push_test_inviata_successo'), 'success');
    } else {
      showToast((t('msg.errore_invio_notifica') + " ") + (data.message || t('msg.errore')), 'error');
    }
  } catch (err) {
    showToast((t('msg.errore_comunicazione') + " ") + err.message, 'error');
  }
}

function resetSettingsToDefaults() {
  if (document.getElementById('cfg-server-port')) document.getElementById('cfg-server-port').value = 32769;
  if (document.getElementById('cfg-restart-interval')) document.getElementById('cfg-restart-interval').value = 240;
  if (document.getElementById('cfg-scheduled-restart')) document.getElementById('cfg-scheduled-restart').checked = true;
  if (document.getElementById('cfg-chat-warnings')) document.getElementById('cfg-chat-warnings').checked = true;
  if (document.getElementById('cfg-auto-restart-crash')) document.getElementById('cfg-auto-restart-crash').checked = true;
  if (document.getElementById('cfg-jvm-ram')) document.getElementById('cfg-jvm-ram').value = 8;
  if (document.getElementById('cfg-jvm-aikar')) document.getElementById('cfg-jvm-aikar').checked = true;
  if (document.getElementById('cfg-eco-enabled')) document.getElementById('cfg-eco-enabled').checked = true;
  if (document.getElementById('cfg-eco-minutes')) document.getElementById('cfg-eco-minutes').value = 30;
  if (document.getElementById('cfg-antilag-enabled')) document.getElementById('cfg-antilag-enabled').checked = true;
  if (document.getElementById('cfg-antilag-autoclean')) document.getElementById('cfg-antilag-autoclean').checked = true;
  if (document.getElementById('cfg-require-pin')) document.getElementById('cfg-require-pin').checked = true;
  showToast(t('msg.valori_consigliati_applicati_nel_form'), 'info');
}

// =========================================================
// CANVAS LIVE CHART
// =========================================================
function initCanvas() {
  const chartPref = localStorage.getItem('mc_live_chart_visible');
  if (chartPref === 'false') {
    const box = document.getElementById('collapsible-chart-box');
    const arrow = document.getElementById('chart-toggle-arrow');
    if (box) box.style.display = 'none';
    if (arrow) arrow.textContent = '▾';
  }
  if (metricsHistory.length === 0) {
    metricsHistory.push({ host_cpu: 0, host_ram_percent: 0, mc_ram_gb: 0, timestamp: Date.now() - 2000 });
    metricsHistory.push({ host_cpu: 0, host_ram_percent: 0, mc_ram_gb: 0, timestamp: Date.now() });
  }
  resizeCanvas();
}

function resizeCanvas() {
  if (!canvas || !ctx) return;
  const displayW = Math.max(200, Math.floor(canvas.clientWidth || 320));
  const displayH = 125;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);

  canvas.width = Math.floor(displayW * dpr);
  canvas.height = Math.floor(displayH * dpr);
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

  drawChart();
}

function drawChart() {
  if (!ctx || !canvas) return;
  if (metricsHistory.length === 0) return;
  if (document.hidden) return;

  const box = document.getElementById('collapsible-chart-box');
  if (box && box.style.display === 'none') return;
  const overviewGrid = document.querySelector('.overview-dashboard-grid');
  if (overviewGrid && overviewGrid.style.display === 'none') return;

  const w = Math.max(200, Math.floor(canvas.clientWidth || 320));
  const h = 125;
  const padding = { top: 12, bottom: 22, left: 34, right: 16 };
  const chartW = Math.max(10, w - padding.left - padding.right);
  const chartH = Math.max(10, h - padding.top - padding.bottom);

  ctx.clearRect(0, 0, w, h);

  // Background grid lines
  ctx.shadowBlur = 0;
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.08)';
  ctx.lineWidth = 1;
  for (let yVal = 0; yVal <= 100; yVal += 25) {
    const y = padding.top + chartH - (yVal / 100) * chartH;
    ctx.beginPath();
    ctx.moveTo(padding.left, y);
    ctx.lineTo(w - padding.right, y);
    ctx.stroke();

    ctx.fillStyle = '#8a8c92';
    ctx.font = '10px monospace';
    ctx.fillText(`${yVal}%`, 4, y + 3);
  }

  const n = metricsHistory.length;
  const stepX = chartW / Math.max(1, n - 1);

  function drawLine(getValueOrKey, color, maxScale = 100) {
    if (n === 0) return 0;
    ctx.strokeStyle = color;
    ctx.shadowColor = color;
    ctx.shadowBlur = 5;
    ctx.lineWidth = 2.5;
    ctx.beginPath();

    let lastX = padding.left;
    let lastY = padding.top + chartH;
    let lastVal = 0;

    metricsHistory.forEach((pt, idx) => {
      let rawVal = typeof getValueOrKey === 'function' ? getValueOrKey(pt) : (pt[getValueOrKey] || 0);
      const val = Number(rawVal) || 0;
      lastVal = val;
      const x = n === 1 ? padding.left + chartW : padding.left + idx * stepX;
      const y = padding.top + chartH - Math.min(1, Math.max(0, val / maxScale)) * chartH;
      if (idx === 0) {
        ctx.moveTo(padding.left, y);
      }
      ctx.lineTo(x, y);
      lastX = x;
      lastY = y;
    });
    ctx.stroke();

    // Draw indicator dot at latest point
    ctx.beginPath();
    ctx.arc(lastX, lastY, 3.5, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();

    return Math.round(lastVal);
  }

  // 1. Host CPU -> Blue / Sapphire (#4a8ad6)
  const curCpu = drawLine(pt => Number(pt.host_cpu !== undefined ? pt.host_cpu : (pt.cpu || 0)) || 0, '#f2f2f3', 100);

  // 2. Host RAM PC -> Amber (#d9a441)
  const curRam = drawLine(pt => {
    if (pt.host_ram_percent !== undefined && pt.host_ram_percent !== null) return Number(pt.host_ram_percent);
    if (pt.host_ram_pct !== undefined && pt.host_ram_pct !== null) return Number(pt.host_ram_pct);
    if (pt.host_ram_used_gb && pt.host_ram_total_gb) return (Number(pt.host_ram_used_gb) / Number(pt.host_ram_total_gb)) * 100;
    return 0;
  }, '#8a8c92', 100);

  // 3. Minecraft RAM -> Emerald (#7cb342)
  const curMcRam = drawLine(pt => {
    if (pt.mc_ram_pct !== undefined && pt.mc_ram_pct !== null && !isNaN(pt.mc_ram_pct)) return Number(pt.mc_ram_pct);
    if (pt.mc_ram_percent !== undefined && pt.mc_ram_percent !== null && !isNaN(pt.mc_ram_percent)) return Number(pt.mc_ram_percent);
    const maxRam = (pt.mc_ram_max_gb && pt.mc_ram_max_gb > 0) ? pt.mc_ram_max_gb : ((currentStatus && currentStatus.server_ram_max_gb) || 16.0);
    return maxRam > 0 ? (Number(pt.mc_ram_gb || 0) / maxRam) * 100 : 0;
  }, '#7cb342', 100);

  ctx.shadowBlur = 0;

  // Live Legend percentage numbers
  const legCpu = document.getElementById('chart-legend-cpu');
  const legRam = document.getElementById('chart-legend-ram');
  const legMcRam = document.getElementById('chart-legend-mc-ram');
  if (legCpu) legCpu.textContent = `${curCpu}%`;
  if (legRam) legRam.textContent = `${curRam}%`;
  if (legMcRam) {
    if (currentStatus && currentStatus.state === 'ONLINE') {
      legMcRam.textContent = `${curMcRam}%`;
    } else {
      legMcRam.textContent = '0% (Offline)';
    }
  }
}


// =========================================================
// UNIVERSAL CLIPBOARD (Android Bridge + API + ExecCommand Fallback)
// =========================================================
function copyRawText(text, btnElement, successMsg = t('header.copiato')) {
  if (!text) return;

  // 1. Android Native Bridge
  if (window.AndroidBridge && typeof window.AndroidBridge.copyToClipboard === 'function') {
    try {
      window.AndroidBridge.copyToClipboard(text);
      if (btnElement) {
        const orig = btnElement.innerHTML;
        btnElement.innerHTML = successMsg;
        btnElement.style.color = '#9ccc65';
        setTimeout(() => {
          btnElement.innerHTML = orig;
          btnElement.style.color = '';
        }, 1500);
      }
      return;
    } catch (e) {
      console.warn('[Bridge] Errore copia bridge Android:', e);
    }
  }

  // 2. Standard navigator.clipboard
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(() => {
      if (btnElement) {
        const orig = btnElement.innerHTML;
        btnElement.innerHTML = successMsg;
        btnElement.style.color = '#9ccc65';
        setTimeout(() => {
          btnElement.innerHTML = orig;
          btnElement.style.color = '';
        }, 1500);
      }
    }).catch(() => {
      fallbackExecCopy(text, btnElement, successMsg);
    });
  } else {
    // 3. Fallback execCommand
    fallbackExecCopy(text, btnElement, successMsg);
  }
}

function fallbackExecCopy(text, btnElement, successMsg) {
  try {
    const temp = document.createElement('textarea');
    temp.value = text;
    temp.style.position = 'fixed';
    temp.style.left = '-9999px';
    temp.style.top = '0';
    temp.setAttribute('readonly', '');
    document.body.appendChild(temp);
    temp.focus();
    temp.select();
    const successful = document.execCommand('copy');
    document.body.removeChild(temp);
    if (successful && btnElement) {
      const orig = btnElement.innerHTML;
      btnElement.innerHTML = successMsg;
      btnElement.style.color = '#9ccc65';
      setTimeout(() => {
        btnElement.innerHTML = orig;
        btnElement.style.color = '';
      }, 1500);
    }
  } catch (err) {
    console.error('Fallback execCommand fallito', err);
  }
}

function copyToClipboard(elementIdOrText) {
  let text = '';
  let btn = null;
  const el = document.getElementById(elementIdOrText);
  if (el) {
    text = (el.textContent || el.innerText || '').trim();
    btn = el.nextElementSibling || (el.parentElement ? el.parentElement.querySelector('.btn-copy, .btn-copy-highlight, .header-ip-copy-btn') : null);
  } else {
    text = String(elementIdOrText || '').trim();
  }
  copyRawText(text, btn, t('header.copiato'));
  if (typeof showToast === 'function') {
    showToast(t('tpl.copiato_negli_appunti', text), 'info');
  }
}

function escapeHtml(text) {
  if (!text) return '';
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}

// =========================================================
// BACKUP MANAGER (.ZIP TO DESKTOP WITH REAL-TIME PERCENTAGE)
// =========================================================
// Helpers for human-friendly backup metrics
function formatDurationHuman(seconds, pct = 0) {
  if (seconds === undefined || seconds === null || isNaN(seconds) || seconds <= 0) {
    return pct >= 99 ? t('msg.quasi_finito_finalizzazione') : t('actions.calcolo_corso');
  }
  const sec = Math.round(seconds);
  if (sec <= 0) return pct >= 99 ? t('msg.quasi_finito_finalizzazione') : t('actions.calcolo_corso');
  if (sec < 60) return t('msg.xs_rimanenti', sec);
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) {
    return s > 0 ? t('msg.xm_xs_rimanenti', m, s) : t('msg.xm_rimanenti', m);
  }
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return remM > 0 ? t('msg.xh_xm_rimanenti', h, remM) : t('msg.xh_rimanenti', h);
}

function formatElapsedHuman(seconds) {
  if (!seconds || seconds <= 0) return '0s';
  const sec = Math.round(seconds);
  if (sec < 60) return `${sec}s`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m < 60) {
    return s > 0 ? `${m}m ${s}s` : `${m}m`;
  }
  const h = Math.floor(m / 60);
  const remM = m % 60;
  return `${h}h ${remM}m ${s}s`;
}

function formatSizeHuman(mb) {
  if (!mb || mb <= 0) return '0 MB';
  if (mb >= 1024) {
    return `${(mb / 1024).toFixed(1)} GB`;
  }
  return `${mb.toFixed(1)} MB`;
}

function renderBackupStatus(backup) {
  if (!backup) return;
  currentBackupData = backup;

  const modal = document.getElementById('modal-backup');
  const idleView = document.getElementById('backup-idle-view');
  const progressView = document.getElementById('backup-progress-view');
  const cancelBtn = document.getElementById('btn-backup-cancel-run');
  const startBtn = document.getElementById('btn-backup-start-now');
  const closeBtn = document.getElementById('btn-backup-close');

  const percentBadge = document.getElementById('backup-percent-badge');
  const progressBar = document.getElementById('backup-progress-bar');
  const statusText = document.getElementById('backup-status-text');
  const sizeStat = document.getElementById('backup-size-stat');
  const filesStat = document.getElementById('backup-files-stat');
  const timeStat = document.getElementById('backup-time-stat');
  const pctMetric = document.getElementById('backup-pct-metric');
  const currentFile = document.getElementById('backup-current-file');
  const speedStat = document.getElementById('backup-speed-stat');
  const etaStat = document.getElementById('backup-eta-stat');

  const prevInfo = document.getElementById('backup-prev-info');
  const prevDetails = document.getElementById('backup-prev-details');
  const serverStateBadge = document.getElementById('backup-server-state-badge');
  const existingCard = document.getElementById('backup-existing-download-card');
  const existingInfo = document.getElementById('backup-existing-download-info');
  const downloadRow = document.getElementById('backup-download-action-row');

  if (serverStateBadge) {
    serverStateBadge.textContent = currentServerState;
    serverStateBadge.className = currentServerState === 'STOPPED' ? 'badge badge-emerald' : 'badge badge-amber';
  }

  if (backup.existing_backup_exists) {
    if (prevInfo) prevInfo.style.display = 'flex';
    if (prevDetails) prevDetails.textContent = t('msg.creato', formatSizeHuman(backup.existing_backup_size_mb || 0), backup.existing_backup_date || 'recente');
    if (existingCard) existingCard.style.display = 'flex';
    if (existingInfo) {
      existingInfo.textContent = t('msg.x_x_del_x', backup.target_filename || 'Backup_Server_Mc.zip', formatSizeHuman(backup.existing_backup_size_mb || 0), backup.existing_backup_date || t('msg.recente'));
    }
  } else {
    if (prevInfo) prevInfo.style.display = 'none';
    if (existingCard) existingCard.style.display = 'none';
  }

  // Handle Google Drive sync checkbox visibility and preference
  const cloudOptRow = document.getElementById('backup-cloud-opt-row');
  const cloudChk = document.getElementById('backup-copy-cloud-chk');
  if (cloudOptRow && cloudChk) {
    if (backup.cloud_sync_dir) {
      cloudOptRow.style.display = 'flex';
      const savedPref = localStorage.getItem('mc_backup_copy_to_cloud');
      if (savedPref !== null) {
        cloudChk.checked = savedPref === 'true';
      }
      cloudChk.onchange = () => {
        localStorage.setItem('mc_backup_copy_to_cloud', cloudChk.checked);
      };
    } else {
      cloudOptRow.style.display = 'none';
      cloudChk.checked = false;
    }
  }

  if (backup.active) {
    // Backup in progress!
    if (idleView) idleView.style.display = 'none';
    if (progressView) progressView.style.display = 'block';
    if (cancelBtn) {
      cancelBtn.style.display = 'inline-flex';
      cancelBtn.disabled = false;
      cancelBtn.textContent = t('ui.blocca_annulla_backup');
    }
    if (startBtn) startBtn.style.display = 'none';
    if (closeBtn) closeBtn.textContent = t('msg.riduci_icona');
    if (downloadRow) downloadRow.style.display = 'none';

    const pct = backup.percent || 0;
    if (percentBadge) percentBadge.textContent = `${pct}%`;
    if (progressBar) progressBar.style.width = `${pct}%`;
    if (statusText) statusText.textContent = backup.status || t('msg.compressione_corso');
    if (currentFile) currentFile.textContent = backup.current_file || '-';

    // Check if operation is a server restore
    const isRestoreOp = backup.operation === 'restore' || (backup.status && (backup.status.toLowerCase().includes('ripristino') || backup.status.toLowerCase().includes('estrazione')));
    if (isRestoreOp) {
      const rBox = document.getElementById('restore-progress-box');
      const rTitle = document.getElementById('restore-status-title');
      const rBar = document.getElementById('restore-progress-bar');
      const rPct = document.getElementById('restore-pct');
      const rFiles = document.getElementById('restore-files-stat');
      const rTime = document.getElementById('restore-time-stat');
      if (rBox) {
        rBox.style.display = 'block';
        if (rTitle) rTitle.textContent = backup.status;
        if (rBar) rBar.style.width = `${pct}%`;
        if (rPct) rPct.textContent = `${pct}%`;
        if (rFiles) rFiles.textContent = backup.current_file ? `File: ${backup.current_file}` : t('msg.file_estratti_2', backup.files_processed || 0);
        if (rTime && backup.elapsed_seconds) rTime.textContent = `${backup.elapsed_seconds}s`;
      }
    }

    // 1. Calculate / extract speed
    let speedMb = backup.speed_mb_s;
    if ((!speedMb || speedMb <= 0) && backup.elapsed_seconds > 0 && backup.processed_mb > 0) {
      speedMb = Number((backup.processed_mb / backup.elapsed_seconds).toFixed(1));
    }

    // 2. Calculate / extract ETA
    let etaText = backup.eta_formatted;
    if (backup.eta_seconds && backup.eta_seconds > 0) {
      etaText = formatDurationHuman(backup.eta_seconds, pct);
    } else if (backup.total_mb > 0 && backup.processed_mb > 0 && speedMb > 0) {
      const remMb = Math.max(0, backup.total_mb - backup.processed_mb);
      const remSec = remMb / speedMb;
      etaText = formatDurationHuman(remSec, pct);
    } else if (pct >= 99) {
      etaText = t('msg.quasi_finito_finalizzazione');
    } else if (!etaText || etaText === 'In attesa') {
      etaText = t('actions.calcolo_corso');
    }

    // Update Hero ETA card & speed
    if (etaStat) etaStat.textContent = etaText;
    if (speedStat) speedStat.textContent = speedMb > 0 ? `${speedMb} MB/s` : t('actions.calcolo');

    // Update 4-metric responsive grid
    if (sizeStat) sizeStat.textContent = `${formatSizeHuman(backup.processed_mb)} / ${formatSizeHuman(backup.total_mb)}`;
    if (filesStat) filesStat.textContent = `${(backup.files_processed || 0).toLocaleString()} / ${(backup.files_total || 0).toLocaleString()}`;
    if (timeStat) timeStat.textContent = formatElapsedHuman(backup.elapsed_seconds);
    if (pctMetric) pctMetric.textContent = `${pct}%`;

    // Update Top Live Banner (visible in all tabs)
    const liveBanner = document.getElementById('banner-backup-live');
    const bannerPct = document.getElementById('banner-backup-pct');
    const bannerDetails = document.getElementById('banner-backup-details');
    const bannerEta = document.getElementById('banner-backup-eta');
    if (liveBanner) liveBanner.style.display = 'block';
    if (bannerPct) bannerPct.textContent = `${pct}%`;
    if (bannerDetails) bannerDetails.textContent = t('msg.file', formatSizeHuman(backup.processed_mb), formatSizeHuman(backup.total_mb), (backup.files_processed || 0).toLocaleString(), (backup.files_total || 0).toLocaleString());
    if (bannerEta) bannerEta.textContent = t('tpl.mancano', etaText);
  } else {
    // Idle or finished
    const liveBanner = document.getElementById('banner-backup-live');
    if (liveBanner) liveBanner.style.display = 'none';

    stopBackupPolling();
    if (cancelBtn) cancelBtn.style.display = 'none';
    if (closeBtn) closeBtn.textContent = t('stopdlg.chiudi');

    if (backup.percent === 100) {
      if (idleView) idleView.style.display = 'none';
      if (progressView) progressView.style.display = 'block';
      if (startBtn) {
        startBtn.style.display = 'inline-flex';
        startBtn.innerHTML = `
          <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"></polyline><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"></path></svg>
          ${th('msg.esegui_nuovo_backup')}
        `;
      }
      if (percentBadge) percentBadge.textContent = '100%';
      if (progressBar) progressBar.style.width = '100%';
      let finMsg = `<b>${th('msg.backup_completato_successo')}</b> (${formatSizeHuman(backup.processed_mb)} in ${formatElapsedHuman(backup.elapsed_seconds)})`;
      if (backup.cloud_synced) {
        finMsg += `<br><span style="color: #9ccc65; font-size: 0.82rem;">${th('msg.sincronizzato_automaticamente_google_drive_cloud')}</span>`;
      }
      const isRestoreCompleted = backup.status && backup.status.toLowerCase().includes('ripristino');
      if (isRestoreCompleted) {
        const rBox = document.getElementById('restore-progress-box');
        const rTitle = document.getElementById('restore-status-title');
        const rBar = document.getElementById('restore-progress-bar');
        const rPct = document.getElementById('restore-pct');
        if (rBox) {
          rBox.style.display = 'block';
          if (rTitle) rTitle.textContent = t('msg.ripristino_completato_successo');
          if (rBar) rBar.style.width = '100%';
          if (rPct) rPct.textContent = '100%';
        }
        showToast(t('msg.ripristino_server_completato_successo'), 'success');
      }

      if (statusText) statusText.innerHTML = finMsg;
      if (currentFile) currentFile.textContent = t('msg.file_salvato_sul_desktop', backup.target_filename);
      if (etaStat) etaStat.textContent = (t('msg.completato') + " ");
      if (speedStat) speedStat.textContent = backup.speed_mb_s ? t('msg.mb_s_media', backup.speed_mb_s) : t('msg.completato');
      if (sizeStat) sizeStat.textContent = `${formatSizeHuman(backup.processed_mb)} (100%)`;
      if (filesStat) filesStat.textContent = `${(backup.files_processed || 0).toLocaleString()} file`;
      if (timeStat) timeStat.textContent = formatElapsedHuman(backup.elapsed_seconds);
      if (pctMetric) pctMetric.textContent = '100%';
      if (downloadRow) downloadRow.style.display = 'block';
    } else if (backup.error) {
      if (idleView) idleView.style.display = 'block';
      if (progressView) progressView.style.display = 'none';
      if (startBtn) startBtn.style.display = 'inline-flex';
      if (downloadRow) downloadRow.style.display = 'none';
      if (backup.error !== "Operazione annullata dall'utente.") {
        showToast(t('msg.errore_operazione_backup', backup.error), "error");
      }
    } else {
      if (idleView) idleView.style.display = 'block';
      if (progressView) progressView.style.display = 'none';
      if (startBtn) startBtn.style.display = 'inline-flex';
      if (downloadRow) downloadRow.style.display = 'none';
    }
  }
}

let backupPollingTimer = null;

function startBackupPolling() {
  if (backupPollingTimer) clearInterval(backupPollingTimer);
  backupPollingTimer = setInterval(async () => {
    try {
      const res = await fetch('/api/backup/status');
      if (res.ok) {
        const bData = await res.json();
        renderBackupStatus(bData);
        if (!bData.active) {
          stopBackupPolling();
          if (bData.percent === 100 && bData.completed_at && bData.completed_at !== window._lastWebNotifiedBackup) {
            window._lastWebNotifiedBackup = bData.completed_at;
            showToast(t('msg.backup_server_completato_successo', formatSizeHuman(bData.processed_mb)), 'success');
            if (window.Notification && Notification.permission === 'granted') {
              try {
                new Notification(t('msg.backup_server_completato'), {
                  body: t('msg.backup_terminato_successo', formatSizeHuman(bData.processed_mb)),
                  icon: '/static/minecraft_block.png'
                });
              } catch (_) {}
            }
          }
        }
      }
    } catch (e) {
      console.warn("Poll backup error:", e);
    }
  }, 400);
}

function stopBackupPolling() {
  if (backupPollingTimer) {
    clearInterval(backupPollingTimer);
    backupPollingTimer = null;
  }
}

// =========================================================
// BACKUP: PESO PREVISTO E SPAZIO LIBERO (Desktop e Google Drive)
// =========================================================
let lastBackupEstimate = null;

async function loadBackupEstimate() {
  const total = document.getElementById('backup-size-total');
  const desk = document.getElementById('backup-size-desktop');
  const cloud = document.getElementById('backup-size-cloud');
  if (!total) return;
  lastBackupEstimate = null;
  total.textContent = t('actions.calcolo');
  total.className = 'badge badge-sapphire';
  try {
    const res = await fetch('/api/backup/estimate');
    const e = await res.json();
    if (!res.ok) throw new Error(e.detail || `HTTP ${res.status}`);
    lastBackupEstimate = e;
    total.textContent = t('msg.circa_gb_file', e.total_gb, Number(e.files).toLocaleString('it-IT'));
    const line = (el, freeGb, fits, extra) => {
      if (!el) return;
      el.textContent = extra || (fits ? t('msg.gb_liberi_ci_sta', freeGb) : t('msg.gb_liberi_ci_sta_2', freeGb));
      el.className = extra ? 'backup-size-muted' : (fits ? 'backup-size-ok' : 'backup-size-bad');
    };
    line(desk, e.desktop_free_gb, e.desktop_fits, e.desktop_free_gb === null ? t('msg.spazio_leggibile') : '');
    if (!e.cloud_configured) line(cloud, null, true, t('msg.collegato_vedi_nota_qui_sotto'));
    else if (!e.cloud_available) line(cloud, null, true, t('msg.cartella_raggiungibile_google_drive_desktop'));
    else line(cloud, e.cloud_free_gb, e.cloud_fits, e.cloud_free_gb === null ? t('msg.spazio_leggibile') : '');
    if (!e.desktop_fits || (e.cloud_available && !e.cloud_fits)) total.className = 'badge badge-amber';
    showCloudBackupCheck(document.getElementById('backup-cloud-check'), e.cloud_check);
  } catch (err) {
    total.textContent = t('msg.calcolabile');
    if (desk) desk.textContent = '--';
    if (cloud) cloud.textContent = '--';
  }
}

// Copia su Google Drive mancante, più vecchia dell'ultimo backup sul Desktop o con un peso diverso
function cloudBackupCheckText(c) {
  if (!c) return '';
  if (c.problem === 'missing') return t('msg.backup_drive_mancante', c.desktop_date);
  if (c.problem === 'older') return t('msg.backup_drive_vecchio', c.cloud_date, c.desktop_date);
  return t('msg.backup_drive_peso_diverso', c.cloud_mb, c.desktop_mb);
}

function showCloudBackupCheck(el, check) {
  if (!el) return;
  const text = cloudBackupCheckText(check);
  el.textContent = text ? `⚠ ${text} ${t('msg.backup_drive_come_aggiornare')}` : '';
  el.style.display = text ? 'block' : 'none';
}

async function openBackupModal() {
  const modal = document.getElementById('modal-backup');
  loadBackupEstimate();
  const warn = document.getElementById('backup-server-running-warn');
  const startBtn = document.getElementById('btn-backup-start-now');
  const serverBadge = document.getElementById('backup-server-state-badge');

  if (serverBadge) {
    serverBadge.textContent = currentServerState;
    serverBadge.className = currentServerState === 'STOPPED' ? 'badge badge-emerald' : 'badge badge-amber';
  }

  // Check if server is running: MUST BE STOPPED TO AVOID CORRUPTING FILES
  if (currentServerState !== 'STOPPED') {
    if (warn) warn.style.display = 'flex';
    if (startBtn) {
      startBtn.disabled = true;
      startBtn.title = t('msg.spegni_prima_server_abilitare_backup');
    }
  } else {
    if (warn) warn.style.display = 'none';
    if (startBtn) {
      startBtn.disabled = false;
      startBtn.title = "";
    }
  }

  // Fetch current backup status
  try {
    const res = await fetch('/api/backup/status');
    if (res.ok) {
      const bData = await res.json();
      renderBackupStatus(bData);
      if (bData && bData.active) {
        startBackupPolling();
      }
    }
  } catch (e) {}

  loadCloudBackupConfig();
  openModal('modal-backup');
}

function closeBackupModal() {
  closeModal('modal-backup');
}

async function triggerServerBackup() {
  if (currentServerState !== 'STOPPED') {
    showToast(t('msg.server_minecraft_deve_essere_completamente'), "warning");
    return;
  }

  const idleView = document.getElementById('backup-idle-view');
  const progressView = document.getElementById('backup-progress-view');
  const startBtn = document.getElementById('btn-backup-start-now');
  const cancelBtn = document.getElementById('btn-backup-cancel-run');
  const statusText = document.getElementById('backup-status-text');
  const downloadRow = document.getElementById('backup-download-action-row');
  const cloudChk = document.getElementById('backup-copy-cloud-chk');
  const copyCloud = cloudChk && cloudChk.checked;

  const cloudText = copyCloud ? (" " + t('msg.anche_quello_google_drive')) : '';
  // Avviso se il backup non ci sta (peso calcolato all'apertura della finestra)
  const est = lastBackupEstimate;
  let spaceWarn = '';
  if (est && !est.desktop_fits) spaceWarn += t('msg.attenzione_sul_desktop_c_abbastanza', est.total_gb);
  if (est && copyCloud && !est.cloud_available) spaceWarn += ("\n\n" + t('msg.attenzione_cartella_google_drive_raggiungibile'));
  else if (est && copyCloud && !est.cloud_fits) spaceWarn += t('msg.attenzione_google_drive_c_abbastanza', est.total_gb);
  if (!confirm(t('msg.backup_sul_desktop_verra_sostituitox', cloudText, spaceWarn))) {
    return;
  }

  if (idleView) idleView.style.display = 'none';
  if (progressView) progressView.style.display = 'block';
  if (startBtn) startBtn.style.display = 'none';
  if (downloadRow) downloadRow.style.display = 'none';
  if (cancelBtn) {
    cancelBtn.style.display = 'inline-flex';
    cancelBtn.disabled = false;
    cancelBtn.textContent = t('ui.blocca_annulla_backup');
  }
  if (statusText) statusText.textContent = t('msg.verifica_ed_eliminazione_vecchio_backup');

  try {
    const { ok, data } = await safeJsonFetch('/api/backup/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ copy_to_cloud: !!copyCloud })
    });
    if (ok && data.status !== 'error') {
      startBackupPolling();
      showToast(t('msg.creazione_backup_avviata_background'), "info");
    } else {
      showToast(`${data.detail || data.message || t('msg.errore_avvio_backup')}`, "error");
      if (idleView) idleView.style.display = 'block';
      if (progressView) progressView.style.display = 'none';
      if (startBtn) startBtn.style.display = 'inline-flex';
      if (cancelBtn) cancelBtn.style.display = 'none';
      stopBackupPolling();
    }
  } catch (e) {
    showToast(t('msg.impossibile_avviare_backup', e.message || e), "error");
    if (idleView) idleView.style.display = 'block';
    if (progressView) progressView.style.display = 'none';
    if (startBtn) startBtn.style.display = 'inline-flex';
    if (cancelBtn) cancelBtn.style.display = 'none';
    stopBackupPolling();
  }
}

async function detectGoogleDrivePath() {
  const input = document.getElementById('cfg-cloud-sync-dir');
  const box = document.getElementById('cloud-sync-suggestions');
  try {
    const res = await fetch('/api/backup/detect-cloud-drives');
    if (res.ok) {
      const data = await res.json();
      if (data.found && data.drives && data.drives.length > 0) {
        const top = data.drives[0];
        if (input) input.value = top.recommended_path;
        if (box) {
          box.innerHTML = '';
          data.drives.forEach(d => {
            const btn = document.createElement('button');
            btn.type = 'button';
            btn.className = 'btn btn-xs btn-secondary';
            btn.style.fontSize = '11px';
            btn.textContent = t('msg.usa', d.name);
            btn.onclick = () => {
              if (input) input.value = d.recommended_path;
              saveCloudBackupConfig();
            };
            box.appendChild(btn);
          });
        }
        showToast(t('msg.rilevato_clicca_salva_confermare', top.name), 'success');
      } else {
        // Fallback default Google Drive on G:
        if (input) input.value = "G:\\Il mio Drive\\Backup Minecraft";
        showToast(t('msg.impostato_percorso_predefinito_google_drive'), 'info');
      }
    } else {
      if (input) input.value = "G:\\Il mio Drive\\Backup Minecraft";
    }
  } catch (e) {
    if (input) input.value = "G:\\Il mio Drive\\Backup Minecraft";
  }
}

async function cancelBackupProcess() {
  if (!confirm(t('msg.sei_sicuro_voler_bloccare_ed'))) {
    return;
  }
  const cancelBtn = document.getElementById('btn-backup-cancel-run');
  const statusText = document.getElementById('backup-status-text');
  if (cancelBtn) {
    cancelBtn.disabled = true;
    cancelBtn.textContent = t('msg.blocco_corso');
  }
  if (statusText) {
    statusText.textContent = t('msg.blocco_immediato_backup_corso');
  }

  try {
    await fetch('/api/backup/cancel', { method: 'POST' });
  } catch (_) {}

  // Fallback rapido di sicurezza: forza la chiusura dell'archivio se il worker non ha già interrotto
  setTimeout(async () => {
    try {
      await fetch('/api/backup/force-cancel', { method: 'POST' });
    } catch (_) {}
    stopBackupPolling();
    const res = await safeJsonFetch('/api/backup/status');
    if (res.ok && res.data) renderBackupStatus(res.data);
    showToast(t('msg.creazione_backup_bloccata_annullata_successo'), "info");
  }, 350);
}

// =========================================================
// MANAGED IN-BROWSER DOWNLOAD SYSTEM WITH ABORT SUPPORT
// =========================================================
let activeDownloadAbortController = null;

function abortManagedDownload() {
  if (activeDownloadAbortController) {
    activeDownloadAbortController.abort();
    activeDownloadAbortController = null;
    showToast(t('msg.download_file_bloccato_annullato'), "warning");
    const activeCard = document.getElementById('download-active-card');
    if (activeCard) activeCard.style.display = 'none';
  }
}

async function downloadDesktopBackupDirectly() {
  return startManagedDownload('/api/backup/download', 'Backup_Server_Mc.zip');
}

async function downloadBackupItem(id, filename) {
  return startManagedDownload(`/api/backup/download-item?id=${encodeURIComponent(id)}`, filename || 'Backup_Server_Mc.zip');
}

async function startManagedDownload(url, filename) {
  if (activeDownloadAbortController) {
    if (!confirm(t('msg.altro_download_gia_attivo_nel'))) {
      return;
    }
    abortManagedDownload();
  }

  const activeCard = document.getElementById('download-active-card');
  const pFill = document.getElementById('download-progress-fill');
  const pTxt = document.getElementById('download-status-txt');
  const pPct = document.getElementById('download-pct-txt');

  if (activeCard) activeCard.style.display = 'block';
  if (pFill) pFill.style.width = '0%';
  if (pPct) pPct.textContent = '0%';
  if (pTxt) pTxt.textContent = t('msg.connessione_scaricare', filename);

  activeDownloadAbortController = new AbortController();
  const signal = activeDownloadAbortController.signal;

  try {
    const response = await fetch(url, { signal });
    if (!response.ok) {
      throw new Error(t('msg.errore_server_http', response.status, response.statusText));
    }

    const contentLength = response.headers.get('content-length');
    const totalBytes = contentLength ? parseInt(contentLength, 10) : 0;
    let receivedBytes = 0;

    const reader = response.body.getReader();
    const chunks = [];

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      receivedBytes += value.length;

      if (totalBytes > 0) {
        const pct = Math.min(100, Math.round((receivedBytes / totalBytes) * 100));
        if (pFill) pFill.style.width = `${pct}%`;
        if (pPct) pPct.textContent = `${pct}%`;
        if (pTxt) pTxt.textContent = `${(receivedBytes / (1024 * 1024)).toFixed(1)} / ${(totalBytes / (1024 * 1024)).toFixed(1)} MB (${pct}%)`;
      } else {
        if (pTxt) pTxt.textContent = t('msg.mb_scaricati', (receivedBytes / (1024 * 1024)).toFixed(1));
      }
    }

    if (pTxt) pTxt.textContent = t('msg.salvataggio_file_corso');
    const blob = new Blob(chunks, { type: 'application/zip' });
    const blobUrl = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = blobUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    setTimeout(() => {
      URL.revokeObjectURL(blobUrl);
      a.remove();
    }, 2000);

    showToast(t('msg.download_completato', filename), 'success');
  } catch (err) {
    if (err.name === 'AbortError') {
      console.log("Download interrotto dall'utente.");
    } else {
      alert(t('msg.errore_durante_download', err.message || err));
    }
  } finally {
    activeDownloadAbortController = null;
    setTimeout(() => {
      if (activeCard && !activeDownloadAbortController) {
        activeCard.style.display = 'none';
      }
    }, 2500);
  }
}

async function stopServerFromBackupModal() {
  if (confirm(t('msg.vuoi_arrestare_server_minecraft_adesso'))) {
    closeBackupModal();
    confirmStop(false);
  }
}

// =========================================================
// MINECRAFT COMMANDS LIST & INTERACTIVE RUNNER
// =========================================================
let onlinePlayerNames = [];
let pinnedCommands = [];
try {
  const savedPins = localStorage.getItem('mc_pinned_commands');
  if (savedPins) pinnedCommands = JSON.parse(savedPins);
  if (!Array.isArray(pinnedCommands)) pinnedCommands = [];
} catch (e) {
  pinnedCommands = [];
}

function togglePinCommand(cmdId, event) {
  if (event) event.stopPropagation();
  const idx = pinnedCommands.indexOf(cmdId);
  if (idx >= 0) {
    pinnedCommands.splice(idx, 1);
    if (typeof showToast === 'function') showToast(t('msg.rimosso_dai_preferiti'), 'info');
  } else {
    pinnedCommands.push(cmdId);
    if (typeof showToast === 'function') showToast(t('msg.aggiunto_ai_preferiti'), 'success');
  }
  try {
    localStorage.setItem('mc_pinned_commands', JSON.stringify(pinnedCommands));
  } catch (e) {}
  renderCommands();
}

function updateOnlinePlayerNames(list) {
  if (Array.isArray(list)) {
    const names = list.map(p => (typeof p === 'string' ? p : p.name)).filter(Boolean);
    if (JSON.stringify(names) !== JSON.stringify(onlinePlayerNames)) {
      onlinePlayerNames = names;
      const pane = document.getElementById('tab-commands');
      if (pane && pane.classList.contains('active')) {
        const activeEl = document.activeElement;
        const isTyping = activeEl && (activeEl.tagName === 'INPUT' || activeEl.tagName === 'SELECT') && pane.contains(activeEl);
        if (!isTyping) {
          renderCommands();
        }
      }
    }
  }
}

const FULL_MINECRAFT_COMMANDS = [
  // ==========================================
  // 1. AMMINISTRAZIONE & GESTIONE SERVER
  // ==========================================
  {
    id: "cmd_tps",
    name: "forge tps / tps",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.visualizza_tick_second_tps_durata'),
    template: (p) => p.flavor === 'forge' ? 'forge tps' : 'tps',
    params: [
      {
        id: "flavor",
        type: "select",
        label: t('msg.comando'),
        default: "vanilla",
        options: [
          { val: "forge", label: "forge tps (Server Forge / NeoForge)" },
          { val: "vanilla", label: "tps (Paper / Spigot / Fabric)" }
        ]
      }
    ]
  },
  {
    id: "cmd_op",
    name: "op",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.assegna_tutti_permessi_operatore_amministratore'),
    template: (p) => `op ${p.target || '@p'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" }
    ]
  },
  {
    id: "cmd_deop",
    name: "deop",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.revoca_permessi_operatore_giocatore_specificato'),
    template: (p) => `deop ${p.target || '@p'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" }
    ]
  },
  {
    id: "cmd_kick",
    name: "kick",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.disconnette_forzatamente_giocatore_dal_server'),
    template: (p) => `kick ${p.target || '@p'} ${p.reason || 'Disconnesso da admin'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "reason", type: "text", label: t('msg.motivo'), default: t('defaults.disconnesso_dall_amministratore') }
    ]
  },
  {
    id: "cmd_ban",
    name: "ban",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.banna_giocatore_dal_server_impedendogli'),
    template: (p) => `ban ${p.target || '@p'} ${p.reason || trDefault(t('defaults.violazione_regole'))}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "reason", type: "text", label: t('msg.motivo'), default: t('pc.eco.violazione_regole_server') }
    ]
  },
  {
    id: "cmd_pardon",
    name: "pardon",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.rimuove_ban_giocatore_consentendogli_entrare'),
    template: (p) => `pardon ${p.target || 'NomePlayer'}`,
    params: [
      { id: "target", type: "text", label: t('msg.nome_player'), default: "NomePlayer" }
    ]
  },
  {
    id: "cmd_ban_ip",
    name: "ban-ip",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.banna_l_indirizzo_ip_giocatore'),
    template: (p) => `ban-ip ${p.target || '@p'} ${p.reason || 'Ban IP da admin'}`,
    params: [
      { id: "target", type: "player", label: "Player / IP", default: "@p" },
      { id: "reason", type: "text", label: t('msg.motivo'), default: t('msg.ban_ip_permanente') }
    ]
  },
  {
    id: "cmd_pardon_ip",
    name: "pardon-ip",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.rimuove_ban_indirizzo_ip_precedentemente'),
    template: (p) => `pardon-ip ${p.ip || '192.168.1.1'}`,
    params: [
      { id: "ip", type: "text", label: t('msg.indirizzo_ip'), default: "192.168.1.50" }
    ]
  },
  {
    id: "cmd_whitelist_toggle",
    name: "whitelist (on/off)",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.attiva_o_disattiva_whitelist_rendere'),
    template: (p) => `whitelist ${p.mode || 'on'}`,
    params: [
      { id: "mode", type: "select", label: t('msg.stato'), options: [
        { label: t('msg.attiva_on_solo_utenti_lista'), val: "on" },
        { label: t('msg.disattiva_off_accesso_libero'), val: "off" }
      ], default: "on" }
    ]
  },
  {
    id: "cmd_whitelist_manage",
    name: "whitelist (add/remove)",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.aggiunge_o_rimuove_giocatore_dalla'),
    template: (p) => `whitelist ${p.action || 'add'} ${p.player || 'NomePlayer'}`,
    params: [
      { id: "action", type: "select", label: t('msg.azione'), options: [
        { label: t('msg.aggiungi_add'), val: "add" },
        { label: t('msg.rimuovi_remove'), val: "remove" }
      ], default: "add" },
      { id: "player", type: "text", label: t('msg.nome_player'), default: "NomePlayer" }
    ]
  },
  {
    id: "cmd_whitelist_list",
    name: "whitelist list",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.mostra_nella_console_tutti_giocatori'),
    template: () => `whitelist list`,
    params: []
  },
  {
    id: "cmd_save_all",
    name: "save-all",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.forza_salvataggio_immediato_tutti_mondi'),
    template: () => `save-all`,
    params: []
  },
  {
    id: "cmd_save_off",
    name: "save-off",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.sospende_salvataggio_automatico_usato_dai'),
    template: () => `save-off`,
    params: []
  },
  {
    id: "cmd_save_on",
    name: "save-on",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.riabilita_salvataggio_automatico_periodico_mondo'),
    template: () => `save-on`,
    params: []
  },
  {
    id: "cmd_reload",
    name: "reload",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.ricarica_datapack_funzioni_ricette_loot'),
    template: () => `reload`,
    params: []
  },
  {
    id: "cmd_stop",
    name: "stop",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.arresta_server_minecraft_modo_pulito'),
    template: () => `stop`,
    params: []
  },
  {
    id: "cmd_say",
    name: "say",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.invia_annuncio_broadcast_visibile_tutti'),
    template: (p) => `say ${p.msg || 'Messaggio importante del Server'}`,
    params: [
      { id: "msg", type: "text", label: t('msg.messaggio'), default: t('defaults.benvenuti_sul_server') }
    ]
  },
  {
    id: "cmd_msg",
    name: "tell / msg",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.invia_messaggio_privato_giocatore_specifico'),
    template: (p) => `tell ${p.target || '@p'} ${p.msg || 'Ciao!'}`,
    params: [
      { id: "target", type: "player", label: t('msg.destinatario'), default: "@p" },
      { id: "msg", type: "text", label: t('msg.messaggio'), default: t('defaults.ciao_questo_messaggio_privato_console') }
    ]
  },
  {
    id: "cmd_list",
    name: "list",
    category: "admin",
    catName: t('ui.amministrazione_2'),
    desc: t('msg.mostra_l_elenco_conteggio_tutti'),
    template: () => `list`,
    params: []
  },

  // ==========================================
  // 2. MONDO, TEMPO & REGOLE (GAMERULES)
  // ==========================================
  {
    id: "cmd_time_set",
    name: "time set",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.imposta_l_orario_mondo_alba'),
    template: (p) => `time set ${p.time || 'day'}`,
    params: [
      { id: "time", type: "select", label: t('msg.orario'), options: [
        { label: t('msg.giorno_mattina_1000'), val: "day" },
        { label: t('msg.mezzogiorno_6000'), val: "noon" },
        { label: t('msg.tramonto_12000'), val: "12000" },
        { label: t('msg.notte_13000'), val: "night" },
        { label: t('msg.mezzanotte_18000'), val: "midnight" }
      ], default: "day" }
    ]
  },
  {
    id: "cmd_time_add",
    name: "time add",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.fa_avanzare_tempo_mondo_determinato'),
    template: (p) => `time add ${p.ticks || 6000}`,
    params: [
      { id: "ticks", type: "number", label: t('msg.tick_sommare'), default: 6000, min: 100, max: 100000 }
    ]
  },
  {
    id: "cmd_weather",
    name: "weather",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.modifica_meteo_mondo_durata_opzionale'),
    template: (p) => `weather ${p.type || 'clear'} ${p.duration ? p.duration + 's' : ''}`.trim(),
    params: [
      { id: "type", type: "select", label: t('msg.meteo'), options: [
        { label: t('msg.sereno_clear'), val: "clear" },
        { label: t('msg.pioggia_rain'), val: "rain" },
        { label: t('msg.temporale_thunder'), val: "thunder" }
      ], default: "clear" },
      { id: "duration", type: "number", label: t('msg.secondi_opzionale'), default: 3600, min: 10, max: 86400 }
    ]
  },
  {
    id: "cmd_difficulty",
    name: "difficulty",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.imposta_livello_difficolta_server'),
    template: (p) => `difficulty ${p.diff || 'normal'}`,
    params: [
      { id: "diff", type: "select", label: t('msg.difficolta'), options: [
        { label: t('msg.pacifica_peaceful_no_mostri_vita'), val: "peaceful" },
        { label: t('ui.facile_easy'), val: "easy" },
        { label: t('ui.normale_normal'), val: "normal" },
        { label: t('ui.difficile_hard'), val: "hard" }
      ], default: "normal" }
    ]
  },
  {
    id: "cmd_defaultgamemode",
    name: "defaultgamemode",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.imposta_modalita_gioco_predefinita_assegnata'),
    template: (p) => `defaultgamemode ${p.mode || 'survival'}`,
    params: [
      { id: "mode", type: "select", label: t('msg.modalita'), options: [
        { label: t('ui.sopravvivenza_survival'), val: "survival" },
        { label: t('ui.creativa_creative'), val: "creative" },
        { label: t('ui.avventura_adventure'), val: "adventure" },
        { label: t('ui.spettatore_spectator'), val: "spectator" }
      ], default: "survival" }
    ]
  },
  {
    id: "cmd_rule_keepinv",
    name: "gamerule keepInventory",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.mantiene_l_inventario_livelli_exp'),
    template: (p) => `gamerule keepInventory ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.stato'), options: [
        { label: t('msg.attivo_true_oggetti_conservati_alla'), val: "true" },
        { label: t('msg.disattivo_false_oggetti_cadono_terra'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_mobgriefing",
    name: "gamerule mobGriefing",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.impedisce_creeper_enderman_ghast_wither'),
    template: (p) => `gamerule mobGriefing ${p.val || 'false'}`,
    params: [
      { id: "val", type: "select", label: t('msg.stato'), options: [
        { label: t('msg.protetto_false_blocchi_intatti_dopo'), val: "false" },
        { label: t('msg.distruzione_attiva_true_mob_rompono'), val: "true" }
      ], default: "false" }
    ]
  },
  {
    id: "cmd_rule_firetick",
    name: "gamerule doFireTick",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.impedisce_fuoco_propagarsi_distruggere_costruzio'),
    template: (p) => `gamerule doFireTick ${p.val || 'false'}`,
    params: [
      { id: "val", type: "select", label: t('msg.stato'), options: [
        { label: t('msg.fuoco_fermo_false_fuoco_si'), val: "false" },
        { label: t('msg.propagazione_attiva_true_fuoco_brucia'), val: "true" }
      ], default: "false" }
    ]
  },
  {
    id: "cmd_rule_sleeping",
    name: "gamerule playersSleepingPercentage",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.percentuale_giocatori_devono_dormire_passare'),
    template: (p) => `gamerule playersSleepingPercentage ${p.pct || 1}`,
    params: [
      { id: "pct", type: "number", label: t('msg.percentuale_1_1_solo_player'), default: 1, min: 0, max: 100 }
    ]
  },
  {
    id: "cmd_rule_daylight",
    name: "gamerule doDaylightCycle",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.attiva_o_blocca_l_alternarsi'),
    template: (p) => `gamerule doDaylightCycle ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.ciclo_solare'), options: [
        { label: t('msg.ciclo_attivo_true'), val: "true" },
        { label: t('msg.orario_bloccato_false'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_weather",
    name: "gamerule doWeatherCycle",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.attiva_o_blocca_cambiamento_naturale'),
    template: (p) => `gamerule doWeatherCycle ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.ciclo_meteo'), options: [
        { label: t('msg.meteo_dinamico_attivo_true'), val: "true" },
        { label: t('msg.meteo_bloccato_false'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_mobspawn",
    name: "gamerule doMobSpawning",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.abilita_o_disabilita_comparsa_naturale'),
    template: (p) => `gamerule doMobSpawning ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.spawn_mob'), options: [
        { label: t('msg.spawn_naturale_attivo_true'), val: "true" },
        { label: t('msg.nessun_mostro_naturale_false'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_tickspeed",
    name: "gamerule randomTickSpeed",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.velocita_crescita_grano_alberi_canna'),
    template: (p) => `gamerule randomTickSpeed ${p.speed || 3}`,
    params: [
      { id: "speed", type: "number", label: t('msg.velocita_default_3_rapido_20'), default: 3, min: 0, max: 1000 }
    ]
  },
  {
    id: "cmd_rule_nat_regen",
    name: "gamerule naturalRegeneration",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.se_disattivata_salute_si_rigenera'),
    template: (p) => `gamerule naturalRegeneration ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.rigenerazione'), options: [
        { label: t('msg.normale_true'), val: "true" },
        { label: t('msg.disattivata_stile_uhc_false'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_fall_dmg",
    name: "gamerule fallDamage",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.attiva_o_disattiva_danni_caduta'),
    template: (p) => `gamerule fallDamage ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.danno_caduta'), options: [
        { label: t('msg.attivo_true'), val: "true" },
        { label: t('msg.disattivato_false_niente_danni_caduta'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_fire_dmg",
    name: "gamerule fireDamage",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.attiva_o_disattiva_danni_fuoco'),
    template: (p) => `gamerule fireDamage ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.danno_fuoco'), options: [
        { label: t('msg.attivo_true'), val: "true" },
        { label: t('msg.disattivato_false_immuni_fuoco'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_drown_dmg",
    name: "gamerule drowningDamage",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.attiva_o_disattiva_soffocamento_danno'),
    template: (p) => `gamerule drowningDamage ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.danno_annegamento'), options: [
        { label: t('msg.attivo_true'), val: "true" },
        { label: t('msg.disattivato_false_respiro_infinito'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_death_msg",
    name: "gamerule showDeathMessages",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.mostra_o_nasconde_messaggi_morte'),
    template: (p) => `gamerule showDeathMessages ${p.val || 'true'}`,
    params: [
      { id: "val", type: "select", label: t('msg.messaggi_morte'), options: [
        { label: t('msg.mostra_chat_true'), val: "true" },
        { label: t('msg.nascondi_false'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_rule_cramming",
    name: "gamerule maxEntityCramming",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.numero_massimo_entita_possono_stare'),
    template: (p) => `gamerule maxEntityCramming ${p.val !== undefined ? p.val : 24}`,
    params: [
      { id: "val", type: "number", label: t('msg.limite_mob_0_nessun_limite'), default: 24, min: 0, max: 200 }
    ]
  },
  {
    id: "cmd_worldborder_set",
    name: "worldborder set",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.imposta_diametro_confine_mondo_worldborder'),
    template: (p) => `worldborder set ${p.size || 10000}`,
    params: [
      { id: "size", type: "number", label: t('msg.diametro_blocchi'), default: 10000, min: 100, max: 60000000 }
    ]
  },
  {
    id: "cmd_worldborder_add",
    name: "worldborder add",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.aumenta_o_diminuisce_diametro_barriera'),
    template: (p) => `worldborder add ${p.distance || '1000'} ${p.time || '10'}`,
    params: [
      { id: "distance", type: "number", label: t('msg.blocchi_aggiungere'), default: 1000 },
      { id: "time", type: "number", label: t('msg.secondi_transizione'), default: 10 }
    ]
  },
  {
    id: "cmd_tick_rate",
    name: "tick rate",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.modifica_velocita_simulazione_globale_gioco'),
    template: (p) => `tick rate ${p.rate || '20'}`,
    params: [
      { id: "rate", type: "number", label: t('msg.tick_secondo_default_20'), default: 20 }
    ]
  },
  {
    id: "cmd_seed",
    name: "seed",
    category: "world",
    catName: t('msg.mondo_regole'),
    desc: t('msg.stampa_nella_console_seed_numerico'),
    template: () => `seed`,
    params: []
  },

  // ==========================================
  // 3. TELETRASPORTO & GESTIONE GIOCATORI
  // ==========================================
  {
    id: "cmd_tp_players",
    name: t('msg.tp_tra_giocatori'),
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.teletrasporta_giocatore_direttamente_nella_posiz'),
    template: (p) => `tp ${p.origin || '@p'} ${p.dest || '@s'}`,
    params: [
      { id: "origin", type: "player", label: t('msg.chi_teletrasportare'), default: "@p" },
      { id: "dest", type: "player", label: t('msg.destinazione'), default: "@s" }
    ]
  },
  {
    id: "cmd_tp_coords",
    name: t('msg.tp_coordinate'),
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.teletrasporta_giocatore_coordinate_spaziali_y'),
    template: (p) => `tp ${p.target || '@p'} ${p.x || 0} ${p.y || 70} ${p.z || 0}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "x", type: "number", label: t('msg.coordinata'), default: 0 },
      { id: "y", type: "number", label: t('msg.coordinata_y_altezza'), default: 70 },
      { id: "z", type: "number", label: t('msg.coordinata_z'), default: 0 }
    ]
  },
  {
    id: "cmd_gamemode",
    name: "gamemode",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.cambia_modalita_gioco_giocatore_specifico'),
    template: (p) => `gamemode ${p.mode || 'creative'} ${p.target || '@p'}`,
    params: [
      { id: "mode", type: "select", label: t('msg.modalita'), options: [
        { label: t('ui.sopravvivenza_survival'), val: "survival" },
        { label: t('ui.creativa_creative'), val: "creative" },
        { label: t('ui.avventura_adventure'), val: "adventure" },
        { label: t('ui.spettatore_spectator'), val: "spectator" }
      ], default: "creative" },
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" }
    ]
  },
  {
    id: "cmd_spawnpoint",
    name: "spawnpoint",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.imposta_punto_rinascita_spawn_personale'),
    template: (p) => `spawnpoint ${p.target || '@p'} ${p.coords || ''}`.trim(),
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "coords", type: "text", label: t('msg.coord_y_z_vuoto_pos'), default: "" }
    ]
  },
  {
    id: "cmd_setworldspawn",
    name: "setworldspawn",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.imposta_coordinate_punto_spawn_globale'),
    template: (p) => `setworldspawn ${p.coords || '~ ~ ~'}`.trim(),
    params: [
      { id: "coords", type: "text", label: t('msg.coord_y_z_pos_attuale'), default: "~ ~ ~" }
    ]
  },
  {
    id: "cmd_kill_player",
    name: t('msg.kill_giocatore'),
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.elimina_istantaneamente_giocatore_specificato_fa'),
    template: (p) => `kill ${p.target || '@p'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" }
    ]
  },
  {
    id: "cmd_kill_entities",
    name: t('msg.kill_entita_pulizia_lag'),
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.elimina_entita_pulire_server_azzerare'),
    template: (p) => `kill ${p.selector || '@e[type=item]'}`,
    params: [
      { id: "selector", type: "select", label: t('msg.bersaglio'), options: [
        { label: t('msg.oggetti_caduti_terra_type_item'), val: "@e[type=item]" },
        { label: t('msg.tutti_mostri_ostili_type_monster'), val: "@e[type=monster]" },
        { label: t('msg.frecce_proiettili_terra_type_arrow'), val: "@e[type=arrow]" },
        { label: t('msg.tutte_entita_tranne_player_type'), val: "@e[type=!player]" }
      ], default: "@e[type=item]" }
    ]
  },
  {
    id: "cmd_clear",
    name: "clear",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.svuota_l_inventario_giocatore_oppure'),
    template: (p) => `clear ${p.target || '@p'} ${p.item || ''}`.trim(),
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "item", type: "text", label: t('msg.oggetto_specifico_vuoto_tutto_l'), default: "" }
    ]
  },
  {
    id: "cmd_xp_add",
    name: "experience add (xp)",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.aggiunge_livelli_o_punti_esperienza'),
    template: (p) => `experience add ${p.target || '@p'} ${p.amount || 30} ${p.type || 'levels'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "amount", type: "number", label: t('msg.quantita'), default: 30, min: 1, max: 10000 },
      { id: "type", type: "select", label: t('msg.tipo'), options: [
        { label: t('msg.livelli_levels'), val: "levels" },
        { label: t('msg.punti_esperienza_points'), val: "points" }
      ], default: "levels" }
    ]
  },
  {
    id: "cmd_xp_set",
    name: "experience set",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.imposta_esattamente_livello_o_punti'),
    template: (p) => `experience set ${p.target || '@p'} ${p.amount || 0} ${p.type || 'levels'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "amount", type: "number", label: t('msg.livello_esatto'), default: 30, min: 0, max: 10000 },
      { id: "type", type: "select", label: t('msg.tipo'), options: [
        { label: t('msg.livelli_levels'), val: "levels" },
        { label: t('msg.punti_esperienza_points'), val: "points" }
      ], default: "levels" }
    ]
  },
  {
    id: "cmd_recipe_give",
    name: "recipe give",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.sblocca_ricette_nel_ricettario_giocatore'),
    template: (p) => `recipe give ${p.target || '@p'} ${p.recipe || '*'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "recipe", type: "text", label: t('msg.ricetta_tutte_ricette_esistenti'), default: "*" }
    ]
  },
  {
    id: "cmd_advancement_grant",
    name: "advancement grant",
    category: "teleport",
    catName: t('msg.teletrasporto'),
    desc: t('msg.sblocca_tutti_progressi_trofei_giocatore'),
    template: (p) => `advancement grant ${p.target || '@p'} ${p.type || 'everything'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "type", type: "select", label: t('msg.avanzamenti'), options: [
        { label: t('msg.tutti_progressi_everything'), val: "everything" },
        { label: t('msg.solo_capitolo_attuale_through'), val: "through" }
      ], default: "everything" }
    ]
  },

  // ==========================================
  // 4. OGGETTI, POZIONI & INCANTESIMI
  // ==========================================
  {
    id: "cmd_give",
    name: "give",
    category: "items",
    catName: t('msg.oggetti_pozioni'),
    desc: t('msg.assegna_qualsiasi_blocco_strumento_o'),
    template: (p) => `give ${p.target || '@p'} ${p.item || 'minecraft:diamond'} ${p.count || 64}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "item", type: "select", label: t('msg.oggetto'), options: [
        { label: t('msg.diamante_diamond'), val: "minecraft:diamond" },
        { label: t('msg.lingotto_netherite_netherite_ingot'), val: "minecraft:netherite_ingot" },
        { label: t('msg.spada_netherite_netherite_sword'), val: "minecraft:netherite_sword" },
        { label: t('msg.piccone_netherite_netherite_pickaxe'), val: "minecraft:netherite_pickaxe" },
        { label: t('msg.mela_d_oro_incantata_enchanted'), val: "minecraft:enchanted_golden_apple" },
        { label: t('msg.totem_immortalita_totem_of_undying'), val: "minecraft:totem_of_undying" },
        { label: t('msg.elitre_elytra'), val: "minecraft:elytra" },
        { label: "Shulker Box (shulker_box)", val: "minecraft:shulker_box" },
        { label: t('msg.blocco_smeraldo_emerald_block'), val: "minecraft:emerald_block" },
        { label: t('msg.pietra_stone'), val: "minecraft:stone" },
        { label: t('msg.tronco_quercia_oak_log'), val: "minecraft:oak_log" }
      ], default: "minecraft:diamond" },
      { id: "count", type: "number", label: t('msg.quantita'), default: 64, min: 1, max: 6400 }
    ]
  },
  {
    id: "cmd_effect_give",
    name: "effect give",
    category: "items",
    catName: t('msg.oggetti_pozioni'),
    desc: t('msg.applica_effetto_pozione_giocatore_durata'),
    template: (p) => `effect give ${p.target || '@p'} ${p.effect || 'minecraft:night_vision'} ${p.seconds || 99999} ${p.amp || 0} ${p.hide || 'true'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "effect", type: "select", label: t('msg.effetto'), options: [
        { label: t('msg.visione_notturna_night_vision'), val: "minecraft:night_vision" },
        { label: t('msg.velocita_speed'), val: "minecraft:speed" },
        { label: t('msg.forza_strength'), val: "minecraft:strength" },
        { label: t('msg.rigenerazione_regeneration'), val: "minecraft:regeneration" },
        { label: t('msg.resistenza_resistance'), val: "minecraft:resistance" },
        { label: t('msg.saturazione_no_fame_saturation'), val: "minecraft:saturation" },
        { label: t('msg.invisibilita_invisibility'), val: "minecraft:invisibility" },
        { label: t('msg.scavo_veloce_haste'), val: "minecraft:haste" },
        { label: t('msg.resistenza_fuoco_fire_resistance'), val: "minecraft:fire_resistance" },
        { label: t('msg.respirazione_subacquea_water_breathing'), val: "minecraft:water_breathing" },
        { label: t('msg.caduta_lenta_slow_falling'), val: "minecraft:slow_falling" },
        { label: t('msg.salto_potenziato_jump_boost'), val: "minecraft:jump_boost" },
        { label: t('msg.eroe_villaggio_hero_of_the'), val: "minecraft:hero_of_the_village" }
      ], default: "minecraft:night_vision" },
      { id: "seconds", type: "number", label: t('msg.secondi_99999_infinito'), default: 99999, min: 1, max: 999999 },
      { id: "amp", type: "number", label: t('msg.livello_amplificatore_0_liv_1'), default: 0, min: 0, max: 255 },
      { id: "hide", type: "select", label: t('msg.particelle'), options: [
        { label: t('msg.nascondi_particelle_true'), val: "true" },
        { label: t('msg.mostra_particelle_false'), val: "false" }
      ], default: "true" }
    ]
  },
  {
    id: "cmd_effect_clear",
    name: "effect clear",
    category: "items",
    catName: t('msg.oggetti_pozioni'),
    desc: t('msg.rimuove_istantaneamente_tutti_effetti_pozione'),
    template: (p) => `effect clear ${p.target || '@p'} ${p.effect || ''}`.trim(),
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "effect", type: "text", label: t('msg.effetto_specifico_vuoto_rimuove_tutti'), default: "" }
    ]
  },
  {
    id: "cmd_enchant",
    name: "enchant",
    category: "items",
    catName: t('msg.oggetti_pozioni'),
    desc: t('msg.applica_incantesimo_all_oggetto_giocatore'),
    template: (p) => `enchant ${p.target || '@p'} ${p.ench || 'minecraft:sharpness'} ${p.lvl || 5}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "ench", type: "select", label: t('msg.incantesimo'), options: [
        { label: t('msg.acutezza_sharpness_max_5'), val: "minecraft:sharpness" },
        { label: t('msg.protezione_protection_max_4'), val: "minecraft:protection" },
        { label: t('msg.riparazione_mending_max_1'), val: "minecraft:mending" },
        { label: t('msg.indistruttibilita_unbreaking_max_3'), val: "minecraft:unbreaking" },
        { label: t('msg.efficienza_efficiency_max_5'), val: "minecraft:efficiency" },
        { label: t('msg.fortuna_fortune_max_3'), val: "minecraft:fortune" },
        { label: t('msg.potenza_power_max_5'), val: "minecraft:power" },
        { label: t('msg.infinita_infinity_max_1'), val: "minecraft:infinity" },
        { label: t('msg.saccheggio_looting_max_3'), val: "minecraft:looting" },
        { label: t('msg.tocco_velluto_silk_touch_max'), val: "minecraft:silk_touch" }
      ], default: "minecraft:sharpness" },
      { id: "lvl", type: "number", label: t('msg.livello'), default: 5, min: 1, max: 10 }
    ]
  },
  {
    id: "cmd_item_replace",
    name: "item replace",
    category: "items",
    catName: t('msg.oggetti_pozioni'),
    desc: t('msg.sostituisce_o_equipaggia_forzatamente_oggetto'),
    template: (p) => `item replace entity ${p.target || '@p'} ${p.slot || 'armor.head'} with ${p.item || 'minecraft:netherite_helmet'}`,
    params: [
      { id: "target", type: "player", label: t('msg.giocatore'), default: "@p" },
      { id: "slot", type: "select", label: t('msg.slot_armatura_mano'), options: [
        { label: t('msg.elmo_armor_head'), val: "armor.head" },
        { label: t('msg.pettorale_armor_chest'), val: "armor.chest" },
        { label: t('msg.gambali_armor_legs'), val: "armor.legs" },
        { label: t('msg.stivali_armor_feet'), val: "armor.feet" },
        { label: t('msg.mano_principale_weapon_mainhand'), val: "weapon.mainhand" },
        { label: t('msg.seconda_mano_weapon_offhand'), val: "weapon.offhand" }
      ], default: "armor.head" },
      { id: "item", type: "text", label: t('msg.oggetto'), default: "minecraft:netherite_helmet" }
    ]
  },

  // ==========================================
  // 5. STRUTTURE, GENERAZIONE & COSTRUZIONE
  // ==========================================
  {
    id: "cmd_locate_struct",
    name: "locate structure",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.trova_coordinate_y_z_distanza'),
    template: (p) => `locate structure ${p.struct || 'minecraft:village_plains'}`,
    params: [
      { id: "struct", type: "select", label: t('msg.struttura'), options: [
        { label: t('msg.villaggio_pianura_village_plains'), val: "minecraft:village_plains" },
        { label: t('msg.villaggio_deserto_village_desert'), val: "minecraft:village_desert" },
        { label: t('msg.antica_citta_ancient_city'), val: "minecraft:ancient_city" },
        { label: t('msg.fortezza_nether_fortress'), val: "minecraft:fortress" },
        { label: t('msg.magione_boschi_mansion'), val: "minecraft:mansion" },
        { label: t('msg.monumento_oceanico_monument'), val: "minecraft:monument" },
        { label: t('msg.stronghold_portale_end_stronghold'), val: "minecraft:stronghold" },
        { label: t('msg.miniera_abbandonata_mineshaft'), val: "minecraft:mineshaft" },
        { label: t('msg.bastione_nether_bastion_remnant'), val: "minecraft:bastion_remnant" },
        { label: "Trial Chambers (trial_chambers)", val: "minecraft:trial_chambers" }
      ], default: "minecraft:village_plains" }
    ]
  },
  {
    id: "cmd_locate_biome",
    name: "locate biome",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.trova_coordinate_bioma_desiderato_piu'),
    template: (p) => `locate biome ${p.biome || 'minecraft:cherry_grove'}`,
    params: [
      { id: "biome", type: "select", label: t('msg.bioma'), options: [
        { label: t('msg.ciliegi_cherry_grove'), val: "minecraft:cherry_grove" },
        { label: t('msg.oscurita_profonda_deep_dark'), val: "minecraft:deep_dark" },
        { label: t('msg.caverne_rigogliose_lush_caves'), val: "minecraft:lush_caves" },
        { label: t('msg.isola_funghi_mushroom_fields'), val: "minecraft:mushroom_fields" },
        { label: t('msg.giungla_jungle'), val: "minecraft:jungle" },
        { label: t('msg.deserto_desert'), val: "minecraft:desert" },
        { label: "Badlands / Mesa (badlands)", val: "minecraft:badlands" },
        { label: t('msg.pianura_plains'), val: "minecraft:plains" }
      ], default: "minecraft:cherry_grove" }
    ]
  },
  {
    id: "cmd_summon",
    name: "summon",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.evoca_mob_animale_boss_o'),
    template: (p) => `summon ${p.entity || 'minecraft:iron_golem'} ${p.coords || '~ ~ ~'}`,
    params: [
      { id: "entity", type: "select", label: t('msg.entita'), options: [
        { label: "Iron Golem (iron_golem)", val: "minecraft:iron_golem" },
        { label: t('msg.villico_villager'), val: "minecraft:villager" },
        { label: t('msg.cavallo_horse'), val: "minecraft:horse" },
        { label: t('msg.lupo_cane_wolf'), val: "minecraft:wolf" },
        { label: t('msg.fulmine_lightning_bolt'), val: "minecraft:lightning_bolt" },
        { label: "Zombie (zombie)", val: "minecraft:zombie" },
        { label: t('msg.scheletro_skeleton'), val: "minecraft:skeleton" },
        { label: "Creeper (creeper)", val: "minecraft:creeper" },
        { label: "Wither Boss (wither)", val: "minecraft:wither" },
        { label: "Ender Dragon (ender_dragon)", val: "minecraft:ender_dragon" }
      ], default: "minecraft:iron_golem" },
      { id: "coords", type: "text", label: t('msg.coordinate_dove_sei'), default: "~ ~ ~" }
    ]
  },
  {
    id: "cmd_fill",
    name: "fill",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.riempie_intera_area_geometrica_3d'),
    template: (p) => `fill ${p.from || '~-5 ~ ~-5'} ${p.to || '~5 ~10 ~5'} ${p.block || 'minecraft:air'} ${p.mode || 'replace'}`,
    params: [
      { id: "from", type: "text", label: t('msg.x1_y1_z1'), default: "~-5 ~ ~-5" },
      { id: "to", type: "text", label: "A (X2 Y2 Z2)", default: "~5 ~10 ~5" },
      { id: "block", type: "text", label: t('msg.blocco_es_minecraft_air_rimuovere'), default: "minecraft:air" },
      { id: "mode", type: "select", label: t('msg.modalita'), options: [
        { label: t('msg.sostituisci_replace'), val: "replace" },
        { label: t('msg.distruggi_particelle_destroy'), val: "destroy" },
        { label: t('msg.solo_contorno_outline'), val: "outline" },
        { label: t('msg.cavo_all_interno_hollow'), val: "hollow" },
        { label: t('msg.mantieni_blocchi_esistenti_keep'), val: "keep" }
      ], default: "replace" }
    ]
  },
  {
    id: "cmd_clone",
    name: "clone",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.copia_incolla_porzione_blocchi_posizione'),
    template: (p) => `clone ${p.from || '100 64 100'} ${p.to || '110 74 110'} ${p.dest || '200 64 200'}`,
    params: [
      { id: "from", type: "text", label: t('msg.angolo_1_x1_y1_z1'), default: "100 64 100" },
      { id: "to", type: "text", label: t('msg.angolo_2_x2_y2_z2'), default: "110 74 110" },
      { id: "dest", type: "text", label: t('msg.destinazione_y_z'), default: "200 64 200" }
    ]
  },
  {
    id: "cmd_setblock",
    name: "setblock",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.piazza_o_rimpiazza_singolo_blocco'),
    template: (p) => `setblock ${p.coords || '~ ~-1 ~'} ${p.block || 'minecraft:diamond_block'}`,
    params: [
      { id: "coords", type: "text", label: t('msg.coordinate_1_sotto_piedi'), default: "~ ~-1 ~" },
      { id: "block", type: "text", label: t('msg.blocco'), default: "minecraft:diamond_block" }
    ]
  },
  {
    id: "cmd_particle",
    name: "particle",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.genera_particelle_visive_coordinate_specifiche'),
    template: (p) => `particle ${p.particle || 'minecraft:flame'} ${p.coords || '~ ~1 ~'} 0.5 0.5 0.5 0.1 50`,
    params: [
      { id: "particle", type: "select", label: t('msg.particella'), options: [
        { label: t('msg.fiamme_flame'), val: "minecraft:flame" },
        { label: t('msg.cuori_heart'), val: "minecraft:heart" },
        { label: t('msg.villico_felice_happy_villager'), val: "minecraft:happy_villager" },
        { label: t('msg.esplosione_explosion'), val: "minecraft:explosion" },
        { label: t('msg.portale_portal'), val: "minecraft:portal" },
        { label: t('msg.fulmine_scintille_electric_spark'), val: "minecraft:electric_spark" }
      ], default: "minecraft:flame" },
      { id: "coords", type: "text", label: t('msg.coordinate'), default: "~ ~1 ~" }
    ]
  },
  {
    id: "cmd_playsound",
    name: "playsound",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.riproduce_effetto_sonoro_personalizzato_uno'),
    template: (p) => `playsound ${p.sound || 'ui.toast.challenge_complete'} master ${p.target || '@a'} ~ ~ ~ 1 1`,
    params: [
      { id: "sound", type: "select", label: t('msg.suono'), options: [
        { label: t('msg.obiettivo_completato_challenge_complete'), val: "ui.toast.challenge_complete" },
        { label: t('msg.salita_livello_player_levelup'), val: "entity.player.levelup" },
        { label: t('msg.ruggito_ender_dragon_ender_dragon'), val: "entity.ender_dragon.growl" },
        { label: t('msg.campana_villaggio_bell_block'), val: "block.bell.use" },
        { label: t('msg.tuono_vicino_thunder'), val: "entity.lightning_bolt.thunder" }
      ], default: "ui.toast.challenge_complete" },
      { id: "target", type: "player", label: t('msg.destinatario'), default: "@a" }
    ]
  },
  {
    id: "cmd_title",
    name: "title",
    category: "structures",
    catName: t('msg.strutture_costruzione'),
    desc: t('msg.mostra_titolo_o_sottotitolo_gigante'),
    template: (p) => `title ${p.target || '@a'} ${p.type || 'title'} {"text":"${p.text || 'BENVENUTI!'}","color":"${p.color || 'gold'}","bold":true}`,
    params: [
      { id: "target", type: "player", label: t('msg.destinatario'), default: "@a" },
      { id: "type", type: "select", label: t('msg.posizione'), options: [
        { label: t('msg.titolo_gigante_centro_title'), val: "title" },
        { label: t('msg.sottotitolo_subtitle'), val: "subtitle" },
        { label: t('msg.barra_d_azione_basso_actionbar'), val: "actionbar" }
      ], default: "title" },
      { id: "text", type: "text", label: t('msg.testo_mostrare'), default: t('defaults.benvenuti_sul_server_2') },
      { id: "color", type: "select", label: t('msg.colore'), options: [
        { label: t('msg.oro_gold'), val: "gold" },
        { label: t('msg.verde_green'), val: "green" },
        { label: t('msg.rosso_red'), val: "red" },
        { label: t('msg.azzurro_aqua'), val: "aqua" },
        { label: t('msg.bianco_white'), val: "white" }
      ], default: "gold" }
    ]
  }
];

// Interactive state store for parameter values of each command
const commandParamsState = {};

function initCommandParamsState() {
  FULL_MINECRAFT_COMMANDS.forEach(c => {
    if (!commandParamsState[c.id]) {
      commandParamsState[c.id] = {};
      if (c.params) {
        c.params.forEach(p => {
          commandParamsState[c.id][p.id] = p.default !== undefined ? trDefault(p.default) : '';
        });
      }
    }
  });
}
initCommandParamsState();

// Cambio di lingua (o dizionario arrivato dopo l'avvio): i valori proposti che l'utente non ha toccato passano alla
// nuova lingua; quelli scritti dall'utente restano com'erano
document.addEventListener('i18n:ready', () => {
  let changed = false;
  FULL_MINECRAFT_COMMANDS.forEach(c => {
    (c.params || []).forEach(p => {
      if (typeof p.default !== 'string' || !commandParamsState[c.id]) return;
      const cur = commandParamsState[c.id][p.id];
      const next = trDefault(p.default);
      if (cur !== next && (cur === p.default || cur === c._shownDefaults?.[p.id])) {
        commandParamsState[c.id][p.id] = next;
        changed = true;
      }
      (c._shownDefaults = c._shownDefaults || {})[p.id] = next;
    });
  });
  if (changed && typeof renderCommands === 'function') renderCommands();
});

let activeCommandCategory = 'all';

function getLiveCommandString(cmdId) {
  const cmd = FULL_MINECRAFT_COMMANDS.find(c => c.id === cmdId);
  if (!cmd) return '';
  const params = commandParamsState[cmdId] || {};
  return cmd.template(params);
}

function updateLivePreview(cmdId) {
  const previewEl = document.getElementById(`preview-${cmdId}`);
  if (previewEl) {
    const fullCmd = getLiveCommandString(cmdId);
    previewEl.textContent = `/${fullCmd}`;
  }
}

function onParamChange(cmdId, paramId, value) {
  if (!commandParamsState[cmdId]) commandParamsState[cmdId] = {};
  commandParamsState[cmdId][paramId] = value;
  updateLivePreview(cmdId);
}

function onPlayerSelectChange(cmdId, paramId, selectEl) {
  const val = selectEl.value;
  const customInput = document.getElementById(`custom-${cmdId}-${paramId}`);
  if (val === '__custom__') {
    if (customInput) {
      customInput.style.display = 'block';
      customInput.focus();
      onParamChange(cmdId, paramId, customInput.value || 'Player');
    }
  } else {
    if (customInput) customInput.style.display = 'none';
    onParamChange(cmdId, paramId, val);
  }
}

function onCustomPlayerInput(cmdId, paramId, value) {
  onParamChange(cmdId, paramId, value || 'Player');
}

function renderCommands() {
  const grid = document.getElementById('commands-grid');
  if (!grid) return;

  const searchInput = document.getElementById('commands-search');
  const query = (searchInput ? searchInput.value : '').toLowerCase().trim();

  // Category counts
  const countAll = FULL_MINECRAFT_COMMANDS.length;
  const countFavorites = FULL_MINECRAFT_COMMANDS.filter(c => pinnedCommands.includes(c.id)).length;
  const countAdmin = FULL_MINECRAFT_COMMANDS.filter(c => c.category === 'admin').length;
  const countWorld = FULL_MINECRAFT_COMMANDS.filter(c => c.category === 'world').length;
  const countTeleport = FULL_MINECRAFT_COMMANDS.filter(c => c.category === 'teleport').length;
  const countItems = FULL_MINECRAFT_COMMANDS.filter(c => c.category === 'items').length;
  const countStructures = FULL_MINECRAFT_COMMANDS.filter(c => c.category === 'structures').length;

  if (document.getElementById('cmd-count-all')) document.getElementById('cmd-count-all').textContent = countAll;
  if (document.getElementById('cmd-count-favorites')) document.getElementById('cmd-count-favorites').textContent = countFavorites;
  if (document.getElementById('cmd-count-admin')) document.getElementById('cmd-count-admin').textContent = countAdmin;
  if (document.getElementById('cmd-count-world')) document.getElementById('cmd-count-world').textContent = countWorld;
  if (document.getElementById('cmd-count-teleport')) document.getElementById('cmd-count-teleport').textContent = countTeleport;
  if (document.getElementById('cmd-count-items')) document.getElementById('cmd-count-items').textContent = countItems;
  if (document.getElementById('cmd-count-structures')) document.getElementById('cmd-count-structures').textContent = countStructures;

  let filtered = FULL_MINECRAFT_COMMANDS.filter(c => {
    const matchesCat = (activeCommandCategory === 'all')
      ? true
      : (activeCommandCategory === 'favorites')
        ? pinnedCommands.includes(c.id)
        : (c.category === activeCommandCategory);
    if (!matchesCat) return false;
    if (!query) return true;
    const inName = c.name.toLowerCase().includes(query);
    const inDesc = c.desc.toLowerCase().includes(query);
    const inCat = c.catName.toLowerCase().includes(query);
    const inParams = c.params && c.params.some(p => {
      if (p.label && p.label.toLowerCase().includes(query)) return true;
      if (p.options && p.options.some(o => (o.label && o.label.toLowerCase().includes(query)) || (o.val && o.val.toLowerCase().includes(query)))) return true;
      return false;
    });
    return inName || inDesc || inCat || inParams;
  });

  if (activeCommandCategory === 'all') {
    filtered.sort((a, b) => {
      const aPinned = pinnedCommands.includes(a.id);
      const bPinned = pinnedCommands.includes(b.id);
      if (aPinned && !bPinned) return -1;
      if (!aPinned && bPinned) return 1;
      return 0;
    });
  }

  if (filtered.length === 0) {
    if (activeCommandCategory === 'favorites') {
      grid.innerHTML = `
        <div class="cmd-empty-state" style="grid-column: 1 / -1; text-align: center; padding: 40px 20px;">
          <div style="font-size: 40px; margin-bottom: 12px;"></div>
          <h4 style="color: #f4f4f5; margin-bottom: 8px;">${th('msg.nessun_comando_nei_preferiti')}</h4>
          <p style="color: #9a9ca2; max-width: 480px; margin: 0 auto;">${th('msg.clicca_sull_icona_stella')} <strong></strong> ${th('msg.alto_destra_qualsiasi_scheda_comando')}</p>
        </div>
      `;
      return;
    }
    grid.innerHTML = `
      <div class="cmd-empty-state" style="grid-column: 1 / -1;">
        <svg viewBox="0 0 24 24" width="48" height="48" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="8"></circle><line x1="21" y1="21" x2="16.65" y2="16.65"></line></svg>
        <h4>${th('msg.nessun_comando_trovato')}</h4>
        <p>${th('msg.nessun_comando_corrisponde')}<strong>${escapeHtml(query)}</strong>${th('msg.prova_parole_come')} <em>keep</em>, <em>tp</em>, <em>meteo</em>, <em>op</em> o <em>giorno</em>.</p>
      </div>
    `;
    return;
  }

  grid.innerHTML = filtered.map(c => {
    const badgeClass = `cmd-badge-${c.category}`;
    const params = commandParamsState[c.id] || {};
    const liveCmd = c.template(params);
    const isPinned = pinnedCommands.includes(c.id);
    const pinClass = isPinned ? 'cmd-pin-btn pinned' : 'cmd-pin-btn';
    const pinIcon = isPinned ? t('msg.fissato') : t('msg.fissa');
    const pinTitle = isPinned ? t('msg.rimuovi_dai_preferiti') : t('msg.aggiungi_ai_preferiti');
    const cardPinnedClass = isPinned ? ' pinned' : '';

    // Build interactive form if params exist
    let formHtml = '';
    if (c.params && c.params.length > 0) {
      const rows = c.params.map(p => {
        const curVal = params[p.id] !== undefined ? params[p.id] : trDefault(p.default);

        if (p.type === 'player') {
          const isStandard = ['@p', '@a', '@s'].includes(curVal) || onlinePlayerNames.includes(curVal);
          const selectVal = isStandard ? curVal : '__custom__';
          const customVal = isStandard ? '' : curVal;
          const customDisplay = isStandard ? 'none' : 'block';

          let optionsHtml = `
            <option value="@p" ${curVal === '@p' ? 'selected' : ''}>${th('msg.p_giocatore_piu_vicino')}</option>
            <option value="@a" ${curVal === '@a' ? 'selected' : ''}>${th('msg.tutti_giocatori')}</option>
            <option value="@s" ${curVal === '@s' ? 'selected' : ''}>${th('msg.s_te_stesso_esecutore')}</option>
          `;
          if (onlinePlayerNames.length > 0) {
            optionsHtml += `<optgroup label="Giocatori Online">`;
            onlinePlayerNames.forEach(pName => {
              optionsHtml += `<option value="${escapeHtml(pName)}" ${curVal === pName ? 'selected' : ''}>${escapeHtml(pName)}</option>`;
            });
            optionsHtml += `</optgroup>`;
          }
          optionsHtml += `<option value="__custom__" ${selectVal === '__custom__' ? 'selected' : ''}>${th('msg.nome_personalizzato')}</option>`;

          return `
            <div class="cmd-form-row">
              <span class="cmd-form-label">${escapeHtml(p.label)}:</span>
              <select class="cmd-form-select" onchange="onPlayerSelectChange('${c.id}', '${p.id}', this)">
                ${optionsHtml}
              </select>
              <input type="text" id="custom-${c.id}-${p.id}" class="cmd-form-input" style="display:${customDisplay}; max-width:130px;" placeholder="${th('ui.nome_2')}" value="${escapeHtml(customVal)}" oninput="onCustomPlayerInput('${c.id}', '${p.id}', this.value)">
            </div>
          `;
        } else if (p.type === 'select') {
          const opts = (p.options || []).map(opt => `
            <option value="${escapeHtml(opt.val)}" ${curVal === opt.val ? 'selected' : ''}>${escapeHtml(opt.label)}</option>
          `).join('');
          return `
            <div class="cmd-form-row">
              <span class="cmd-form-label">${escapeHtml(p.label)}:</span>
              <select class="cmd-form-select" onchange="onParamChange('${c.id}', '${p.id}', this.value)">
                ${opts}
              </select>
            </div>
          `;
        } else if (p.type === 'number') {
          return `
            <div class="cmd-form-row">
              <span class="cmd-form-label">${escapeHtml(p.label)}:</span>
              <input type="number" class="cmd-form-input" min="${p.min !== undefined ? p.min : 0}" max="${p.max !== undefined ? p.max : 999999}" value="${escapeHtml(String(curVal))}" oninput="onParamChange('${c.id}', '${p.id}', this.value)">
            </div>
          `;
        } else {
          return `
            <div class="cmd-form-row">
              <span class="cmd-form-label">${escapeHtml(p.label)}:</span>
              <input type="text" class="cmd-form-input" placeholder="${escapeHtml(p.label)}..." value="${escapeHtml(String(curVal))}" oninput="onParamChange('${c.id}', '${p.id}', this.value)">
            </div>
          `;
        }
      }).join('');

      formHtml = `<div class="cmd-form">${rows}</div>`;
    }

    return `
      <div class="command-card${cardPinnedClass}" id="card-${c.id}">
        <div>
          <div class="cmd-card-top">
            <div style="display: flex; align-items: center; gap: 8px; flex-wrap: wrap;">
              <span class="cmd-code">/${escapeHtml(c.name)}</span>
              <span class="cmd-badge ${badgeClass}">${escapeHtml(c.catName)}</span>
            </div>
            <button class="${pinClass}" onclick="togglePinCommand('${c.id}', event)" title="${pinTitle}">${pinIcon}</button>
          </div>
          <p class="cmd-desc">${escapeHtml(c.desc)}</p>
          ${formHtml}
          <div class="cmd-live-preview-box">
            <span class="cmd-preview-label">${th('msg.comando_pronto')}</span>
            <div class="cmd-live-preview" id="preview-${c.id}">/${escapeHtml(liveCmd)}</div>
          </div>
        </div>
        <div>
          <div class="cmd-actions">
            <button class="btn btn-xs btn-emerald" onclick="executeInteractiveCommand('${c.id}', this)" title="${th('ui.esegue_immediatamente_comando_parametri_scelti')}">
              ${th('msg.esegui')}
            </button>
            <button class="btn btn-xs btn-secondary" onclick="copyInteractiveCommand('${c.id}', this)" title="${th('ui.copia_negli_appunti')}">
              ${th('header.copia')}
            </button>
            <button class="btn btn-xs btn-sapphire" onclick="sendInteractiveToConsole('${c.id}')" title="${th('ui.invia_alla_console_completa')}">
              ${th('msg.console')}
            </button>
          </div>
          <div id="toast-${c.id}" class="cmd-toast"></div>
        </div>
      </div>
    `;
  }).join('');
}

function clearCommandSearch() {
  const searchInput = document.getElementById('commands-search');
  const clearBtn = document.getElementById('cmd-search-clear');
  if (searchInput) {
    searchInput.value = '';
    searchInput.focus();
  }
  if (clearBtn) clearBtn.style.display = 'none';
  renderCommands();
}

function filterCommands() {
  const searchInput = document.getElementById('commands-search');
  const clearBtn = document.getElementById('cmd-search-clear');
  if (clearBtn && searchInput) {
    clearBtn.style.display = searchInput.value ? 'inline-block' : 'none';
  }
  renderCommands();
}

function setCommandCategory(cat, btn) {
  activeCommandCategory = cat;
  document.querySelectorAll('.cmd-cat-btn').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderCommands();
}

async function executeInteractiveCommand(cmdId, btn) {
  const fullCmd = getLiveCommandString(cmdId);
  const toast = document.getElementById(`toast-${cmdId}`);

  if (currentServerState !== 'ONLINE') {
    if (typeof showToast === 'function') {
      showToast(t('msg.server_online_stato_avvia_server_3', currentServerState || 'OFFLINE', fullCmd), 'warning');
    }
    if (toast) {
      toast.className = 'cmd-toast cmd-toast-error show';
      toast.textContent = t('msg.server_online_impossibile_eseguire_comando');
      setTimeout(() => { toast.className = 'cmd-toast'; }, 3500);
    }
    return;
  }

  try {
    const res = await fetch('/api/server/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: fullCmd })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      if (typeof showToast === 'function') {
        showToast(t('msg.inviato_successo', fullCmd), 'success');
      }
      if (toast) {
        toast.className = 'cmd-toast cmd-toast-success show';
        toast.innerHTML = `${th('msg.eseguito')} <code>/${escapeHtml(fullCmd)}</code>`;
        setTimeout(() => { toast.className = 'cmd-toast'; }, 3500);
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.message || t('msg.errore_esecuzione_comando')}`, 'error');
      }
      if (toast) {
        toast.className = 'cmd-toast cmd-toast-error show';
        toast.textContent = t('tpl.errore', data.message || t('status.server_raggiungibile'));
        setTimeout(() => { toast.className = 'cmd-toast'; }, 4000);
      }
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.connessione_fallita_verifica_server_sia'), 'error');
    }
    if (toast) {
      toast.className = 'cmd-toast cmd-toast-error show';
      toast.textContent = t('msg.connessione_fallita_server_potrebbe_essere');
      setTimeout(() => { toast.className = 'cmd-toast'; }, 4000);
    }
  }
}

async function executeQuickAction(cmd, label) {
  if (currentServerState !== 'ONLINE') {
    if (typeof showToast === 'function') {
      showToast(t('msg.impossibile_eseguire_server_online_stato', label, currentServerState || 'OFFLINE'), 'warning');
    } else {
      alert(t('msg.server_online_stato', currentServerState || 'OFFLINE'));
    }
    return;
  }

  try {
    const res = await fetch('/api/server/command', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ command: cmd })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      if (typeof showToast === 'function') {
        showToast(t('msg.eseguito_successo', label, cmd), 'success');
      } else {
        alert(t('msg.comando_eseguito_successo', label, cmd));
      }
    } else {
      const msg = `${label}: ${data.message || t('msg.verifica_server_sia_online')}`;
      if (typeof showToast === 'function') {
        showToast(msg, 'warning');
      } else {
        alert(msg);
      }
    }
  } catch (e) {
    const msg = t('msg.errore_connessione_verifica_server_minecraft');
    if (typeof showToast === 'function') {
      showToast(msg, 'error');
    } else {
      alert(msg);
    }
  }
}

function copyInteractiveCommand(cmdId, btn) {
  const fullCmd = '/' + getLiveCommandString(cmdId);
  copyRawText(fullCmd, btn, t('header.copiato'));
}

function sendInteractiveToConsole(cmdId) {
  const fullCmd = getLiveCommandString(cmdId);
  switchMainTab('tab-console');
  const input = document.getElementById('cmd-input');
  if (input) {
    input.value = fullCmd;
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
    if (typeof showToast === 'function') {
      showToast(t('msg.comando_inserito_nella_console_premi', fullCmd), 'info');
    }
  }
}

// =========================================================
// TAB 5: GESTIONE MOD MINECRAFT (SOLO PC)
// =========================================================
let allMods = [];
let activeModFilter = 'all';
let activeModSort = 'name-asc';
let modsNeedRestart = false;

let availableModUpdates = {};
let availableModUpdatesCount = 0;

function syncModUpdateBadges() {
  const cnt = availableModUpdatesCount;

  // Tab 6: Gestione Mod
  const statUpd = document.getElementById('mod-stat-updates');
  if (statUpd) statUpd.textContent = cnt;
  const pillUpd = document.getElementById('mod-filter-updates');
  if (pillUpd) pillUpd.style.display = cnt > 0 ? 'inline-flex' : 'none';

  const btnUpdAll = document.getElementById('btn-update-all-mods');
  const countUpdAll = document.getElementById('count-update-all-mods');
  if (countUpdAll) countUpdAll.textContent = cnt;
  if (btnUpdAll) btnUpdAll.style.display = cnt > 0 ? 'inline-flex' : 'none';

  // Tab 11: Store Modrinth
  const storeNavBadge = document.getElementById('store-updates-nav-badge');
  const storeNavCount = document.getElementById('store-updates-nav-count');
  if (storeNavCount) storeNavCount.textContent = cnt;
  if (storeNavBadge) storeNavBadge.style.display = cnt > 0 ? 'inline-block' : 'none';

  const btnStoreUpdAll = document.getElementById('btn-store-update-all');
  const storeUpdAllCount = document.getElementById('store-update-all-count');
  if (storeUpdAllCount) storeUpdAllCount.textContent = cnt;
  if (btnStoreUpdAll) btnStoreUpdAll.style.display = cnt > 0 ? 'inline-flex' : 'none';
}

function handleModUpdatesDetected(data) {
  if (!data) return;
  availableModUpdates = data.updates_by_file || {};
  availableModUpdatesCount = data.updates_count || Object.keys(availableModUpdates).length;
  syncModUpdateBadges();
  renderMods();
  if (typeof currentStoreActiveView !== 'undefined' && currentStoreActiveView === 'installed') {
    renderInstalledStoreList(currentStoreInstalledList);
  }
}

async function loadModsList(isManual = false) {
  const container = document.getElementById('mods-list-container');
  try {
    const res = await fetch('/api/mods');
    if (res.ok) {
      const data = await res.json();
      allMods = data.mods || [];

      if (data.updates_by_file) {
        availableModUpdates = data.updates_by_file;
        availableModUpdatesCount = data.updates_count || Object.keys(data.updates_by_file).length;
        syncModUpdateBadges();
      }

      updateText('mods-nav-count', data.total || 0);
      updateText('snav-mods-count', data.total || 0);
      updateText('mod-stat-all', data.total || 0);
      updateText('mod-stat-enabled', data.enabled_count || 0);
      updateText('mod-stat-disabled', data.disabled_count || 0);
      updateText('mod-stat-duplicates', data.duplicate_count || 0);

      const sub = document.getElementById('mods-subtitle');
      if (sub && data.total !== undefined) {
        sub.innerHTML = `<b>${data.total}</b> ${th('msg.mod_installate')}<b>${escapeHtml(data.total_size_formatted || '0 MB')}</b> ${th('msg.totali_aggiungi_nuove_mod_jar')}`;
      }

      renderMods();

      if (isManual && typeof showToast === 'function') {
        showToast(t('msg.elenco_mod_aggiornato_mod_trovate', data.total || 0, data.enabled_count || 0), 'info');
      }
    } else {
      if (container) container.innerHTML = `<div class="mods-empty">${th('msg.impossibile_caricare_mod_dal_server')}</div>`;
    }
  } catch (err) {
    if (container) container.innerHTML = `<div class="mods-empty">${th('msg.errore_connessione_controller_mod')}</div>`;
  }
}

function setModFilter(filter, btn) {
  activeModFilter = filter;
  document.querySelectorAll('.mod-pill').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  renderMods();
}

function filterMods() {
  const searchInput = document.getElementById('mods-search-input');
  const clearBtn = document.getElementById('btn-clear-mod-search');
  if (clearBtn && searchInput) {
    clearBtn.style.display = searchInput.value ? 'inline-block' : 'none';
  }
  renderMods();
}

function clearModSearch() {
  const searchInput = document.getElementById('mods-search-input');
  const clearBtn = document.getElementById('btn-clear-mod-search');
  if (searchInput) searchInput.value = '';
  if (clearBtn) clearBtn.style.display = 'none';
  renderMods();
}

function sortAndRenderMods() {
  const sel = document.getElementById('mods-sort-select');
  if (sel) activeModSort = sel.value;
  renderMods();
}

function renderMods() {
  const container = document.getElementById('mods-list-container');
  if (!container) return;

  const searchInput = document.getElementById('mods-search-input');
  const search = searchInput ? searchInput.value.toLowerCase().trim() : '';

  let filtered = allMods.filter(m => {
    // Category filter
    if (activeModFilter === 'updates' && !m.update_available && !availableModUpdates[m.filename]) return false;
    if (activeModFilter === 'enabled' && !m.enabled) return false;
    if (activeModFilter === 'disabled' && m.enabled) return false;
    if (activeModFilter === 'duplicates' && !m.is_duplicate) return false;
    // Search query
    if (search && !m.filename.toLowerCase().includes(search)) return false;
    return true;
  });

  // Sorting
  filtered.sort((a, b) => {
    if (activeModSort === 'name-desc') {
      return b.filename.localeCompare(a.filename, undefined, { sensitivity: 'base' });
    } else if (activeModSort === 'size-desc') {
      return (b.size_bytes || 0) - (a.size_bytes || 0);
    } else if (activeModSort === 'size-asc') {
      return (a.size_bytes || 0) - (b.size_bytes || 0);
    } else if (activeModSort === 'status-first') {
      if (a.enabled !== b.enabled) return b.enabled ? 1 : -1;
      return a.filename.localeCompare(b.filename, undefined, { sensitivity: 'base' });
    } else {
      // default: name-asc
      return a.filename.localeCompare(b.filename, undefined, { sensitivity: 'base' });
    }
  });

  if (filtered.length === 0) {
    if (allMods.length === 0) {
      container.innerHTML = `
        <div class="mods-empty">
          <p>${th('msg.nessuna_mod_trovata_nella_cartella')} <code>mods</code> ${th('msg.server')}</p>
          <p style="margin-top: 8px; font-size: 0.82rem; color: var(--text-dim);">${th('msg.trascina_o_carica_qui_sopra')} <code>.jar</code> ${th('msg.iniziare')}</p>
        </div>
      `;
    } else {
      container.innerHTML = `
        <div class="mods-empty">
          <p>${th('msg.nessuna_mod_corrisponde_ai_filtri')}</p>
          <button class="btn btn-xs btn-secondary" style="margin-top:10px;" onclick="clearModSearch()">${th('msg.mostra_tutte')}</button>
        </div>
      `;
    }
    return;
  }

  container.innerHTML = filtered.map(m => {
    const cardClass = m.enabled ? 'mod-card mod-active' : 'mod-card mod-disabled';
    const statusLabel = m.enabled ? t('msg.attiva') : t('msg.disattivata');
    const toggleBtnClass = m.enabled ? 'btn-mod-toggle toggle-active' : 'btn-mod-toggle toggle-disabled';
    const toggleBtnText = m.enabled ? t('msg.disattiva') : t('msg.attiva');
    const dupBadge = m.is_duplicate ? `<span class="badge" style="font-size:0.68rem; padding:1px 6px; background:rgba(245,158,11,0.18); color:#d9a441; border:1px solid rgba(245,158,11,0.35);" title="${th('ui.rilevato_file_multiplo_o_possibile')}">Conflitto/Duplicato</span>` : '';

    const upd = m.update_info || availableModUpdates[m.filename];
    const updBadge = upd ? `<span class="badge" style="font-size:0.68rem; padding:1px 7px; background:rgba(245,158,11,0.2); color:#e3b35a; border:1px solid rgba(245,158,11,0.4);" title="${th('ui.nuova_versione', escapeHtml(upd.version_number), escapeHtml(upd.new_filename))}">${th('msg.aggiornamento_vx', escapeHtml(upd.version_number))}</span>` : '';
    const updBtn = upd ? `<button class="btn-mod-update" onclick="updateSingleMod('${escapeJsAttr(m.filename)}', this)" title="${th('msg.aggiorna_alla_versione_vx', escapeHtml(upd.version_number), escapeHtml(upd.new_filename))}">${th('ui.aggiorna')}</button>` : '';

    return `
      <div class="${cardClass}">
        <div class="mod-info-left mod-info-clickable" onclick="openModInfo('${escapeJsAttr(m.filename)}')" title="${th('ui.clicca_informazioni_mod')}">
          <div class="mod-icon-box" title="${escapeHtml(m.filename)}">
            ${m.enabled ? '' : ''}
          </div>
          <div class="mod-details">
            <span class="mod-title" title="${escapeHtml(m.filename)}">${escapeHtml(m.filename)}</span>
            <div class="mod-meta">
              <span>${statusLabel}</span>
              <span>•</span>
              <span class="badge" style="font-size:0.68rem; padding:1px 6px; background:rgba(255,255,255,0.06);">${escapeHtml(m.size_formatted)}</span>
              ${dupBadge ? `<span>•</span>${dupBadge}` : ''}
              ${updBadge ? `<span>•</span>${updBadge}` : ''}
            </div>
          </div>
        </div>
        <div class="mod-actions">
          <button class="btn-mod-info" onclick="openModInfo('${escapeJsAttr(m.filename)}')" title="${th('ui.informazioni_mod')}">Info</button>
          ${updBtn}
          <button class="${toggleBtnClass}" onclick="toggleMod('${escapeJsAttr(m.filename)}', this)" title="${m.enabled ? t('msg.disattiva_questa_mod_aggiunge_disabled') : t('msg.attiva_questa_mod')}">
            ${toggleBtnText}
          </button>
          <button class="btn-mod-delete" onclick="deleteMod('${escapeJsAttr(m.filename)}')" title="${th('ui.elimina_definitivamente_questo_file_dal')}">${th('msg.elimina')}</button>
        </div>
      </div>
    `;
  }).join('');
}

// =========================================================
// SCHEDA DI UNA MOD (dati del .jar + progetto Modrinth se c'è)
// =========================================================
const MOD_LOADER_NAMES = { forge: 'Forge', neoforge: 'NeoForge', fabric: 'Fabric', quilt: 'Quilt', paper: 'Paper', bukkit: 'Bukkit / Spigot' };
const MOD_SIDE_NAMES = { required: t('msg.necessaria'), optional: t('msg.facoltativa'), unsupported: t('msg.serve'), unknown: t('msg.indicato') };
let modInfoRequestSeq = 0;

function safeExternalUrl(url) {
  return /^https?:\/\//i.test(url || '') ? url : '';
}

function modInfoRow(label, valueHtml) {
  return valueHtml ? `<div class="mod-info-row"><span class="mod-info-label">${label}</span><span class="mod-info-value">${valueHtml}</span></div>` : '';
}

async function openModInfo(filename) {
  const seq = ++modInfoRequestSeq;
  const mod = allMods.find(m => m.filename === filename) || { filename };
  document.getElementById('mod-info-title').textContent = filename;
  document.getElementById('mod-info-subtitle').textContent = '';
  document.getElementById('mod-info-icon').innerHTML = '';
  document.getElementById('mod-info-body').innerHTML = `<div class="mod-info-loading"><div class="spinner-inline"></div> ${th('msg.lettura_informazioni_mod')}</div>`;
  openModal('modal-mod-info');

  try {
    const res = await fetch(`/api/mods/info?filename=${encodeURIComponent(filename)}`);
    const data = await res.json();
    if (seq !== modInfoRequestSeq) return; // nel frattempo è stata aperta un'altra mod
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderModInfo(data, mod);
  } catch (err) {
    if (seq !== modInfoRequestSeq) return;
    document.getElementById('mod-info-body').innerHTML = `<div class="mod-info-empty">${th('msg.impossibile_leggere_informazioni_mod', escapeHtml(err.message))}</div>`;
  }
}

function renderModInfo(data, mod) {
  const meta = data.metadata || {};
  const mr = data.modrinth || {};
  const name = meta.name || mr.title || data.filename;
  const version = meta.version || mr.version_number || '';

  document.getElementById('mod-info-title').textContent = name;
  document.getElementById('mod-info-subtitle').textContent = [version && `v${version}`, MOD_LOADER_NAMES[meta.loader] || meta.loader].filter(Boolean).join(' • ');

  const iconSrc = (meta.icon && meta.icon.startsWith('data:image/')) ? meta.icon : safeExternalUrl(mr.icon_url);
  const iconBox = document.getElementById('mod-info-icon');
  iconBox.innerHTML = iconSrc ? `<img src="${escapeHtml(iconSrc)}" alt="">` : escapeHtml((name || '?').trim().charAt(0).toUpperCase());

  const description = meta.description || mr.description || '';
  const authors = (meta.authors || []).join(', ');
  const deps = (meta.dependencies || []).filter(d => !['minecraft', 'java', 'fabricloader', 'quilt_loader'].includes((d.id || '').toLowerCase()));
  const sizeMb = (data.size_bytes || 0) >= 1048576 ? `${(data.size_bytes / 1048576).toFixed(1)} MB` : `${((data.size_bytes || 0) / 1024).toFixed(1)} KB`;
  const modified = data.mtime ? new Date(data.mtime * 1000).toLocaleString('it-IT') : '';
  const homepage = safeExternalUrl(meta.homepage) || safeExternalUrl(mr.source_url);
  const issues = safeExternalUrl(meta.issues) || safeExternalUrl(mr.issues_url);
  const link = (url, text) => url ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text || url)}</a>` : '';

  let html = '';
  if (!meta.loader && !mr.title) {
    html += `<div class="mod-info-empty">${th('msg.questo_file_contiene_informazioni_mod')}</div>`;
  }
  if (description) {
    html += `<p class="mod-info-desc">${escapeHtml(description).replace(/\n/g, '<br>')}</p>`;
  }

  let modRows = modInfoRow(t('ui.nome'), escapeHtml(meta.name || mr.title || ''));
  modRows += modInfoRow('ID', meta.mod_id ? `<code>${escapeHtml(meta.mod_id)}</code>` : '');
  modRows += modInfoRow(t('msg.versione'), escapeHtml(version));
  modRows += modInfoRow('Loader', escapeHtml(MOD_LOADER_NAMES[meta.loader] || meta.loader || (mr.loaders || []).join(', ')));
  modRows += modInfoRow(t('msg.versioni_minecraft'), escapeHtml((mr.game_versions || []).join(', ')));
  modRows += modInfoRow(t('msg.autori'), escapeHtml(authors));
  modRows += modInfoRow(t('msg.riconoscimenti'), escapeHtml(meta.credits || ''));
  modRows += modInfoRow(t('msg.licenza'), escapeHtml(meta.license || mr.license || ''));
  modRows += modInfoRow(t('msg.sito'), link(homepage));
  modRows += modInfoRow(t('msg.segnalazioni'), link(issues));
  if (modRows) html += `<div class="mod-info-section"><div class="mod-info-section-title">Mod</div>${modRows}</div>`;

  if (mr.url) {
    html += '<div class="mod-info-section"><div class="mod-info-section-title">Modrinth</div>';
    html += modInfoRow(t('ui.pagina_2'), link(safeExternalUrl(mr.url), t('msg.apri_modrinth')));
    html += modInfoRow('Download', escapeHtml(Number(mr.downloads || 0).toLocaleString('it-IT')));
    html += modInfoRow(t('msg.categorie'), escapeHtml((mr.categories || []).join(', ')));
    html += modInfoRow(t('msg.sul_server'), escapeHtml(MOD_SIDE_NAMES[mr.server_side] || mr.server_side || ''));
    html += modInfoRow(t('msg.sul_client'), escapeHtml(MOD_SIDE_NAMES[mr.client_side] || mr.client_side || ''));
    html += '</div>';
  }

  if (deps.length) {
    html += `<div class="mod-info-section"><div class="mod-info-section-title">${th('msg.dipendenze')}</div><div class="mod-info-deps">`;
    html += deps.map(d => `<span class="mod-info-dep ${d.required ? 'dep-required' : ''}" title="${d.required ? t('msg.obbligatoria') : t('msg.facoltativa')}">${escapeHtml(d.id)}${d.version ? ` <span class="dep-ver">${escapeHtml(d.version)}</span>` : ''}${d.side && d.side !== 'both' ? ` <span class="dep-ver">(${escapeHtml(d.side)})</span>` : ''}</span>`).join('');
    html += '</div></div>';
  }

  html += '<div class="mod-info-section"><div class="mod-info-section-title">File</div>';
  html += modInfoRow(t('msg.nome_file'), `<code>${escapeHtml(data.filename)}</code>`);
  html += modInfoRow(t('msg.stato'), mod.enabled === false ? t('msg.disattivata') : t('msg.attiva'));
  html += modInfoRow(t('ui.dimensione'), escapeHtml(sizeMb));
  html += modInfoRow(t('msg.modificato'), escapeHtml(modified));
  html += modInfoRow('SHA-1', data.sha1 ? `<code class="mod-info-hash">${escapeHtml(data.sha1)}</code>` : '');
  html += '</div>';

  document.getElementById('mod-info-body').innerHTML = html;
}

// Scheda di una mod o plugin dello Store (dati del progetto Modrinth), nella stessa finestra delle mod installate
async function openStoreProjectInfo(projectId) {
  const seq = ++modInfoRequestSeq;
  document.getElementById('mod-info-title').textContent = t('msg.informazioni');
  document.getElementById('mod-info-subtitle').textContent = '';
  document.getElementById('mod-info-icon').innerHTML = '';
  document.getElementById('mod-info-body').innerHTML = `<div class="mod-info-loading"><div class="spinner-inline"></div> ${th('msg.lettura_informazioni_modrinth')}</div>`;
  openModal('modal-mod-info');

  try {
    const res = await fetch(`/api/store/project?project_id=${encodeURIComponent(projectId)}`);
    const data = await res.json();
    if (seq !== modInfoRequestSeq) return;
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    renderStoreProjectInfo(data);
  } catch (err) {
    if (seq !== modInfoRequestSeq) return;
    document.getElementById('mod-info-body').innerHTML = `<div class="mod-info-empty">${th('msg.impossibile_leggere_informazioni', escapeHtml(err.message))}</div>`;
  }
}

// Testo della pagina Modrinth (Markdown) reso leggibile come testo semplice: niente immagini né HTML
function modrinthBodyToText(md) {
  return String(md || '')
    .replace(/<[^>]+>/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]+)\]\([^)]*\)/g, '$1')
    .replace(/^#{1,6}\s*/gm, '')
    .replace(/(\*\*|__)(.*?)\1/g, '$2')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function renderStoreProjectInfo(p) {
  const typeName = p.project_type === 'plugin' ? 'Plugin' : (p.project_type === 'mod' ? 'Mod' : p.project_type);
  document.getElementById('mod-info-title').textContent = p.title || t('msg.progetto');
  document.getElementById('mod-info-subtitle').textContent = [typeName, (p.loaders || []).map(l => MOD_LOADER_NAMES[l] || l).join(', ')].filter(Boolean).join(' • ');
  const iconSrc = safeExternalUrl(p.icon_url);
  document.getElementById('mod-info-icon').innerHTML = iconSrc
    ? `<img src="${escapeHtml(iconSrc)}" alt="">`
    : escapeHtml((p.title || '?').trim().charAt(0).toUpperCase());

  const link = (url, text) => {
    const u = safeExternalUrl(url);
    return u ? `<a href="${escapeHtml(u)}" target="_blank" rel="noopener noreferrer">${escapeHtml(text || u)}</a>` : '';
  };
  const date = (iso) => iso ? new Date(iso).toLocaleDateString('it-IT') : '';

  let html = '';
  if (p.description) html += `<p class="mod-info-desc">${escapeHtml(p.description)}</p>`;

  let rows = modInfoRow(t('msg.autori'), escapeHtml((p.authors || []).join(', ')));
  rows += modInfoRow('Download', escapeHtml(Number(p.downloads || 0).toLocaleString('it-IT')));
  rows += modInfoRow('Follower', escapeHtml(Number(p.followers || 0).toLocaleString('it-IT')));
  rows += modInfoRow(t('msg.categorie'), escapeHtml((p.categories || []).join(', ')));
  rows += modInfoRow('Loader', escapeHtml((p.loaders || []).map(l => MOD_LOADER_NAMES[l] || l).join(', ')));
  rows += modInfoRow(t('msg.versioni_minecraft'), escapeHtml((p.game_versions || []).join(', ')));
  rows += modInfoRow(t('msg.sul_server'), escapeHtml(MOD_SIDE_NAMES[p.server_side] || p.server_side || ''));
  rows += modInfoRow(t('msg.sul_client'), escapeHtml(MOD_SIDE_NAMES[p.client_side] || p.client_side || ''));
  rows += modInfoRow(t('msg.licenza'), escapeHtml(p.license || ''));
  rows += modInfoRow(t('msg.pubblicata'), escapeHtml(date(p.published)));
  rows += modInfoRow(t('msg.aggiornata'), escapeHtml(date(p.updated)));
  if (rows) html += `<div class="mod-info-section"><div class="mod-info-section-title">${escapeHtml(typeName)}</div>${rows}</div>`;

  let links = modInfoRow(t('ui.pagina_2'), link(p.url, t('msg.apri_modrinth')));
  links += modInfoRow(t('msg.codice'), link(p.source_url));
  links += modInfoRow(t('msg.segnalazioni'), link(p.issues_url));
  links += modInfoRow('Wiki', link(p.wiki_url));
  links += modInfoRow('Discord', link(p.discord_url));
  if (links) html += `<div class="mod-info-section"><div class="mod-info-section-title">Link</div>${links}</div>`;

  const body = modrinthBodyToText(p.body);
  if (body) {
    html += `<div class="mod-info-section"><div class="mod-info-section-title">${th('msg.descrizione_completa')}</div><div class="store-info-body">${escapeHtml(body)}</div></div>`;
  }
  document.getElementById('mod-info-body').innerHTML = html;
}

async function batchToggleMods(action) {
  const isEnable = action === 'enable_all';
  const confirmMsg = isEnable
    ? t('msg.vuoi_attivare_tutte_mod_attualmente')
    : t('msg.vuoi_disattivare_tutte_mod_attive');

  if (!confirm(confirmMsg)) return;

  if (currentServerState === 'ONLINE' && !isEnable) {
    const proceed = confirm(t('msg.attenzione_server_minecraft_attualmente_online'));
    if (!proceed) return;
  }

  try {
    const res = await fetch('/api/mods/batch-toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ action: action })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      if (data.errors && data.errors.length > 0) {
        showToast(t('msg.modificate_mod_alcuni_file_modificati', data.modified_count, data.errors.length), 'warning');
      } else {
        showToast(t('msg.operazione_completata_mod', data.modified_count, isEnable ? t('msg.attivate') : t('msg.disattivate')), 'success');
      }
      if (currentServerState === 'ONLINE') {
        notifyModsRestartRequired();
      }
      await loadModsList();
    } else {
      showToast(`${data.detail || data.message || t('msg.errore_durante_operazione_massiva_mod')}`, 'error');
      await loadModsList();
    }
  } catch (err) {
    showToast(t('msg.errore_rete', err.message), 'error');
    await loadModsList();
  }
}

async function toggleMod(filename, btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = '...';
  }

  try {
    const res = await fetch('/api/mods/toggle', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: filename })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      if (typeof showToast === 'function') {
        showToast(`Mod ${data.enabled ? 'attivata' : 'disattivata'}: ${escapeHtml(data.new_filename || filename)}`, 'success');
      }
      if (currentServerState === 'ONLINE') {
        notifyModsRestartRequired();
      }
      await loadModsList();
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.detail || data.message || t('msg.impossibile_modificare_stato_mod')}`, 'error');
      }
      await loadModsList();
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_rete', err.message), 'error');
    }
    await loadModsList();
  }
}

async function deleteMod(filename) {
  if (!confirm(t('msg.sei_sicuro_voler_eliminare_definitivamente_3', filename))) {
    return;
  }

  try {
    const res = await fetch('/api/mods/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ filename: filename })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      if (typeof showToast === 'function') {
        showToast(t('msg.mod_eliminata_successo', filename), 'info');
      }
      if (currentServerState === 'ONLINE') {
        notifyModsRestartRequired();
      }
      await loadModsList();
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.detail || data.message || t('msg.impossibile_eliminare_mod')}`, 'error');
      }
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_connessione', err.message), 'error');
    }
  }
}

function notifyModsRestartRequired() {
  modsNeedRestart = true;
  const banner = document.getElementById('mods-restart-banner');
  if (banner) banner.style.display = 'flex';
}

function dismissModsRestartBanner() {
  const banner = document.getElementById('mods-restart-banner');
  if (banner) banner.style.display = 'none';
}

async function handleModFilesSelect(fileList) {
  if (!fileList || fileList.length === 0) return;
  const files = Array.from(fileList);
  const jarFiles = files.filter(f => f.name.toLowerCase().endsWith('.jar'));

  if (jarFiles.length === 0) {
    if (typeof showToast === 'function') {
      showToast(t('msg.nessun_file_jar_valido_selezionato'), 'warning');
    }
    return;
  }

  const dropzone = document.getElementById('mods-dropzone');
  if (dropzone) {
    dropzone.innerHTML = `<div class="dropzone-icon"></div><div class="dropzone-text">${th('msg.caricamento_mod_corso_sul_server', jarFiles.length)}</div>`;
  }

  let successCount = 0;
  let lastErr = null;
  for (const f of jarFiles) {
    try {
      const res = await fetch('/api/mods/upload_file?name=' + encodeURIComponent(f.name), {
        method: 'POST',
        headers: { 'Content-Type': 'application/octet-stream' },
        body: f
      });
      if (res.ok) {
        successCount++;
      } else {
        const errData = await res.json().catch(() => ({}));
        lastErr = errData.detail || errData.message;
      }
    } catch (e) {
      console.error('Errore upload mod:', f.name, e);
      lastErr = e.message;
    }
  }

  // Restore dropzone
  if (dropzone) {
    dropzone.innerHTML = `
      <div class="dropzone-icon"></div>
      <div class="dropzone-text"><b>${th('ui.trascina_qui_file_jar_nuove')}</b> oppure <u>${th('ui.clicca_sfogliare_dal_computer')}</u></div>
      <div class="dropzone-sub">${th('ui.file_verranno_installati_istantaneamente_nella')} <code>mods</code> ${th('ui.server_minecraft')}</div>
    `;
  }

  // Clear input
  const fileInput = document.getElementById('mods-file-input');
  if (fileInput) fileInput.value = '';

  if (successCount > 0) {
    if (currentServerState === 'ONLINE') {
      notifyModsRestartRequired();
    }
    if (typeof showToast === 'function') {
      showToast(t('msg.installate_successo_mod_sul_server', successCount), 'success');
    }
    await loadModsList();
  } else {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_caricamento_mod', lastErr || t('msg.operazione_fallita')), 'error');
    }
  }
}

function initModsDropzone() {
  const dropzone = document.getElementById('mods-dropzone');
  if (!dropzone) return;

  ['dragenter', 'dragover'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.add('dragover');
    }, false);
  });

  ['dragleave', 'drop'].forEach(eventName => {
    dropzone.addEventListener(eventName, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropzone.classList.remove('dragover');
    }, false);
  });

  dropzone.addEventListener('drop', (e) => {
    const dt = e.dataTransfer;
    if (dt && dt.files && dt.files.length > 0) {
      handleModFilesSelect(dt.files);
    }
  }, false);
}

async function openModsFolderOnPc() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  if (window.chrome && window.chrome.webview) {
    try {
      window.chrome.webview.postMessage(JSON.stringify({
        action: 'open_folder',
        folder_type: 'mods'
      }));
      showToast(t('msg.cartella_mods_aperta_esplora_file'), 'success');
      return;
    } catch (e) {
      console.warn('Errore postMessage open_folder mods:', e);
    }
  }

  try {
    const res = await fetch('/api/mods/open_folder', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(t('msg.cartella_mods_aperta_esplora_file'), 'success');
    } else {
      showToast(`${data.detail || data.message || t('msg.impossibile_aprire_cartella_mods')}`, 'error');
    }
  } catch (err) {
    showToast(t('msg.errore_connessione', err.message), 'error');
  }
}

// =========================================================
// TAB 7: GESTIONE SERVER PROPERTIES (PC ONLY)
// =========================================================
let currentServerProperties = {};
let isPropertiesRawMode = false;

function getCheckboxVal(id, defaultVal = 'false') {
  const el = document.getElementById(id);
  return el ? (el.checked ? 'true' : 'false') : defaultVal;
}

function getInputVal(id, defaultVal = '') {
  const el = document.getElementById(id);
  return el ? el.value.trim() : defaultVal;
}

function setCheckbox(id, checked) {
  const el = document.getElementById(id);
  if (el) el.checked = !!checked;
}

function setInputVal(id, val) {
  const el = document.getElementById(id);
  if (el) el.value = val !== undefined && val !== null ? val : '';
}

async function loadServerProperties(isManual = false) {
  try {
    const res = await fetch('/api/server/properties');
    if (res.ok) {
      const data = await res.json();
      currentServerProperties = data.properties || {};
      populatePropertiesForm(currentServerProperties);
      const rawTextarea = document.getElementById('properties-raw-text');
      if (rawTextarea) {
        rawTextarea.value = data.raw || '';
      }

      // Update backup status button and tag
      const restoreBtn = document.getElementById('btn-restore-props-backup');
      const backupTag = document.getElementById('prop-backup-status-tag');
      if (restoreBtn) {
        restoreBtn.style.display = data.backup_exists ? 'inline-flex' : 'none';
      }
      if (backupTag) {
        backupTag.style.display = data.backup_exists ? 'inline-block' : 'none';
      }

      if (isManual && typeof showToast === 'function') {
        showToast(t('msg.proprieta_server_ricaricate_dal_file'), 'info');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('msg.impossibile_leggere_server_properties'), 'error');
      }
    }
  } catch (err) {
    console.error('Errore nel caricamento di server.properties:', err);
    if (isManual && typeof showToast === 'function') {
      showToast((t('msg.errore_rete_2') + " ") + err.message, 'error');
    }
  }
}

function populatePropertiesForm(props) {
  // Gameplay
  const diffEl = document.getElementById('prop-difficulty');
  if (diffEl) diffEl.value = props['difficulty'] || 'normal';

  const gmEl = document.getElementById('prop-gamemode');
  if (gmEl) gmEl.value = props['gamemode'] || 'survival';

  setCheckbox('prop-hardcore', props['hardcore'] === 'true');
  setCheckbox('prop-pvp', props['pvp'] !== 'false');
  setCheckbox('prop-allow-flight', props['allow-flight'] === 'true');
  setCheckbox('prop-enable-command-block', props['enable-command-block'] === 'true');
  setCheckbox('prop-force-gamemode', props['force-gamemode'] === 'true');

  // Mobs & Entities
  setCheckbox('prop-spawn-monsters', props['spawn-monsters'] !== 'false');
  setCheckbox('prop-spawn-animals', props['spawn-animals'] !== 'false');
  setCheckbox('prop-spawn-npcs', props['spawn-npcs'] !== 'false');
  setInputVal('prop-spawn-protection', props['spawn-protection'] || '16');
  setCheckbox('prop-allow-nether', props['allow-nether'] !== 'false');

  // World & Performance
  setInputVal('prop-motd', props['motd'] || '');
  setInputVal('prop-level-name', props['level-name'] || 'world');
  setInputVal('prop-level-seed', props['level-seed'] || '');
  setCheckbox('prop-generate-structures', props['generate-structures'] !== 'false');
  setInputVal('prop-view-distance', props['view-distance'] || '10');
  setInputVal('prop-simulation-distance', props['simulation-distance'] || '8');
  setInputVal('prop-max-tick-time', props['max-tick-time'] || '60000');
  setCheckbox('prop-sync-chunk-writes', props['sync-chunk-writes'] === 'true');

  // Network & Players
  setInputVal('prop-server-port', props['server-port'] || '25565');
  setInputVal('prop-server-ip', props['server-ip'] || '');
  setInputVal('prop-max-players', props['max-players'] || '20');
  setInputVal('prop-player-idle-timeout', props['player-idle-timeout'] || '0');
  setCheckbox('prop-online-mode', props['online-mode'] !== 'false');
  setCheckbox('prop-white-list', props['white-list'] === 'true');
  setCheckbox('prop-enforce-whitelist', props['enforce-whitelist'] === 'true');
  setCheckbox('prop-hide-online-players', props['hide-online-players'] === 'true');

  // RCON
  setCheckbox('prop-enable-rcon', props['enable-rcon'] === 'true');
  setInputVal('prop-rcon-port', props['rcon.port'] || '25575');
  setInputVal('prop-rcon-password', props['rcon.password'] || '');
  setCheckbox('prop-broadcast-rcon-to-ops', props['broadcast-rcon-to-ops'] === 'true');
}

async function saveServerProperties(andRestart = false) {
  // If in Raw Editor mode, route to saveRawProperties directly to prevent overwriting with old form fields
  if (isPropertiesRawMode) {
    await saveRawProperties(andRestart);
    return;
  }

  const props = { ...currentServerProperties };

  // Gameplay
  props['difficulty'] = getInputVal('prop-difficulty', 'normal');
  props['gamemode'] = getInputVal('prop-gamemode', 'survival');
  props['hardcore'] = getCheckboxVal('prop-hardcore');
  props['pvp'] = getCheckboxVal('prop-pvp');
  props['allow-flight'] = getCheckboxVal('prop-allow-flight');
  props['enable-command-block'] = getCheckboxVal('prop-enable-command-block');
  props['force-gamemode'] = getCheckboxVal('prop-force-gamemode');

  // Mobs & Entities
  props['spawn-monsters'] = getCheckboxVal('prop-spawn-monsters');
  props['spawn-animals'] = getCheckboxVal('prop-spawn-animals');
  props['spawn-npcs'] = getCheckboxVal('prop-spawn-npcs');
  props['spawn-protection'] = getInputVal('prop-spawn-protection', '16');
  props['allow-nether'] = getCheckboxVal('prop-allow-nether');

  // World & Performance
  props['motd'] = getInputVal('prop-motd', '');
  props['level-name'] = getInputVal('prop-level-name', 'world');
  props['level-seed'] = getInputVal('prop-level-seed', '');
  props['generate-structures'] = getCheckboxVal('prop-generate-structures');
  props['view-distance'] = getInputVal('prop-view-distance', '10');
  props['simulation-distance'] = getInputVal('prop-simulation-distance', '8');
  props['max-tick-time'] = getInputVal('prop-max-tick-time', '60000');
  props['sync-chunk-writes'] = getCheckboxVal('prop-sync-chunk-writes');

  // Network & Players
  props['server-port'] = getInputVal('prop-server-port', '25565');
  props['server-ip'] = getInputVal('prop-server-ip', '');
  props['max-players'] = getInputVal('prop-max-players', '20');
  props['player-idle-timeout'] = getInputVal('prop-player-idle-timeout', '0');
  props['online-mode'] = getCheckboxVal('prop-online-mode');
  props['white-list'] = getCheckboxVal('prop-white-list');
  props['enforce-whitelist'] = getCheckboxVal('prop-enforce-whitelist');
  props['hide-online-players'] = getCheckboxVal('prop-hide-online-players');

  // RCON
  props['enable-rcon'] = getCheckboxVal('prop-enable-rcon');
  props['rcon.port'] = getInputVal('prop-rcon-port', '25575');
  props['rcon.password'] = getInputVal('prop-rcon-password', '');
  props['broadcast-rcon-to-ops'] = getCheckboxVal('prop-broadcast-rcon-to-ops');

  // Input Validation
  const serverPort = parseInt(props['server-port'], 10);
  if (isNaN(serverPort) || serverPort < 1 || serverPort > 65535) {
    if (typeof showToast === 'function') {
      showToast(t('msg.porta_server_valida_deve_essere'), 'error');
    }
    return;
  }

  const maxPlayers = parseInt(props['max-players'], 10);
  if (isNaN(maxPlayers) || maxPlayers < 1) {
    if (typeof showToast === 'function') {
      showToast(t('msg.max_giocatori_valido_deve_essere'), 'error');
    }
    return;
  }

  if (props['enable-rcon'] === 'true') {
    const rconPort = parseInt(props['rcon.port'], 10);
    if (isNaN(rconPort) || rconPort < 1 || rconPort > 65535) {
      if (typeof showToast === 'function') {
        showToast(t('msg.porta_rcon_valida_deve_essere'), 'error');
      }
      return;
    }
  }

  try {
    const res = await fetch('/api/server/properties', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ properties: props })
    });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      currentServerProperties = props;
      if (typeof showToast === 'function') {
        showToast(t('msg.proprieta_salvate_successo_server_properties'), 'success');
      }
      await loadServerProperties();
      if (andRestart) {
        if (typeof showToast === 'function') {
          showToast(t('msg.riavvio_server_corso_applicare_proprieta'), 'info');
        }
        restartServer(10);
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('msg.errore_nel_salvataggio', data.detail || data.message || t('actions.errore_sconosciuto')), 'error');
      }
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_durante_salvataggio', err.message), 'error');
    }
  }
}

async function restorePropertiesBackup() {
  if (!confirm(t('msg.sei_sicuro_voler_ripristinare_file'))) {
    return;
  }

  try {
    const res = await fetch('/api/server/properties/restore-backup', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      if (typeof showToast === 'function') {
        showToast(t('msg.file_server_properties_ripristinato_successo'), 'success');
      }
      await loadServerProperties();
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.detail || data.message || t('msg.impossibile_ripristinare_backup')}`, 'error');
      }
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_durante_ripristino', err.message), 'error');
    }
  }
}

function copyRawPropertiesText() {
  const rawTextarea = document.getElementById('properties-raw-text');
  if (!rawTextarea || !rawTextarea.value) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_contenuto_copiare'), 'warning');
    return;
  }
  navigator.clipboard.writeText(rawTextarea.value).then(() => {
    if (typeof showToast === 'function') showToast(t('msg.testo_server_properties_copiato_negli'), 'success');
  }).catch(() => {
    rawTextarea.select();
    document.execCommand('copy');
    if (typeof showToast === 'function') showToast(t('msg.testo_server_properties_copiato'), 'success');
  });
}

function filterPropertiesForm() {
  const searchInput = document.getElementById('prop-search-input');
  const clearBtn = document.getElementById('btn-clear-prop-search');
  const q = searchInput ? searchInput.value.toLowerCase().trim() : '';

  if (clearBtn) {
    clearBtn.style.display = q ? 'inline-block' : 'none';
  }

  const cards = document.querySelectorAll('#properties-form-container .prop-card');
  cards.forEach(card => {
    let cardMatches = false;
    const rows = card.querySelectorAll('.prop-field-row, .prop-toggle-row');
    rows.forEach(row => {
      const text = row.innerText.toLowerCase();
      const inputs = row.querySelectorAll('input, select');
      let inputVal = '';
      inputs.forEach(inp => { inputVal += ' ' + (inp.id || '') + ' ' + (inp.value || ''); });
      const combined = (text + inputVal).toLowerCase();

      if (!q || combined.includes(q)) {
        row.style.display = '';
        cardMatches = true;
      } else {
        row.style.display = 'none';
      }
    });

    card.style.display = cardMatches ? '' : 'none';
  });
}

function clearPropSearch() {
  const searchInput = document.getElementById('prop-search-input');
  if (searchInput) searchInput.value = '';
  filterPropertiesForm();
}

async function saveRawProperties(andRestart = false) {
  const rawTextarea = document.getElementById('properties-raw-text');
  const rawText = rawTextarea ? rawTextarea.value : '';
  try {
    const res = await fetch('/api/server/properties', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ raw: rawText })
    });
    const data = await res.json();
    if (res.ok && data.status === 'ok') {
      if (typeof showToast === 'function') {
        showToast(t('msg.file_server_properties_salvato_direttamente'), 'success');
      }
      await loadServerProperties();
      if (andRestart) {
        if (typeof showToast === 'function') {
          showToast(t('msg.riavvio_server_corso_applicare_proprieta'), 'info');
        }
        restartServer(10);
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('msg.errore_nel_salvataggio', data.detail || data.message || t('actions.errore_sconosciuto')), 'error');
      }
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_durante_salvataggio', err.message), 'error');
    }
  }
}

function togglePropertiesEditorMode() {
  isPropertiesRawMode = !isPropertiesRawMode;
  const formBox = document.getElementById('properties-form-container');
  const rawBox = document.getElementById('properties-raw-container');
  const btn = document.getElementById('btn-toggle-prop-mode');

  if (isPropertiesRawMode) {
    if (formBox) formBox.style.display = 'none';
    if (rawBox) rawBox.style.display = 'block';
    if (btn) btn.innerHTML = t('msg.modalita_schede');
  } else {
    if (formBox) formBox.style.display = 'block';
    if (rawBox) rawBox.style.display = 'none';
    if (btn) btn.innerHTML = t('ui.modalita_editor_testo');
    // Reload properties so visual form fields reflect any raw text edits
    loadServerProperties();
  }
}

// =========================================================
// WHITELIST & BAN MANAGEMENT FUNCTIONS
// =========================================================
function updateWhitelistPill(isEnabled) {
  const pill = document.getElementById('whitelist-status-pill');
  const btn = document.getElementById('btn-toggle-whitelist');
  if (pill) {
    pill.className = isEnabled ? 'badge badge-emerald' : 'badge badge-secondary';
    pill.textContent = isEnabled ? t('msg.attiva_obbligatoria') : t('msg.disattivata_accesso_libero');
  }
  if (btn) {
    btn.className = isEnabled ? 'btn btn-xs btn-amber' : 'btn btn-xs btn-sapphire';
    btn.textContent = isEnabled ? t('msg.disattiva_whitelist') : t('ui.attiva_whitelist');
  }
}

async function toggleWhitelistStatus() {
  try {
    const res = await fetch('/api/whitelist/toggle', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      updateWhitelistPill(data.enabled);
      if (typeof showToast === 'function') {
        showToast(data.enabled ? t('msg.whitelist_attivata_solo_giocatori_autorizzati') : t('msg.whitelist_disattivata_accesso_consentito_tutti'), data.enabled ? 'success' : 'warning');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('msg.errore_durante_modifica_whitelist'), 'error');
      } else {
        alert(t('msg.errore_durante_modifica_whitelist'));
      }
    }
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_rete', e.message), 'error');
    } else {
      alert(t('msg.errore_rete', e.message));
    }
  }
}

async function loadWhitelist(isManual = false) {
  try {
    const res = await fetch('/api/whitelist');
    const data = await res.json();
    const countEl = document.getElementById('whitelist-count');
    const listEl = document.getElementById('whitelist-list');
    if (countEl) countEl.textContent = data.count || 0;
    if (typeof data.enabled === 'boolean') {
      updateWhitelistPill(data.enabled);
    }
    if (isManual && typeof showToast === 'function') {
      showToast(t('msg.whitelist_aggiornata'), 'info');
    }
    if (!listEl) return;
    listEl.innerHTML = '';
    if (!data.whitelist || data.whitelist.length === 0) {
      listEl.innerHTML = `<div style="color: #8a8c92; text-align: center; padding: 12px;">${th('ui.nessun_giocatore_whitelist')}</div>`;
      return;
    }
    data.whitelist.forEach(item => {
      const name = item.name || item.uuid || t('msg.sconosciuto');
      const row = document.createElement('div');
      row.style = 'display: flex; justify-content: space-between; align-items: center; background: #0b1120; padding: 6px 10px; border-radius: 6px; border: 1px solid #212023;';
      row.innerHTML = `
        <span style="color: #f4f4f5; font-weight: 500;">${escapeHtml(name)}</span>
        <button class="btn btn-xs btn-ruby" onclick="removeWhitelistPlayer('${escapeJsAttr(name)}')">${th('msg.rimuovi')}</button>
      `;
      listEl.appendChild(row);
    });
  } catch (err) {
    console.error('Error loading whitelist:', err);
  }
}

async function addWhitelistPlayer() {
  const input = document.getElementById('whitelist-add-input');
  const name = input ? input.value.trim() : '';
  if (!name) return;
  try {
    const res = await fetch('/api/whitelist/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_name: name })
    });
    if (res.ok) {
      input.value = '';
      loadWhitelist();
      if (typeof showToast === 'function') showToast(t('msg.aggiunto_alla_whitelist', name), 'success');
    } else {
      const err = await res.json();
      if (typeof showToast === 'function') showToast(`${err.detail || t('msg.errore_durante_aggiunta_alla_whitelist')}`, 'error');
      else alert(`${err.detail || t('msg.errore_durante_aggiunta_alla_whitelist')}`);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(t('msg.errore_rete', e.message), 'error');
    else alert(t('msg.errore_rete', e.message));
  }
}

async function removeWhitelistPlayer(name) {
  if (!confirm(t('msg.sei_sicuro_voler_rimuovere_dalla', name))) return;
  try {
    const res = await fetch('/api/whitelist/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_name: name })
    });
    if (res.ok) {
      loadWhitelist();
      if (typeof showToast === 'function') showToast(t('msg.rimosso_dalla_whitelist', name), 'info');
    } else {
      const err = await res.json();
      if (typeof showToast === 'function') showToast(`${err.detail || t('msg.errore')}`, 'error');
      else alert(`${err.detail || t('msg.errore')}`);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(t('msg.errore_rete', e.message), 'error');
    else alert(t('msg.errore_rete', e.message));
  }
}

async function loadBannedPlayers(isManual = false) {
  try {
    const res = await fetch('/api/banned-players');
    const data = await res.json();
    const countEl = document.getElementById('bans-count');
    const listEl = document.getElementById('bans-list');
    if (countEl) countEl.textContent = data.count || 0;
    if (isManual && typeof showToast === 'function') {
      showToast(t('msg.elenco_giocatori_bannati_aggiornato'), 'info');
    }
    if (!listEl) return;
    listEl.innerHTML = '';
    if (!data.bans || data.bans.length === 0) {
      listEl.innerHTML = `<div style="color: #8a8c92; text-align: center; padding: 12px;">${th('ui.nessun_giocatore_bannato')}</div>`;
      return;
    }
    data.bans.forEach(item => {
      const name = item.name || item.uuid || t('msg.sconosciuto');
      const reason = item.reason || t('msg.bannato');
      const row = document.createElement('div');
      row.style = 'display: flex; justify-content: space-between; align-items: center; background: #0b1120; padding: 6px 10px; border-radius: 6px; border: 1px solid #212023;';
      row.innerHTML = `
        <div style="display: flex; flex-direction: column;">
          <span style="color: #f0788c; font-weight: 500;">${escapeHtml(name)}</span>
          <span style="color: #9a9ca2; font-size: 10px;">${escapeHtml(reason)}</span>
        </div>
        <button class="btn btn-xs btn-emerald" onclick="removeBanPlayer('${escapeJsAttr(name)}')">${th('msg.sbanna')}</button>
      `;
      listEl.appendChild(row);
    });
  } catch (err) {
    console.error('Error loading bans:', err);
  }
}

async function addBanPlayer() {
  const input = document.getElementById('ban-add-input');
  const name = input ? input.value.trim() : '';
  if (!name) return;
  const reason = prompt(t('msg.motivo_ban', name), trDefault(t('pc.eco.violazione_regole_server')));
  if (reason === null) return;
  try {
    const res = await fetch('/api/ban/add', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_name: name, reason: reason })
    });
    if (res.ok) {
      input.value = '';
      loadBannedPlayers();
      if (typeof showToast === 'function') showToast(t('msg.bannato_dal_server_2', name), 'success');
      refreshPlayers();
    } else {
      const err = await res.json();
      if (typeof showToast === 'function') showToast(`${err.detail || t('msg.errore_durante_ban')}`, 'error');
      else alert(`${err.detail || t('msg.errore_durante_ban')}`);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(t('msg.errore_rete', e.message), 'error');
    else alert(t('msg.errore_rete', e.message));
  }
}

async function removeBanPlayer(name) {
  if (!confirm(t('msg.sei_sicuro_voler_sbannare', name))) return;
  try {
    const res = await fetch('/api/ban/remove', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ player_name: name })
    });
    if (res.ok) {
      loadBannedPlayers();
      if (typeof showToast === 'function') showToast(t('msg.sbannato_successo', name), 'success');
    } else {
      const err = await res.json();
      if (typeof showToast === 'function') showToast(`${err.detail || t('msg.errore')}`, 'error');
      else alert(`${err.detail || t('msg.errore')}`);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(t('msg.errore_rete', e.message), 'error');
    else alert(t('msg.errore_rete', e.message));
  }
}

document.addEventListener('DOMContentLoaded', () => {
  loadWhitelist();
  loadBannedPlayers();
});

// =========================================================
// 24-HOUR METRICS HISTORY & CHART
// =========================================================
let metrics24hData = [];
let activeChartSeries = {
  ram: true,
  cpu: true,
  players: true,
  tps: true
};
let metrics24hHoverIndex = -1;
let metrics24hAutoRefreshTimer = null;

async function loadMetrics24h(force = false) {
  try {
    const res = await fetch('/api/metrics/history-24h');
    if (!res.ok) return;
    const data = await res.json();
    metrics24hData = data.history || [];
    renderMetrics24hChart(metrics24hData);
    initMetrics24hHover();
    if (force && typeof showToast === 'function') {
      showToast(t('msg.grafici_24h_aggiornati_campioni_registrati', metrics24hData.length), 'info');
    }
  } catch (e) {
    console.error('Errore caricamento metriche 24h:', e);
    if (force && typeof showToast === 'function') {
      showToast(t('msg.errore_caricamento_metriche', e.message), 'error');
    }
  }
}

function toggleChartSeries(name) {
  if (activeChartSeries.hasOwnProperty(name)) {
    activeChartSeries[name] = !activeChartSeries[name];
    const pill = document.getElementById(`pill-series-${name}`);
    if (pill) {
      if (activeChartSeries[name]) pill.classList.add('active');
      else pill.classList.remove('active');
    }
    renderMetrics24hChart(metrics24hData);
  }
}

function initMetrics24hHover() {
  const canvas = document.getElementById('canvas-metrics-24h');
  const tooltip = document.getElementById('chart-24h-tooltip');
  if (!canvas || canvas._hoverInitialized) return;
  canvas._hoverInitialized = true;

  canvas.addEventListener('mousemove', (e) => {
    if (!metrics24hData || metrics24hData.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const clientX = (e.clientX - rect.left) * scaleX;
    const padLeft = 45;
    const padRight = 20;
    const chartW = canvas.width - padLeft - padRight;
    if (chartW <= 0) return;

    const relX = clientX - padLeft;
    if (relX < 0 || relX > chartW) {
      if (tooltip) tooltip.style.display = 'none';
      if (metrics24hHoverIndex !== -1) {
        metrics24hHoverIndex = -1;
        renderMetrics24hChart(metrics24hData);
      }
      return;
    }

    const stepX = metrics24hData.length > 1 ? chartW / (metrics24hData.length - 1) : chartW;
    const idx = Math.round(relX / stepX);
    const clampedIdx = Math.max(0, Math.min(metrics24hData.length - 1, idx));

    if (clampedIdx !== metrics24hHoverIndex) {
      metrics24hHoverIndex = clampedIdx;
      renderMetrics24hChart(metrics24hData);
    }

    const pt = metrics24hData[clampedIdx];
    if (pt && tooltip) {
      tooltip.style.display = 'block';
      const cssX = e.clientX - rect.left;
      const tooltipX = Math.min(rect.width - 190, Math.max(10, cssX + 15));
      const tooltipY = Math.max(10, e.clientY - rect.top - 80);
      tooltip.style.left = `${tooltipX}px`;
      tooltip.style.top = `${tooltipY}px`;
      tooltip.innerHTML = `
        <div style="font-weight:700; margin-bottom:4px; color:#e7e7e9; font-size:0.82rem;">${th('msg.ore', pt.time_str || '--:--')}</div>
        <div style="color:#8db6e8; margin:2px 0;">RAM: <b>${(pt.mc_ram_gb || 0).toFixed(1)} GB</b></div>
        <div style="color:#d9a441; margin:2px 0;">CPU: <b>${Math.round(pt.host_cpu || 0)}%</b></div>
        <div style="color:#7cb342; margin:2px 0;">${th('serverbar.giocatori')} <b>${pt.players || 0}</b></div>
        <div style="color:#9a9ca2; margin:2px 0;">TPS: <b>${(pt.tps || 20).toFixed(1)}</b></div>
      `;
    }
  });

  canvas.addEventListener('mouseleave', () => {
    metrics24hHoverIndex = -1;
    if (tooltip) tooltip.style.display = 'none';
    const hoverBar = document.getElementById('chart-24h-hover-bar');
    if (hoverBar) {
      hoverBar.innerHTML = `<span><i>${th('ui.passa_mouse_sul_grafico_visualizzare')}</i></span>`;
    }
    renderMetrics24hChart(metrics24hData);
  });
}

function exportMetrics24hCsv() {
  if (!metrics24hData || metrics24hData.length === 0) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_dato_storico_disponibile_esportare'), 'warning');
    return;
  }

  let csv = 'Timestamp,Ora,CPU_Host_Percent,RAM_Minecraft_GB,Giocatori,TPS\n';
  metrics24hData.forEach(d => {
    const ts = d.timestamp || '';
    const ora = d.time_str || '';
    const cpu = d.host_cpu !== undefined ? d.host_cpu : 0;
    const ram = d.mc_ram_gb !== undefined ? d.mc_ram_gb : 0;
    const players = d.players !== undefined ? d.players : 0;
    const tps = d.tps !== undefined ? d.tps : 20;
    csv += `"${ts}","${ora}",${cpu},${ram},${players},${tps}\n`;
  });

  const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = `metriche_server_24h_${new Date().toISOString().slice(0, 10)}.csv`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  if (typeof showToast === 'function') {
    showToast(t('msg.dati_storici_24h_esportati_successo'), 'success');
  }
}

function exportChart24hPng() {
  const canvas = document.getElementById('canvas-metrics-24h');
  if (!canvas) return;
  if (!metrics24hData || metrics24hData.length === 0) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_dato_presente_nel_grafico'), 'warning');
    return;
  }

  try {
    const dataUrl = canvas.toDataURL('image/png');
    const a = document.createElement('a');
    a.href = dataUrl;
    a.download = `grafico_prestazioni_24h_${new Date().toISOString().slice(0, 10)}.png`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    if (typeof showToast === 'function') {
      showToast(t('msg.grafico_salvato_successo_come_immagine'), 'success');
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_salvataggio_png', err.message), 'error');
    }
  }
}

async function resetMetrics24h() {
  if (!confirm(t('msg.sei_sicuro_voler_azzerare_tutti'))) {
    return;
  }

  try {
    const res = await fetch('/api/metrics/reset-24h', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      metrics24hData = [];
      metrics24hHoverIndex = -1;
      renderMetrics24hChart(metrics24hData);
      if (typeof showToast === 'function') {
        showToast(t('msg.storico_metriche_24_ore_azzerato'), 'info');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.message || t('msg.errore_durante_azzeramento')}`, 'error');
      }
    }
  } catch (err) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_rete', err.message), 'error');
    }
  }
}

function renderMetrics24hChart(data) {
  const canvas = document.getElementById('canvas-metrics-24h');
  const empty = document.getElementById('chart-24h-empty');
  if (!canvas) return;

  if (!data || data.length === 0) {
    if (empty) empty.style.display = 'flex';
    const elPeakRam = document.getElementById('stat-24h-peak-ram');
    const elPeakCpu = document.getElementById('stat-24h-peak-cpu');
    const elPeakPlayers = document.getElementById('stat-24h-peak-players');
    const elAvgTps = document.getElementById('stat-24h-avg-tps');
    if (elPeakRam) elPeakRam.textContent = '0.0 GB';
    if (elPeakCpu) elPeakCpu.textContent = '0%';
    if (elPeakPlayers) elPeakPlayers.textContent = '0';
    if (elAvgTps) elAvgTps.textContent = '20.0';
    const ctx = canvas.getContext('2d');
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    return;
  }
  if (empty) empty.style.display = 'none';

  // Compute stat summary
  let peakRam = 0;
  let peakCpu = 0;
  let peakPlayers = 0;
  let sumTps = 0;
  let countTps = 0;

  data.forEach(d => {
    if (d.mc_ram_gb > peakRam) peakRam = d.mc_ram_gb;
    if (d.host_cpu > peakCpu) peakCpu = d.host_cpu;
    if (d.players > peakPlayers) peakPlayers = d.players;
    if (typeof d.tps === 'number' && d.tps > 0) {
      sumTps += d.tps;
      countTps++;
    }
  });

  const avgTps = countTps > 0 ? (sumTps / countTps).toFixed(1) : '20.0';
  const elPeakRam = document.getElementById('stat-24h-peak-ram');
  const elPeakCpu = document.getElementById('stat-24h-peak-cpu');
  const elPeakPlayers = document.getElementById('stat-24h-peak-players');
  const elAvgTps = document.getElementById('stat-24h-avg-tps');

  if (elPeakRam) elPeakRam.textContent = `${peakRam.toFixed(1)} GB`;
  if (elPeakCpu) elPeakCpu.textContent = `${Math.round(peakCpu)}%`;
  if (elPeakPlayers) elPeakPlayers.textContent = `${peakPlayers}`;
  if (elAvgTps) elAvgTps.textContent = avgTps;

  // Draw on Canvas
  const ctx = canvas.getContext('2d');
  const parent = canvas.parentElement;
  const w = canvas.width = parent ? (parent.clientWidth - 20) : 900;
  const h = canvas.height = 300;

  ctx.clearRect(0, 0, w, h);

  const padLeft = 45;
  const padRight = 20;
  const padTop = 20;
  const padBottom = 35;
  const chartW = w - padLeft - padRight;
  const chartH = h - padTop - padBottom;

  if (chartW <= 0 || chartH <= 0) return;

  // Grid lines
  ctx.strokeStyle = 'rgba(255, 255, 255, 0.06)';
  ctx.lineWidth = 1;
  ctx.fillStyle = '#8a8c92';
  ctx.font = '11px Segoe UI, sans-serif';
  ctx.textAlign = 'right';

  for (let i = 0; i <= 4; i++) {
    const y = padTop + (chartH * (i / 4));
    ctx.beginPath();
    ctx.moveTo(padLeft, y);
    ctx.lineTo(w - padRight, y);
    ctx.stroke();

    const pctLabel = `${100 - (i * 25)}%`;
    ctx.fillText(pctLabel, padLeft - 8, y + 4);
  }

  // Draw X axis timestamps
  ctx.textAlign = 'center';
  const stepX = data.length > 1 ? chartW / (data.length - 1) : chartW;
  const labelInterval = Math.max(1, Math.floor(data.length / 6));

  data.forEach((d, idx) => {
    if (idx % labelInterval === 0 || idx === data.length - 1) {
      const x = padLeft + idx * stepX;
      ctx.fillText(d.time_str || '', x, h - 10);
    }
  });

  // Helper to draw a line series normalized
  function drawSeries(getValue, maxVal, color) {
    if (data.length === 0 || maxVal <= 0) return;
    ctx.beginPath();
    ctx.strokeStyle = color;
    ctx.lineWidth = 2.5;
    ctx.lineJoin = 'round';

    data.forEach((d, idx) => {
      const val = Math.max(0, getValue(d));
      const norm = Math.min(1.0, val / maxVal);
      const x = padLeft + idx * stepX;
      const y = padTop + chartH - (norm * chartH);

      if (idx === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    });
    ctx.stroke();
  }

  // 1. RAM (normalized to max ram or peak ram * 1.25)
  if (activeChartSeries.ram) {
    const maxRam = Math.max(8, peakRam * 1.2);
    drawSeries(d => d.mc_ram_gb || 0, maxRam, '#8db6e8');
  }

  // 2. CPU (0 - 100%)
  if (activeChartSeries.cpu) {
    drawSeries(d => d.host_cpu || 0, 100, '#d9a441');
  }

  // 3. Players
  if (activeChartSeries.players) {
    const maxPl = Math.max(5, peakPlayers * 1.25);
    drawSeries(d => d.players || 0, maxPl, '#7cb342');
  }

  // 4. TPS (0 - 20)
  if (activeChartSeries.tps) {
    drawSeries(d => d.tps || 20.0, 20.0, '#9a9ca2');
  }

  // Crosshair & Live Hover Points
  if (metrics24hHoverIndex >= 0 && metrics24hHoverIndex < data.length) {
    const pt = data[metrics24hHoverIndex];
    const hoverX = padLeft + metrics24hHoverIndex * stepX;

    // Vertical dashed crosshair line
    ctx.save();
    ctx.beginPath();
    ctx.setLineDash([4, 4]);
    ctx.strokeStyle = 'rgba(255, 255, 255, 0.45)';
    ctx.lineWidth = 1.5;
    ctx.moveTo(hoverX, padTop);
    ctx.lineTo(hoverX, padTop + chartH);
    ctx.stroke();
    ctx.restore();

    function drawPointCircle(val, maxVal, color) {
      if (maxVal <= 0) return;
      const norm = Math.min(1.0, Math.max(0, val) / maxVal);
      const cy = padTop + chartH - (norm * chartH);
      ctx.beginPath();
      ctx.arc(hoverX, cy, 5, 0, Math.PI * 2);
      ctx.fillStyle = color;
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = '#171719';
      ctx.stroke();
    }

    if (activeChartSeries.ram) {
      const maxRam = Math.max(8, peakRam * 1.2);
      drawPointCircle(pt.mc_ram_gb || 0, maxRam, '#8db6e8');
    }
    if (activeChartSeries.cpu) {
      drawPointCircle(pt.host_cpu || 0, 100, '#d9a441');
    }
    if (activeChartSeries.players) {
      const maxPl = Math.max(5, peakPlayers * 1.25);
      drawPointCircle(pt.players || 0, maxPl, '#7cb342');
    }
    if (activeChartSeries.tps) {
      drawPointCircle(pt.tps || 20.0, 20.0, '#9a9ca2');
    }

    const hoverBar = document.getElementById('chart-24h-hover-bar');
    if (hoverBar) {
      hoverBar.innerHTML = `
        <span style="color:#f4f4f5; font-weight:700;">${th('msg.ore', pt.time_str || '--:--')}</span>
        <span style="color:#8db6e8;">RAM: <b>${(pt.mc_ram_gb || 0).toFixed(1)} GB</b></span>
        <span style="color:#d9a441;">CPU: <b>${Math.round(pt.host_cpu || 0)}%</b></span>
        <span style="color:#7cb342;">${th('serverbar.giocatori')} <b>${pt.players || 0}</b></span>
        <span style="color:#9a9ca2;">TPS: <b>${(pt.tps || 20).toFixed(1)}</b></span>
      `;
    }
  }
}

// Window resize listener to keep 24h chart crisp and responsive
window.addEventListener('resize', () => {
  const tabMetrics = document.getElementById('tab-metrics-24h');
  if (tabMetrics && tabMetrics.classList.contains('active')) {
    renderMetrics24hChart(metrics24hData);
  }
});

// =========================================================
// CLOUD BACKUP SYNC HANDLERS
// =========================================================
async function loadCloudBackupConfig() {
  try {
    const res = await fetch('/api/backup/cloud-config');
    if (!res.ok) return;
    const data = await res.json();
    const input = document.getElementById('cfg-cloud-sync-dir');
    const badge = document.getElementById('cloud-sync-status-badge');
    if (input) input.value = data.cloud_sync_dir || '';
    if (badge) {
      if (data.cloud_sync_dir) {
        badge.className = 'badge badge-emerald';
        badge.textContent = t('ui.attivo');
      } else {
        badge.className = 'badge badge-secondary';
        badge.textContent = t('ui.configurato');
      }
    }
  } catch (e) {
    console.error('Errore lettura cloud config:', e);
  }
}

async function saveCloudBackupConfig() {
  const input = document.getElementById('cfg-cloud-sync-dir');
  if (!input) return;
  const val = input.value.trim();
  try {
    const res = await fetch('/api/backup/cloud-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ cloud_sync_dir: val })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(t('msg.configurazione_cartella_cloud_salvata'), 'success');
      loadCloudBackupConfig();
      loadBackupEstimate();   // spazio libero della nuova cartella di Drive
    } else {
      showToast(`${data.detail || t('msg.errore_nel_salvataggio_2')}`, 'error');
    }
  } catch (e) {
    showToast(t('msg.errore_rete', e), 'error');
  }
}

// =========================================================
// JVM RAM & AIKAR'S FLAGS TUNER
// =========================================================
// Valori letti dal server: si riscrive la RAM solo se l'utente li cambia (null = non ancora letti)
let loadedJvmRam = null;
let loadedJvmAikar = null;

function fillJvmRamOptions(ramSel, hostGb, currentGb) {
  // Da 2 GB fino alla RAM del PC meno 2 GB per Windows (prima la lista si fermava a 16 GB)
  const maxGb = Math.max(2, (hostGb || 16) - 2);
  const values = [];
  for (let v = 2; v <= maxGb; v += (v < 12 ? 1 : 2)) values.push(v);
  if (currentGb && !values.includes(currentGb)) values.push(currentGb);
  values.sort((a, b) => a - b);
  ramSel.innerHTML = values.map(v => `<option value="${v}">${v} GB</option>`).join('');
}

async function loadJvmTuning() {
  try {
    const res = await fetch('/api/server/jvm-tuning');
    if (!res.ok) return;
    const data = await res.json();
    const ramSel = document.getElementById('cfg-jvm-ram');
    const aikarChk = document.getElementById('cfg-jvm-aikar');
    const badge = document.getElementById('jvm-tuning-badge');

    if (ramSel) {
      fillJvmRamOptions(ramSel, data.host_ram_gb, data.ram_gb);
      if (data.ram_gb) ramSel.value = String(data.ram_gb);
    }
    if (aikarChk) aikarChk.checked = !!data.use_aikar_flags;
    if (badge) badge.textContent = data.ram_gb ? `${data.ram_gb} GB ${data.use_aikar_flags ? '(Aikar G1GC)' : ''}` : t('msg.impostata');
    loadedJvmRam = ramSel ? parseInt(ramSel.value, 10) : null;
    loadedJvmAikar = aikarChk ? aikarChk.checked : null;
  } catch (e) {
    console.error('Errore lettura JVM tuning:', e);
  }
}

// true se salvato o non cambiato, false se il server ha rifiutato (l'errore è già mostrato)
async function saveJvmTuningFromSettings() {
  const ramSel = document.getElementById('cfg-jvm-ram');
  const aikarChk = document.getElementById('cfg-jvm-aikar');
  if (!ramSel || loadedJvmRam === null) return true;

  const ram_gb = parseInt(ramSel.value, 10);
  const use_aikar_flags = aikarChk ? aikarChk.checked : true;
  if (!ram_gb || (ram_gb === loadedJvmRam && use_aikar_flags === loadedJvmAikar)) return true;

  try {
    const res = await fetch('/api/server/jvm-tuning', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ram_gb, use_aikar_flags })
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || data.status === 'error') {
      showToast((t('msg.ram_salvata') + " ") + (data.detail || data.message || res.status), 'error');
      return false;
    }
    loadedJvmRam = ram_gb;
    loadedJvmAikar = use_aikar_flags;
    const badge = document.getElementById('jvm-tuning-badge');
    if (badge) badge.textContent = `${ram_gb} GB ${use_aikar_flags ? '(Aikar G1GC)' : ''}`;
    if (data.restart_needed) showToast(t('msg.ram_impostata_gb_riavvia_server', ram_gb), 'info');
    return true;
  } catch (e) {
    showToast(t('msg.ram_salvata_errore_rete'), 'error');
    return false;
  }
}

// =========================================================
// STORAGE BREAKDOWN & OLD LOGS CLEANER
// =========================================================
function openStorageModal() {
  openModal('modal-storage');
  loadStorageBreakdown();
}

function closeStorageModal() {
  closeModal('modal-storage');
}

async function openLogsFolderInExplorer() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  try {
    const res = await fetch('/api/logs/open_folder', { method: 'POST' });
    if (res.ok) {
      showToast(t('msg.cartella_logs_aperta_esplora_risorse'), 'success');
    } else {
      showToast(t('msg.impossibile_aprire_cartella_logs'), 'error');
    }
  } catch (e) {
    showToast(t('tpl.errore', e), 'error');
  }
}

async function openCrashFolderInExplorer() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  try {
    const res = await fetch('/api/crash-reports/open_folder', { method: 'POST' });
    if (res.ok) {
      showToast(t('msg.cartella_crash_reports_aperta_esplora'), 'success');
    } else {
      showToast(t('msg.impossibile_aprire_cartella_crash_reports'), 'error');
    }
  } catch (e) {
    showToast(t('tpl.errore', e), 'error');
  }
}

async function loadStorageBreakdown() {
  try {
    const res = await fetch('/api/storage/breakdown');
    if (!res.ok) return;
    const data = await res.json();

    const totalEl = document.getElementById('storage-total-val');
    const filesEl = document.getElementById('storage-files-count');
    const cleanEl = document.getElementById('storage-cleanable-val');
    const cleanCountEl = document.getElementById('storage-cleanable-count');
    const worldEl = document.getElementById('storage-world-val');
    const worldDetailEl = document.getElementById('storage-world-detail');
    const diskValEl = document.getElementById('storage-disk-val');
    const diskDetailEl = document.getElementById('storage-disk-detail');

    const bWorld = document.getElementById('storage-breakdown-world');
    const bMods = document.getElementById('storage-breakdown-mods');
    const bLogs = document.getElementById('storage-breakdown-logs');
    const bCrashes = document.getElementById('storage-breakdown-crashes');

    const totalFiles = data.total_files !== undefined ? data.total_files : (data.files_count || 0);
    const cleanCount = data.cleanable_count !== undefined ? data.cleanable_count : (data.cleanable_files_count || 0);
    const cleanMb = data.cleanable_mb !== undefined ? data.cleanable_mb : (data.logs_cleanable_mb || 0);
    const crashesMb = data.crashes_mb !== undefined ? data.crashes_mb : (data.crash_reports_mb || 0);
    const worldGb = data.world_gb !== undefined ? data.world_gb : Number(((data.world_mb || 0) / 1024).toFixed(1));

    if (totalEl) totalEl.textContent = `${data.total_gb || 0} GB`;
    if (filesEl) filesEl.textContent = `${totalFiles.toLocaleString()} file`;
    if (cleanEl) cleanEl.textContent = `${cleanMb} MB`;
    if (cleanCountEl) cleanCountEl.textContent = t('msg.file_inutili', cleanCount);
    if (worldEl) worldEl.textContent = `${worldGb} GB`;
    if (worldDetailEl) worldDetailEl.textContent = t('msg.mb_regioni_mondi', (data.world_mb || 0).toLocaleString());

    if (diskValEl) diskValEl.textContent = t('msg.gb_liberi', data.disk_free_gb || 0);
    if (diskDetailEl) diskDetailEl.textContent = t('msg.gb_totali_usato', data.disk_total_gb || 0, data.disk_used_percent || 0);

    if (bWorld) bWorld.textContent = `${(data.world_mb || 0).toLocaleString()} MB (${worldGb} GB)`;
    if (bMods) bMods.textContent = `${(data.mods_mb || 0).toLocaleString()} MB`;
    if (bLogs) bLogs.textContent = `${(data.logs_mb || 0).toLocaleString()} MB`;
    if (bCrashes) bCrashes.textContent = `${crashesMb} MB`;

    // Multi-segment storage bar percentages
    const totMb = data.total_mb || 1;
    const wPct = Math.min(100, Math.max(1, ((data.world_mb || 0) / totMb) * 100));
    const mPct = Math.min(100, Math.max(0.5, ((data.mods_mb || 0) / totMb) * 100));
    const lPct = Math.min(100, Math.max(0.2, (((data.logs_mb || 0) + (data.crash_reports_mb || 0)) / totMb) * 100));
    const oPct = Math.max(0, 100 - (wPct + mPct + lPct));

    const barWorld = document.getElementById('storage-bar-world');
    const barMods = document.getElementById('storage-bar-mods');
    const barLogs = document.getElementById('storage-bar-logs');
    const barOther = document.getElementById('storage-bar-other');
    if (barWorld) barWorld.style.width = `${wPct.toFixed(1)}%`;
    if (barMods) barMods.style.width = `${mPct.toFixed(1)}%`;
    if (barLogs) barLogs.style.width = `${lPct.toFixed(1)}%`;
    if (barOther) barOther.style.width = `${oPct.toFixed(1)}%`;
  } catch (e) {
    console.error('Errore calcolo spazio storage:', e);
  }
}

async function executeStorageClean() {
  const btn = document.getElementById('btn-storage-clean');
  const resBox = document.getElementById('storage-clean-result');
  if (!confirm(t('msg.vuoi_eliminare_tutti_vecchi_file'))) {
    return;
  }

  if (btn) btn.disabled = true;
  try {
    const { ok, data } = await safeJsonFetch('/api/storage/clean-logs', { method: 'POST' });
    if (ok && data.status === 'success') {
      if (resBox) {
        resBox.style.display = 'block';
        resBox.style.background = 'rgba(124, 179, 66, 0.15)';
        resBox.style.border = '1px solid #7cb342';
        resBox.style.color = '#b5d98e';
        resBox.innerHTML = `<b>${th('msg.pulizia_completata')}</b> ${th('msg.liberati')} <b>${data.freed_mb} MB</b> ${th('msg.eliminando_vecchi_file_inutili', data.deleted_count)}`;
      }
      showToast(t('msg.pulizia_completata_liberati_mb', data.freed_mb), 'success');
      loadStorageBreakdown();
    } else {
      showToast(t('msg.errore_pulizia', data.message || data.detail || t('msg.operazione_fallita')), 'error');
    }
  } catch (e) {
    showToast(t('msg.errore_pulizia', e.message || e), 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// =========================================================
// BACKUP TABS & 1-CLICK RESTORE / ROLLBACK
// =========================================================
function switchBackupTab(tabId) {
  document.querySelectorAll('#modal-backup .modal-tab-btn').forEach(btn => btn.classList.remove('active'));
  document.querySelectorAll('#modal-backup .backup-tab-pane').forEach(pane => pane.style.display = 'none');

  const btn = document.getElementById(tabId.replace('btab-', 'btab-btn-'));
  if (btn) btn.classList.add('active');

  const pane = document.getElementById(tabId);
  if (pane) pane.style.display = 'block';

  if (tabId === 'btab-restore') {
    loadAvailableBackups();
  }
}

async function openBackupFolderInExplorer() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  try {
    const res = await fetch('/api/backup/open_folder', { method: 'POST' });
    if (res.ok) {
      showToast(t('msg.cartella_backup_aperta_esplora_risorse'), 'success');
    } else {
      showToast(t('msg.impossibile_aprire_cartella_backup_2'), 'error');
    }
  } catch (e) {
    showToast(t('tpl.errore', e), 'error');
  }
}

async function deleteBackupItem(backupId, filename) {
  if (!confirm(t('msg.sei_sicuro_voler_eliminare_definitivamente_4', filename))) {
    return;
  }
  try {
    const res = await fetch('/api/backup/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ id: backupId, path: backupId })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(t('msg.archivio_eliminato_successo', filename), 'success');
      loadAvailableBackups();
      try {
        const sRes = await fetch('/api/backup/status');
        if (sRes.ok) {
          const bData = await sRes.json();
          renderBackupStatus(bData);
        }
      } catch (_) {}
    } else {
      showToast(t('tpl.errore', data.message || data.detail || t('msg.impossibile_eliminare_backup')), 'error');
    }
  } catch (e) {
    showToast(t('msg.errore_rete', e), 'error');
  }
}

async function loadAvailableBackups() {
  const container = document.getElementById('available-backups-container');
  if (!container) return;
  container.innerHTML = `<div style="text-align: center; color: #8a8c92; padding: 20px;">${th('msg.ricerca_backup_corso')}</div>`;

  try {
    const res = await fetch('/api/backup/list');
    if (!res.ok) throw new Error(t('msg.impossibile_recuperare_backup'));
    const data = await res.json();
    const backups = data.backups || [];

    if (backups.length === 0) {
      container.innerHTML = `
        <div style="text-align: center; color: #9a9ca2; padding: 24px; background: rgba(0,0,0,0.2); border-radius: 8px;">
          <p style="margin: 0; font-size: 0.9rem;">${th('msg.nessun_archivio_backup_trovato_sul')}</p>
          <small style="color: #8a8c92;">${th('msg.esegui_backup_dalla_scheda_crea')}</small>
        </div>
      `;
      return;
    }

    const checkText = cloudBackupCheckText(data.cloud_check);
    let html = checkText
      ? `<div class="backup-cloud-check">⚠ ${escapeHtml(checkText)} ${th('msg.backup_drive_come_aggiornare')}</div>`
      : '';
    backups.forEach(b => {
      const isCloud = b.location === 'cloud' || (b.location_label && b.location_label.toLowerCase().includes('cloud'));
      const locBadge = isCloud
        ? '<span class="badge badge-sapphire" style="font-size: 0.7rem;">Google Drive / Cloud</span>'
        : '<span class="badge badge-emerald" style="font-size: 0.7rem;">Desktop</span>';

      const backupDate = b.date || b.modified || '-';

      html += `
        <div style="display: flex; align-items: center; justify-content: space-between; padding: 12px; background: rgba(19, 18, 23, 0.7); border: 1px solid rgba(255,255,255,0.08); border-radius: 8px; gap: 10px; flex-wrap: wrap;">
          <div style="display: flex; align-items: center; gap: 10px;">
            <span style="font-size: 1.4rem;"></span>
            <div>
              <div style="display: flex; align-items: center; gap: 8px;">
                <span style="font-weight: 700; font-size: 0.92rem; color: #f4f4f5;">${escapeHtml(b.filename)}</span>
                ${locBadge}
              </div>
              <div style="font-size: 0.76rem; color: #9a9ca2; margin-top: 2px;">
                ${th('msg.mb_creato', b.size_mb, escapeHtml(backupDate))}
              </div>
            </div>
          </div>
          <div style="display: flex; gap: 8px; align-items: center;">
            <span class="badge ${isCloud ? 'badge-sapphire' : 'badge-emerald'}" style="font-size: 0.72rem; font-weight: 700; padding: 4px 8px;">
              ${isCloud ? 'Drive' : 'Desktop'}
            </span>
            <button class="btn btn-xs btn-sapphire" onclick="downloadBackupItem('${escapeJsAttr(b.id || b.filename)}', '${escapeJsAttr(b.filename)}')">
              ${th('ui.scarica')}
            </button>
            <button class="btn btn-xs btn-amber" onclick="confirmRestoreBackup('${escapeJsAttr(b.path)}', '${escapeJsAttr(b.filename)}')">
              ${th('msg.ripristina')}
            </button>
            <button class="btn btn-xs btn-ruby" onclick="deleteBackupItem('${escapeJsAttr(b.id || b.path)}', '${escapeJsAttr(b.filename)}')">
              ${th('msg.elimina')}
            </button>
          </div>
        </div>
      `;
    });
    container.innerHTML = html;
  } catch (e) {
    container.innerHTML = `<div style="color: #f0788c; text-align: center; padding: 16px;">${th('tpl.errore', e)}</div>`;
  }
}

async function confirmRestoreBackup(sourcePath, filename) {
  if (currentServerState !== 'STOPPED') {
    showToast(t('msg.server_minecraft_attualmente_acceso_spegni'), 'warning');
    return;
  }

  const ok = confirm(t('msg.conferma_ripristino_backup_sei_sicuro', filename));
  if (!ok) return;

  const pBox = document.getElementById('restore-progress-box');
  const pTitle = document.getElementById('restore-status-title');
  const pBar = document.getElementById('restore-progress-bar');
  const pPct = document.getElementById('restore-pct');
  const pFiles = document.getElementById('restore-files-stat');

  if (pBox) pBox.style.display = 'block';
  if (pTitle) pTitle.textContent = t('msg.avvio_estrazione_archivio');

  try {
    const { ok, data } = await safeJsonFetch('/api/backup/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_path: sourcePath })
    });
    if (!ok || data.status === 'error') {
      showToast(t('msg.errore_ripristino', data.detail || data.message || t('msg.operazione_riuscita')), 'error');
      if (pBox) pBox.style.display = 'none';
      return;
    }

    startBackupPolling();
    showToast(t('msg.ripristino_avviato', filename), 'info');
  } catch (e) {
    showToast(t('msg.errore_ripristino', e.message || e), 'error');
    if (pBox) pBox.style.display = 'none';
  }
}

// =========================================================
// INTERACTIVE PLAYER QUICK ACTIONS
// =========================================================
let activePlayerForActions = null;

function openPlayerActionsModal(playerName, avatarUrl) {
  activePlayerForActions = playerName;
  const nameEl = document.getElementById('paction-player-name');
  const avEl = document.getElementById('paction-avatar');
  if (nameEl) nameEl.textContent = playerName;
  if (avEl) {
    avEl.src = avatarUrl || `https://mc-heads.net/avatar/${encodeURIComponent(playerName)}/40`;
    avEl.onerror = () => { avEl.src = 'data:image/svg+xml;utf8,<svg xmlns="http://www.w3.org/2000/svg" width="40" height="40"><rect width="40" height="40" fill="%232563eb"/></svg>'; };
  }
  openModal('modal-player-actions');
}

function closePlayerActionsModal() {
  closeModal('modal-player-actions');
  activePlayerForActions = null;
}

async function submitPlayerQuickAction(action, value = '') {
  if (!activePlayerForActions) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_giocatore_selezionato'), 'warning');
    else alert(t('msg.nessun_giocatore_selezionato'));
    return;
  }

  if (action === 'clear_inventory') {
    if (!confirm(t('msg.vuoi_davvero_svuotare_l_inventario', activePlayerForActions))) return;
  }

  try {
    const res = await fetch('/api/player/quick-action', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        player: activePlayerForActions,
        player_name: activePlayerForActions,
        action: action,
        value: value
      })
    });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      showToast(t('msg.azione_eseguita', activePlayerForActions), 'success');
      if (['kick', 'ban'].includes(action)) {
        closePlayerActionsModal();
        refreshPlayers();
      }
    } else {
      if (typeof showToast === 'function') showToast(`${data.message || data.detail || t('msg.errore_esecuzione_comando')}`, 'error');
      else alert(`${data.message || data.detail || t('msg.errore_esecuzione_comando')}`);
    }
  } catch (e) {
    if (typeof showToast === 'function') showToast(t('msg.errore_rete', e), 'error');
    else alert(t('tpl.errore', e));
  }
}

function submitCustomGiveItem() {
  const inp = document.getElementById('paction-custom-item');
  if (!inp || !inp.value.trim()) {
    if (typeof showToast === 'function') showToast(t('msg.inserisci_nome_dell_oggetto_regalare'), 'warning');
    else alert(t('msg.inserisci_nome_dell_oggetto_regalare'));
    return;
  }
  submitPlayerQuickAction('give', inp.value.trim());
  inp.value = '';
}

function promptPlayerKick() {
  if (!activePlayerForActions) return;
  const reason = prompt(t('msg.espellere_motivo_opzionale', activePlayerForActions), "Espulso dall'amministratore");
  if (reason !== null) {
    submitPlayerQuickAction('kick', reason);
  }
}

function promptPlayerBan() {
  if (!activePlayerForActions) return;
  const reason = prompt(t('msg.bannare_permanentemente_motivo_opzionale', activePlayerForActions), "Bannato dall'amministratore");
  if (reason !== null) {
    submitPlayerQuickAction('ban', reason);
  }
}

// =========================================================
// CONFIG FILES EDITOR (PC WEB & REMOTE)
// =========================================================
let currentSelectedConfigFile = '';
let allConfigFilesCache = [];

function openConfigEditorModal() {
  openModal('modal-config-editor');
  const filterInput = document.getElementById('config-filter-input');
  if (filterInput) filterInput.value = '';
  loadConfigFilesList();
}

function closeConfigEditorModal() {
  closeModal('modal-config-editor');
}

async function loadConfigFilesList() {
  const sel = document.getElementById('config-editor-file-select');
  const txt = document.getElementById('config-editor-textarea');
  const msg = document.getElementById('config-editor-status-msg');
  if (!sel) return;

  sel.innerHTML = `<option value="">${th('ui.caricamento_elenco_file')}</option>`;
  if (msg) msg.style.display = 'none';

  try {
    const res = await fetch('/api/config-files');
    if (res.ok) {
      const data = await res.json();
      allConfigFilesCache = data.files || [];
      renderConfigFilesSelect(allConfigFilesCache);

      // Auto-select server.properties by default if present and nothing was selected
      if (!currentSelectedConfigFile || !allConfigFilesCache.some(f => f.rel_path === currentSelectedConfigFile)) {
        const defaultFile = allConfigFilesCache.find(f => f.name === 'server.properties') || allConfigFilesCache[0];
        if (defaultFile) {
          sel.value = defaultFile.rel_path;
          onConfigFileSelected(defaultFile.rel_path);
        }
      } else {
        sel.value = currentSelectedConfigFile;
        onConfigFileSelected(currentSelectedConfigFile);
      }
    } else {
      sel.innerHTML = `<option value="">${th('msg.errore_caricamento_lista_file')}</option>`;
    }
  } catch (e) {
    sel.innerHTML = `<option value="">${th('msg.errore_rete_3')}</option>`;
  }
}

function filterConfigFilesList(query) {
  const q = (query || '').trim().toLowerCase();
  if (!q) {
    renderConfigFilesSelect(allConfigFilesCache);
    return;
  }
  const filtered = allConfigFilesCache.filter(f => 
    (f.name && f.name.toLowerCase().includes(q)) || 
    (f.rel_path && f.rel_path.toLowerCase().includes(q)) ||
    (f.category && f.category.toLowerCase().includes(q))
  );
  renderConfigFilesSelect(filtered);
}

function renderConfigFilesSelect(files) {
  const sel = document.getElementById('config-editor-file-select');
  if (!sel) return;

  if (!files || files.length === 0) {
    sel.innerHTML = `<option value="">${th('msg.nessun_file_trovato')}</option>`;
    return;
  }

  sel.innerHTML = `<option value="">${th('msg.seleziona_file_modificare')}</option>`;

  // Group by category
  const categories = {};
  files.forEach(f => {
    const cat = f.category || t('msg.altri_file');
    if (!categories[cat]) categories[cat] = [];
    categories[cat].push(f);
  });

  for (const cat of Object.keys(categories)) {
    const group = document.createElement('optgroup');
    group.label = `${cat} (${categories[cat].length})`;
    categories[cat].forEach(f => {
      const opt = document.createElement('option');
      opt.value = f.rel_path;
      const bakBadge = f.backup_exists ? ' [.bak]' : '';
      opt.textContent = `${f.name} - ${f.size_str}${bakBadge}`;
      group.appendChild(opt);
    });
    sel.appendChild(group);
  }

  if (currentSelectedConfigFile) {
    sel.value = currentSelectedConfigFile;
  }
}

function updateConfigFileStats() {
  const txt = document.getElementById('config-editor-textarea');
  const stats = document.getElementById('config-editor-file-stats');
  if (!stats) return;
  if (!txt || !currentSelectedConfigFile) {
    stats.textContent = t('ui.seleziona_file_2');
    return;
  }

  const val = txt.value || '';
  const lines = val ? val.split('\n').length : 0;
  const chars = val.length;
  const bytes = new Blob([val]).size;
  const kb = (bytes / 1024).toFixed(1);
  stats.textContent = t('msg.righe_caratteri_kb', currentSelectedConfigFile, lines, chars.toLocaleString(), kb);
}

async function onConfigFileSelected(relPath) {
  const txt = document.getElementById('config-editor-textarea');
  const loading = document.getElementById('config-editor-loading');
  const msg = document.getElementById('config-editor-status-msg');
  const restoreBtn = document.getElementById('btn-config-editor-restore');
  const backupTag = document.getElementById('config-backup-status-tag');

  if (!relPath) {
    if (txt) txt.value = '';
    currentSelectedConfigFile = '';
    if (restoreBtn) restoreBtn.style.display = 'none';
    if (backupTag) backupTag.style.display = 'none';
    updateConfigFileStats();
    return;
  }

  currentSelectedConfigFile = relPath;
  if (loading) loading.style.display = 'flex';
  if (msg) msg.style.display = 'none';

  try {
    const res = await fetch(`/api/config-file?path=${encodeURIComponent(relPath)}`);
    if (res.ok) {
      const data = await res.json();
      if (txt) txt.value = data.content || '';
      const hasBak = !!data.backup_exists;
      if (restoreBtn) restoreBtn.style.display = hasBak ? 'inline-flex' : 'none';
      if (backupTag) {
        backupTag.style.display = hasBak ? 'inline-block' : 'none';
        if (hasBak && data.backup_size_str) {
          backupTag.textContent = t('msg.backup_bak_disponibile', data.backup_size_str);
        } else {
          backupTag.textContent = t('ui.backup_bak_disponibile');
        }
      }
      updateConfigFileStats();
    } else {
      if (txt) txt.value = t('msg.errore_durante_lettura_file_dal');
      if (restoreBtn) restoreBtn.style.display = 'none';
      if (backupTag) backupTag.style.display = 'none';
    }
  } catch (e) {
    if (txt) txt.value = t('msg.errore_rete', e);
    if (restoreBtn) restoreBtn.style.display = 'none';
    if (backupTag) backupTag.style.display = 'none';
  } finally {
    if (loading) loading.style.display = 'none';
  }
}

async function copyConfigFileContent() {
  const txt = document.getElementById('config-editor-textarea');
  if (!txt || !txt.value) {
    showToast(t('msg.nessun_testo_copiare_2'), 'warning');
    return;
  }
  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(txt.value);
    } else {
      txt.select();
      document.execCommand('copy');
    }
    showToast(t('msg.testo_copiato_negli_appunti_caratteri', txt.value.length), 'success');
  } catch (e) {
    showToast(t('msg.impossibile_copiare_negli_appunti'), 'error');
  }
}

async function restoreConfigFileBackup() {
  if (!currentSelectedConfigFile) {
    showToast(t('msg.nessun_file_selezionato'), 'warning');
    return;
  }
  const confirmed = confirm(t('msg.sei_sicuro_voler_ripristinare_file_2', currentSelectedConfigFile));
  if (!confirmed) return;

  const btn = document.getElementById('btn-config-editor-restore');
  const txt = document.getElementById('config-editor-textarea');
  const msg = document.getElementById('config-editor-status-msg');
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/api/config-file/restore', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path: currentSelectedConfigFile })
    });
    if (res.ok) {
      const data = await res.json();
      if (txt) txt.value = data.content || '';
      updateConfigFileStats();
      if (msg) {
        msg.textContent = t('msg.backup_ripristinato_successo', currentSelectedConfigFile);
        msg.style.color = '#8db6e8';
        msg.style.display = 'block';
      }
      showToast(t('msg.backup_ripristinato_successo', currentSelectedConfigFile), 'success');
    } else {
      const err = await res.json().catch(() => ({}));
      showToast(t('msg.errore_ripristino', err.message || err.detail || t('msg.operazione_fallita')), 'error');
    }
  } catch (e) {
    showToast(t('msg.errore_rete_durante_ripristino', e), 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function saveCurrentConfigFile() {
  if (!currentSelectedConfigFile) {
    showToast(t('msg.seleziona_prima_file_modificare'), 'warning');
    return;
  }

  const txt = document.getElementById('config-editor-textarea');
  const btn = document.getElementById('btn-config-editor-save');
  const msg = document.getElementById('config-editor-status-msg');
  const restoreBtn = document.getElementById('btn-config-editor-restore');
  const backupTag = document.getElementById('config-backup-status-tag');
  if (!txt) return;

  const content = txt.value;
  if (btn) btn.disabled = true;

  try {
    const res = await fetch('/api/config-file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        path: currentSelectedConfigFile,
        content: content
      })
    });

    if (res.ok) {
      if (msg) {
        msg.textContent = t('msg.file_salvato_successo_backup_sicurezza', currentSelectedConfigFile);
        msg.style.color = '#9ccc65';
        msg.style.display = 'block';
      }
      if (restoreBtn) restoreBtn.style.display = 'inline-flex';
      if (backupTag) backupTag.style.display = 'inline-block';
      updateConfigFileStats();
      showToast(t('msg.file_salvato_successo', currentSelectedConfigFile), 'success');
    } else {
      const err = await res.json().catch(() => ({}));
      if (msg) {
        msg.textContent = t('msg.errore_salvataggio_2', err.message || err.detail || t('msg.operazione_fallita'));
        msg.style.color = '#e5566f';
        msg.style.display = 'block';
      }
      showToast(t('msg.errore_salvataggio_file', err.message || err.detail || t('msg.operazione_fallita')), 'error');
    }
  } catch (e) {
    if (msg) {
      msg.textContent = t('msg.errore_rete', e);
      msg.style.color = '#e5566f';
      msg.style.display = 'block';
    }
    showToast(t('msg.errore_rete', e), 'error');
  } finally {
    if (btn) btn.disabled = false;
  }
}

// =========================================================
// CHANGELOG MODAL & PLATFORM FILTERING (PC vs TELEFONO)
// =========================================================
function isPhoneDevice() {
  const params = new URLSearchParams(window.location.search);
  if (params.get('device') === 'phone' || params.get('platform') === 'phone') return true;
  if (params.get('device') === 'pc' || params.get('platform') === 'pc') return false;

  const ua = navigator.userAgent || navigator.vendor || window.opera || '';
  if (/android|iphone|ipad|ipod|blackberry|iemobile|opera mini|mobile/i.test(ua)) {
    return true;
  }
  if (window.innerWidth <= 768 && ('ontouchstart' in window || navigator.maxTouchPoints > 0)) {
    return true;
  }
  return false;
}

let currentChangelogFilter = 'pc';
let currentSystemAbout = null;

async function loadSystemAboutInfo() {
  try {
    const res = await fetch('/api/system/about');
    const data = await res.json();
    if (data.status === 'success') {
      currentSystemAbout = data;
      const releaseBadge = document.getElementById('sys-release-badge');
      const headerBadge = document.getElementById('changelog-header-badge');
      const osInfo = document.getElementById('sys-os-info');
      const javaInfo = document.getElementById('sys-java-info');
      const serverInfo = document.getElementById('sys-server-info');
      const hwInfo = document.getElementById('sys-hw-info');

      if (releaseBadge) releaseBadge.textContent = `v${data.version} (Build ${data.build})`;
      if (headerBadge) headerBadge.textContent = t('msg.release_ufficiale_vx_build_attiva', data.version, data.build);
      if (osInfo) osInfo.textContent = `${data.os} • Py ${data.python_version}`;
      if (javaInfo) javaInfo.textContent = data.java_version || t('msg.java_installato');
      if (serverInfo) serverInfo.textContent = t('msg.mod_caricate_porta', data.mods_count, data.server_port || 32769);
      if (hwInfo) hwInfo.textContent = t('msg.gb_ram_cpu_gb_liberi', data.ram_total_gb, data.cpu_count, data.disk_free_gb);
    }
  } catch (err) {
    console.error('Errore caricamento info di sistema:', err);
  }
}

async function copyDiagnosticReport() {
  let reportText = '';
  if (currentSystemAbout) {
    const s = currentSystemAbout;
    reportText = [
      '=== REPORT DIAGNOSTICO VOXARIUM ===',
      `• Versione: v${s.version} (Build ${s.build}) - ${s.release_channel}`,
      `• Data Release: ${s.release_date}`,
      `• Sistema Operativo: ${s.os}`,
      `• Python Runtime: v${s.python_version}`,
      `• Java Runtime: ${s.java_version}`,
      `• Porta Server MC: ${s.server_port} (${s.upnp_active ? t('ui.upnp_attivo') : t('msg.locale')})`,
      `• Cartella Server: ${s.server_dir} (${s.mods_count} Mod rilevate)`,
      `• Cartella Programma: ${s.program_dir}`,
      `• Risorse Host PC: ${s.ram_total_gb} GB RAM Totale, ${s.cpu_count} Core CPU, ${s.disk_free_gb} GB liberi`,
      `• Accesso da internet: ${s.remote_active ? ('Attivo (' + (s.remote_url || 'online') + ')') : t('ui.inattivo')}`,
      `• Uptime Dashboard: ${s.uptime_formatted || (s.uptime_seconds + 's')}`,
      `• Timestamp Generazione: ${new Date().toLocaleString()}`,
      '==================================================='
    ].join('\n');
  } else {
    reportText = `Voxarium\nOS: Windows (Host PC)\nGenerato il: ${new Date().toLocaleString()}`;
  }

  try {
    if (navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(reportText);
    } else {
      const ta = document.createElement('textarea');
      ta.value = reportText;
      document.body.appendChild(ta);
      ta.select();
      document.execCommand('copy');
      document.body.removeChild(ta);
    }
    showToast(t('msg.report_diagnostico_copiato_negli_appunti'), 'success');
  } catch (err) {
    showToast((t('msg.errore_durante_copia_report') + " ") + err.message, 'error');
  }
}

async function openProgramFolderOnPc() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  try {
    const res = await fetch('/api/program/open_folder', { method: 'POST' });
    const data = await res.json();
    if (data.status === 'success') {
      showToast(t('msg.cartella_programma_aperta_esplora_file'), 'success');
    } else {
      showToast((t('msg.errore_apertura_cartella') + " ") + (data.message || t('msg.errore')), 'error');
    }
  } catch (err) {
    showToast((t('msg.errore_2') + " ") + err.message, 'error');
  }
}

function filterChangelogBySearch() {
  const query = document.getElementById('changelog-search-input')?.value.toLowerCase().trim() || '';
  const clearBtn = document.getElementById('btn-clear-changelog-search');
  const footerCount = document.getElementById('changelog-footer-count');

  if (clearBtn) clearBtn.style.display = query ? 'block' : 'none';

  const items = document.querySelectorAll('#modal-changelog li, #modal-changelog .changelog-entry');
  let matchCount = 0;

  items.forEach(el => {
    if (!query) {
      el.style.display = '';
      matchCount++;
    } else {
      const text = el.textContent.toLowerCase();
      if (text.includes(query)) {
        el.style.display = '';
        matchCount++;
      } else {
        el.style.display = 'none';
      }
    }
  });

  if (footerCount) {
    if (query) {
      footerCount.textContent = t('msg.trovati_elementi', matchCount, query);
      footerCount.style.color = matchCount > 0 ? '#8db6e8' : '#f0788c';
    } else {
      footerCount.textContent = t('msg.novita_versioni_archiviate', 14, 'v4.9.5', 7);
      footerCount.style.color = '#9a9ca2';
    }
  }
}

function clearChangelogSearch() {
  const input = document.getElementById('changelog-search-input');
  if (input) input.value = '';
  filterChangelogBySearch();
}

function setChangelogFilter(filter) {
  const modal = document.getElementById('modal-changelog');
  if (!modal) return;

  const btnPc = document.getElementById('btn-filter-changelog-pc');
  const btnPhone = document.getElementById('btn-filter-changelog-phone');
  const btnAll = document.getElementById('btn-filter-changelog-all');
  const badgePlatform = document.getElementById('changelog-active-platform-badge');

  modal.classList.remove('filter-pc', 'filter-phone', 'filter-all');
  if (btnPc) btnPc.classList.remove('active');
  if (btnPhone) btnPhone.classList.remove('active');
  if (btnAll) btnAll.classList.remove('active');

  currentChangelogFilter = filter;

  if (filter === 'pc') {
    modal.classList.add('filter-pc');
    if (btnPc) btnPc.classList.add('active');
    if (badgePlatform) {
      badgePlatform.textContent = t('ui.solo_pc_desktop_server');
      badgePlatform.style.color = '#9ccc65';
      badgePlatform.style.borderColor = 'rgba(74, 222, 128, 0.4)';
    }
  } else if (filter === 'phone') {
    modal.classList.add('filter-phone');
    if (btnPhone) btnPhone.classList.add('active');
    if (badgePlatform) {
      badgePlatform.textContent = t('msg.solo_telefono_app_android_apk');
      badgePlatform.style.color = '#8db6e8';
      badgePlatform.style.borderColor = 'rgba(111, 159, 216, 0.4)';
    }
  } else {
    modal.classList.add('filter-all');
    if (btnAll) btnAll.classList.add('active');
    if (badgePlatform) {
      badgePlatform.textContent = t('msg.tutti_aggiornamenti_pc_telefono');
      badgePlatform.style.color = '#c4c4c8';
      badgePlatform.style.borderColor = 'rgba(154, 156, 162, 0.4)';
    }
  }
}

function openChangelogModal() {
  const isPhone = isPhoneDevice();
  setChangelogFilter(isPhone ? 'phone' : 'pc');
  clearChangelogSearch();
  loadSystemAboutInfo();
  openModal('modal-changelog');
}

function closeChangelogModal() {
  closeModal('modal-changelog');
}

// =========================================================
// =========================================================
// MULTI-TYPE AUTOMATED SERVER CREATOR (FORGE / NEOFORGE / FABRIC / PAPER / VANILLA)
// =========================================================
let forgeInstallPollTimer = null;
let forgeInstallStartTime = null;
let currentServerType = 'forge'; // 'forge', 'neoforge', 'fabric', 'paper', 'vanilla'
let cachedForgeVersions = [];
let cachedNeoForgeVersions = [];
let cachedVanillaVersions = [];
let cachedPaperVersions = [];
let cachedFabricVersions = [];

function handleVersionsUpdated(data) {
  if (!data) return;
  if (data.forge_versions) cachedForgeVersions = data.forge_versions;
  if (data.neoforge_versions) cachedNeoForgeVersions = data.neoforge_versions;
  if (data.fabric_versions) cachedFabricVersions = data.fabric_versions;
  if (data.paper_versions) cachedPaperVersions = data.paper_versions;
  if (data.vanilla_versions) cachedVanillaVersions = data.vanilla_versions;

  const chkNeoForge = document.getElementById('neoforge-inst-include-builds');
  const chkVanilla = document.getElementById('vanilla-inst-include-snapshots');
  const chkPaper = document.getElementById('paper-inst-include-snapshots');
  const chkFabric = document.getElementById('fabric-inst-include-snapshots');

  renderForgeVersions();
  renderNeoForgeVersions(chkNeoForge ? chkNeoForge.checked : false);
  renderVanillaVersions(chkVanilla ? chkVanilla.checked : false);
  renderPaperVersions(chkPaper ? chkPaper.checked : false);
  renderFabricVersions(chkFabric ? chkFabric.checked : false);

  const autoText = document.getElementById('versions-auto-update-text');
  if (autoText) {
    const d = new Date();
    const timeStr = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
    autoText.innerHTML = `${th('msg.sincronizzazione_completata_alle')} <b>${timeStr}</b>: ${cachedForgeVersions.length} Forge, ${cachedNeoForgeVersions.length} NeoForge, ${cachedFabricVersions.length} Fabric, ${cachedPaperVersions.length} Paper, ${cachedVanillaVersions.length} Vanilla.`;
  }

  if (data.has_new_versions && Array.isArray(data.new_versions) && data.new_versions.length > 0) {
    const listNames = data.new_versions.map(v => `${v.loader.toUpperCase()} ${v.version}`).join(', ');
    if (typeof showToast === 'function') {
      showToast(t('msg.nuove_versioni_rilevate_aggiunte_all', listNames), 'success');
    }
  }
}

async function manualRefreshVersions() {
  const btn = document.getElementById('btn-refresh-versions');
  const spinner = document.getElementById('icon-refresh-spinner');
  if (btn) btn.disabled = true;
  if (spinner) {
    spinner.style.display = 'inline-block';
    spinner.style.animation = 'spin 1s linear infinite';
  }

  try {
    const res = await fetch('/api/forge/refresh_versions', { method: 'POST' });
    const data = await res.json();
    handleVersionsUpdated(data);

    if (data.has_new_versions && data.new_versions && data.new_versions.length > 0) {
      if (typeof showToast === 'function') {
        showToast(t('msg.trovate_aggiunte_nuove_versioni_all', data.new_versions.length), 'success');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(t('msg.tutte_versioni_sono_gia_aggiornate'), 'info');
      }
    }
  } catch (err) {
    console.error('Errore refresh versioni:', err);
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_durante_verifica_nuove_versioni'), 'error');
    }
  } finally {
    if (btn) btn.disabled = false;
    if (spinner) {
      spinner.style.animation = 'none';
    }
  }
}

function renderNeoForgeVersions(includeSnapshots) {
  const sel = document.getElementById('neoforge-inst-version');
  const hint = document.getElementById('neoforge-count-hint');
  if (!sel || !cachedNeoForgeVersions || cachedNeoForgeVersions.length === 0) return;

  const prevVal = sel.value;
  sel.innerHTML = '';

  const releases = cachedNeoForgeVersions.filter(v => (v.type === 'release' || !v.type));
  const snapshots = cachedNeoForgeVersions.filter(v => v.type === 'snapshot');

  if (includeSnapshots && snapshots.length > 0) {
    const grpRel = document.createElement('optgroup');
    grpRel.label = t('msg.release_stabili', releases.length);
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Minecraft ${v.mc_version} (NeoForge ${v.neoforge_version || v.version})`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.21.1' || v.mc_version === '1.20.4' || v.mc_version === '1.20.1') && !prevVal) opt.selected = true;
      grpRel.appendChild(opt);
    });
    sel.appendChild(grpRel);

    const grpSnap = document.createElement('optgroup');
    grpSnap.label = `Pre-Release & Dev (${snapshots.length})`;
    snapshots.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Minecraft ${v.mc_version} (NeoForge Dev)`;
      grpSnap.appendChild(opt);
    });
    sel.appendChild(grpSnap);

    if (hint) {
      hint.textContent = t('msg.visualizzate_tutte_versioni_neoforge_stabili', cachedNeoForgeVersions.length, releases.length, snapshots.length);
    }
  } else {
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Minecraft ${v.mc_version} (NeoForge ${v.neoforge_version || v.version})`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.21.1' || v.mc_version === '1.20.4' || v.mc_version === '1.20.1') && !prevVal) opt.selected = true;
      sel.appendChild(opt);
    });

    if (hint) {
      hint.textContent = t('msg.versioni_stabili_neoforge_disponibili_fino', releases.length, '26.3', '1.20.1');
    }
  }

  if (prevVal && Array.from(sel.options).some(o => o.value === prevVal)) {
    sel.value = prevVal;
  }
}

function toggleNeoForgeBuilds() {
  const chk = document.getElementById('neoforge-inst-include-builds');
  renderNeoForgeVersions(chk ? chk.checked : false);
}

function renderForgeVersions() {
  const sel = document.getElementById('forge-inst-version');
  const hint = document.getElementById('forge-count-hint');
  if (!sel || !cachedForgeVersions || cachedForgeVersions.length === 0) return;

  const prevVal = sel.value;
  sel.innerHTML = '';
  cachedForgeVersions.forEach(v => {
    const mc = v.mc_version || v.mc;
    const forge = v.forge_version || v.forge;
    if (!mc) return;
    const opt = document.createElement('option');
    opt.value = mc;
    opt.textContent = v.label || `Minecraft ${mc} (Forge ${forge || 'Latest'})`;
    if (mc === '26.3' || (v.recommended && !prevVal) || mc === '1.20.1') opt.selected = true;
    sel.appendChild(opt);
  });
  if (prevVal && Array.from(sel.options).some(o => o.value === prevVal)) {
    sel.value = prevVal;
  }
  if (hint) {
    hint.textContent = t('msg.versioni_ufficiali_forge_disponibili_fino', cachedForgeVersions.length, '26.3', '1.1');
  }
}

function renderVanillaVersions(includeSnapshots) {
  const sel = document.getElementById('vanilla-inst-version');
  const hint = document.getElementById('vanilla-count-hint');
  if (!sel || !cachedVanillaVersions || cachedVanillaVersions.length === 0) return;

  const prevVal = sel.value;
  sel.innerHTML = '';

  const releases = cachedVanillaVersions.filter(v => (v.type === 'release' || !v.type));
  const snapshots = cachedVanillaVersions.filter(v => v.type === 'snapshot');

  if (includeSnapshots && snapshots.length > 0) {
    const grpRel = document.createElement('optgroup');
    grpRel.label = t('msg.release_ufficiali', releases.length);
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Minecraft ${v.mc_version}`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.21.4' || v.mc_version === '1.20.1') && !prevVal) opt.selected = true;
      grpRel.appendChild(opt);
    });
    sel.appendChild(grpRel);

    const grpSnap = document.createElement('optgroup');
    grpSnap.label = t('msg.snapshot_versioni_sviluppo', snapshots.length);
    snapshots.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Minecraft ${v.mc_version} (Snapshot)`;
      grpSnap.appendChild(opt);
    });
    sel.appendChild(grpSnap);

    if (hint) {
      hint.textContent = t('msg.visualizzate_tutte_versioni_vanilla_release', cachedVanillaVersions.length, releases.length, snapshots.length);
    }
  } else {
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Minecraft ${v.mc_version}`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.21.4' || v.mc_version === '1.20.1') && !prevVal) opt.selected = true;
      sel.appendChild(opt);
    });

    if (hint) {
      hint.textContent = t('msg.release_ufficiali_mojang_disponibili_fino', releases.length, '26.3', '1.0');
    }
  }

  if (prevVal && Array.from(sel.options).some(o => o.value === prevVal)) {
    sel.value = prevVal;
  }
}

function renderPaperVersions(includeSnapshots) {
  const sel = document.getElementById('paper-inst-version');
  const hint = document.getElementById('paper-count-hint');
  if (!sel || !cachedPaperVersions || cachedPaperVersions.length === 0) return;

  const prevVal = sel.value;
  sel.innerHTML = '';

  const releases = cachedPaperVersions.filter(v => (v.type === 'release' || !v.type));
  const snapshots = cachedPaperVersions.filter(v => v.type === 'snapshot');

  if (includeSnapshots && snapshots.length > 0) {
    const grpRel = document.createElement('optgroup');
    grpRel.label = t('msg.release_stabili', releases.length);
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Paper ${v.mc_version}`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.20.4') && !prevVal) opt.selected = true;
      grpRel.appendChild(opt);
    });
    sel.appendChild(grpRel);

    const grpSnap = document.createElement('optgroup');
    grpSnap.label = `Pre-Release & RC (${snapshots.length})`;
    snapshots.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Paper ${v.mc_version} (RC/Pre)`;
      grpSnap.appendChild(opt);
    });
    sel.appendChild(grpSnap);

    if (hint) {
      hint.textContent = t('msg.visualizzate_tutte_versioni_papermc_stabili', cachedPaperVersions.length, releases.length, snapshots.length);
    }
  } else {
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Paper ${v.mc_version}`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.20.4') && !prevVal) opt.selected = true;
      sel.appendChild(opt);
    });

    if (hint) {
      hint.textContent = t('msg.versioni_stabili_papermc_disponibili_fino', releases.length, '26.3', '1.7.10');
    }
  }

  if (prevVal && Array.from(sel.options).some(o => o.value === prevVal)) {
    sel.value = prevVal;
  }
}

function renderFabricVersions(includeSnapshots) {
  const sel = document.getElementById('fabric-inst-version');
  const hint = document.getElementById('fabric-count-hint');
  if (!sel || !cachedFabricVersions || cachedFabricVersions.length === 0) return;

  const prevVal = sel.value;
  sel.innerHTML = '';

  const releases = cachedFabricVersions.filter(v => (v.type === 'release' || v.stable));
  const snapshots = cachedFabricVersions.filter(v => (v.type !== 'release' && !v.stable));

  if (includeSnapshots && snapshots.length > 0) {
    const grpRel = document.createElement('optgroup');
    grpRel.label = t('msg.release_stabili', releases.length);
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Fabric ${v.mc_version}`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.21.1') && !prevVal) opt.selected = true;
      grpRel.appendChild(opt);
    });
    sel.appendChild(grpRel);

    const grpSnap = document.createElement('optgroup');
    grpSnap.label = `Snapshot & Dev (${snapshots.length})`;
    snapshots.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Fabric ${v.mc_version} (Dev)`;
      grpSnap.appendChild(opt);
    });
    sel.appendChild(grpSnap);

    if (hint) {
      hint.textContent = t('msg.visualizzate_tutte_versioni_fabric_stabili', cachedFabricVersions.length, releases.length, snapshots.length);
    }
  } else {
    releases.forEach(v => {
      const opt = document.createElement('option');
      opt.value = v.mc_version || v.mc;
      opt.textContent = v.label || `Fabric ${v.mc_version}`;
      if ((v.mc_version === '26.3' || v.mc_version === '1.21.1') && !prevVal) opt.selected = true;
      sel.appendChild(opt);
    });

    if (hint) {
      hint.textContent = t('msg.versioni_stabili_fabricmc_disponibili_fino', releases.length, '26.3', '1.14');
    }
  }

  if (prevVal && Array.from(sel.options).some(o => o.value === prevVal)) {
    sel.value = prevVal;
  }
}

function toggleVanillaSnapshots() {
  const chk = document.getElementById('vanilla-inst-include-snapshots');
  renderVanillaVersions(chk ? chk.checked : false);
}

function togglePaperSnapshots() {
  const chk = document.getElementById('paper-inst-include-snapshots');
  renderPaperVersions(chk ? chk.checked : false);
}

function toggleFabricSnapshots() {
  const chk = document.getElementById('fabric-inst-include-snapshots');
  renderFabricVersions(chk ? chk.checked : false);
}

let cachedDesktopDir = '';
function getInstallerFolder(folderName) {
  if (cachedDesktopDir) {
    return cachedDesktopDir.replace(/[\\/]+$/, '') + '\\' + folderName;
  }
  return folderName;
}

function selectServerType(type) {
  currentServerType = type;
  
  // Update button active state
  ['forge', 'neoforge', 'fabric', 'paper', 'vanilla'].forEach(t => {
    const btn = document.getElementById(`btn-stype-${t}`);
    if (btn) {
      if (t === type) btn.classList.add('active');
      else btn.classList.remove('active');
    }
  });

  // Switch panes
  const paneForge = document.getElementById('pane-forge-options');
  const paneNeoForge = document.getElementById('pane-neoforge-options');
  const paneFabric = document.getElementById('pane-fabric-options');
  const paneVanilla = document.getElementById('pane-vanilla-options');
  const panePaper = document.getElementById('pane-paper-options');
  if (paneForge) paneForge.style.display = (type === 'forge') ? 'block' : 'none';
  if (paneNeoForge) paneNeoForge.style.display = (type === 'neoforge') ? 'block' : 'none';
  if (paneFabric) paneFabric.style.display = (type === 'fabric') ? 'block' : 'none';
  if (paneVanilla) paneVanilla.style.display = (type === 'vanilla') ? 'block' : 'none';
  if (panePaper) panePaper.style.display = (type === 'paper') ? 'block' : 'none';

  // Update header and footer text
  const modalIcon = document.getElementById('installer-modal-icon');
  const modalTitle = document.getElementById('installer-modal-title');
  const btnStart = document.getElementById('btn-forge-start');
  const dirInput = document.getElementById('forge-inst-dir');

  const calloutTitle = document.getElementById('callout-title');
  const calloutList = document.getElementById('callout-list');

  if (type === 'neoforge') {
    if (modalIcon) modalIcon.textContent = '';
    if (modalTitle) modalTitle.textContent = t('msg.creazione_server_minecraft_neoforge_modloader');
    if (btnStart) btnStart.innerHTML = `<span>${th('msg.avvia_creazione_server', 'NeoForge')}</span>`;
    if (dirInput && (!dirInput.value || dirInput.value.includes('Minecraft_'))) {
      dirInput.value = getInstallerFolder('Minecraft_NeoForge_Server');
    }
    if (calloutTitle) calloutTitle.textContent = t('msg.cosa_fara_creazione_neoforge');
    if (calloutList) {
      calloutList.innerHTML = `
        <li>${th('msg.scarica_l_installer_ufficiale_neoforged')}</li>
        <li>${th('msg.installa_server_modalita_headless_senza')}</li>
        <li>${th('ui.accetta_automaticamente_l')}<code>eula.txt</code> ${th('ui.mojang')}</li>
        <li>${th('ui.imposta')} <code>server.properties</code> ${th('msg.porta_upnp_parametri_prestazionali_ottimali')}</li>
        <li>${th('msg.crea')} <code>user_jvm_args.txt</code> ${th('msg.flag_aikar_g1gc_script_avvio')} <code>run.bat</code>.</li>
        <li>${th('msg.prepara_cartella')} <code>mods/</code> ${th('msg.pronta_caricare_qualsiasi_mod_neoforge')}</li>
        <li style="color: #9ccc65; font-weight: 600;">${th('msg.pieno_supporto_tutte_mod_neoforge')}</li>
      `;
    }
  } else if (type === 'fabric') {
    if (modalIcon) modalIcon.textContent = '';
    if (modalTitle) modalTitle.textContent = t('msg.creazione_server_minecraft_fabric_mod');
    if (btnStart) btnStart.innerHTML = `<span>${th('msg.avvia_creazione_server', 'Fabric')}</span>`;
    if (dirInput && (!dirInput.value || dirInput.value.includes('Minecraft_'))) {
      dirInput.value = getInstallerFolder('Minecraft_Fabric_Server');
    }
    if (calloutTitle) calloutTitle.textContent = t('msg.cosa_fara_creazione_fabric_fabricmc');
    if (calloutList) {
      calloutList.innerHTML = `
        <li>${th('msg.scarica_server_launcher_fabric_base')}</li>
        <li>${th('msg.architettura_ultra_moderna_reattiva_tempi')}</li>
        <li>${th('msg.pieno_supporto_mod_prestazionali_sodium')} <code>mods/</code>.</li>
        <li>${th('ui.accetta_automaticamente_l')}<code>eula.txt</code> ${th('ui.mojang')}</li>
        <li>${th('msg.genera')} <code>server.properties</code> ${th('msg.porta_upnp_ram_configurata')}</li>
        <li>${th('msg.crea_script_avvio_rapido')} <code>run.bat</code> ${th('msg.registra_l_istanza_nel_gestore')}</li>
        <li style="color: #9ccc65; font-weight: 600;">${th('msg.pieno_supporto_mod_fabric_scaricate')}</li>
      `;
    }
  } else if (type === 'paper') {
    if (modalIcon) modalIcon.textContent = '';
    if (modalTitle) modalTitle.textContent = t('msg.creazione_server_minecraft_paper_alte');
    if (btnStart) btnStart.innerHTML = `<span>${th('msg.avvia_creazione_server', 'Paper')}</span>`;
    if (dirInput && (!dirInput.value || dirInput.value.includes('Minecraft_'))) {
      dirInput.value = getInstallerFolder('Minecraft_Paper_Server');
    }
    if (calloutTitle) calloutTitle.textContent = t('msg.cosa_fara_creazione_paper_papermc');
    if (calloutList) {
      calloutList.innerHTML = `
        <li>${th('msg.scarica_l_ultima_build_stabile')}</li>
        <li>${th('msg.prestazioni_elevate_ottimizzazioni_asincrone_tic')}</li>
        <li>${th('msg.pieno_supporto_tutti_plugin_spigot')} <code>plugins/</code>.</li>
        <li>${th('ui.accetta_automaticamente_l')}<code>eula.txt</code> ${th('ui.mojang')}</li>
        <li>${th('msg.genera')} <code>server.properties</code> ${th('msg.porta_upnp_ram_configurata')}</li>
        <li>${th('msg.crea_script_avvio_rapido')} <code>run.bat</code> ${th('msg.registra_l_istanza_nel_gestore')}</li>
      `;
    }
  } else if (type === 'vanilla') {
    if (modalIcon) modalIcon.textContent = '';
    if (modalTitle) modalTitle.textContent = t('msg.creazione_server_minecraft_vanilla_ufficiale');
    if (btnStart) btnStart.innerHTML = `<span>${th('msg.avvia_creazione_server', 'Vanilla')}</span>`;
    if (dirInput && (!dirInput.value || dirInput.value.includes('Minecraft_'))) {
      dirInput.value = getInstallerFolder('Minecraft_Vanilla_Server');
    }
    if (calloutTitle) calloutTitle.textContent = t('msg.cosa_fara_l_installazione_vanilla');
    if (calloutList) {
      calloutList.innerHTML = `
        <li>${th('msg.scarica_file')} <code>server.jar</code> ${th('msg.ufficiale_autentico_direttamente_dai_server')}</li>
        <li>${th('msg.zero_mod_o_complessita_massima')}</li>
        <li>${th('ui.accetta_automaticamente_l')}<code>eula.txt</code> ${th('ui.mojang')}</li>
        <li>${th('msg.genera')} <code>server.properties</code> ${th('msg.porta_upnp_ram_allocata')}</li>
        <li>${th('msg.crea_script_avvio_rapido')} <code>run.bat</code> ottimizzato.</li>
        <li>${th('msg.collega_server_vanilla_direttamente_pannello')}</li>
      `;
    }
  } else {
    // Forge
    if (modalIcon) modalIcon.textContent = '';
    if (modalTitle) modalTitle.textContent = t('msg.creazione_server_minecraft_forge_mod');
    if (btnStart) btnStart.innerHTML = `<span>${th('ui.avvia_creazione_server_forge')}</span>`;
    if (dirInput && (!dirInput.value || dirInput.value.includes('Minecraft_'))) {
      dirInput.value = getInstallerFolder('Minecraft_Forge_Server');
    }
    if (calloutTitle) calloutTitle.textContent = t('msg.cosa_fara_creazione_forge');
    if (calloutList) {
      calloutList.innerHTML = `
        <li>${th('ui.scarica_l_installer_ufficiale_forge')}</li>
        <li>${th('msg.installa_server_modalita_headless_scaricando')}</li>
        <li>${th('ui.accetta_automaticamente_l')}<code>eula.txt</code> ${th('ui.mojang')}</li>
        <li>${th('ui.imposta')} <code>server.properties</code> ${th('ui.porta_parametri_ottimali')}</li>
        <li>${th('msg.crea')} <code>user_jvm_args.txt</code> ${th('msg.flag_aikar_script_avvio')} <code>run.bat</code>.</li>
        <li>${th('msg.prepara_cartella')} <code>mods/</code> ${th('msg.caricare_qualsiasi_mod')}</li>
        <li style="color: #9ccc65; font-weight: 600;">${th('msg.pieno_supporto_tutte_mod_scaricate')}</li>
      `;
    }
  }
}

async function openForgeInstallerModal() {
  if (!isLocalHostClient()) {
    showToast(t('msg.creazione_installazione_nuovo_server_disponibile'), 'warning');
    return;
  }
  openModal('modal-forge-installer');
  
  // Reset state
  const formEl = document.getElementById('forge-inst-form');
  const progEl = document.getElementById('forge-inst-progress-container');
  const succEl = document.getElementById('forge-inst-success-container');
  const footerEl = document.getElementById('forge-inst-footer');
  const terminal = document.getElementById('forge-install-terminal');
  const btnStart = document.getElementById('btn-forge-start');
  const btnCancel = document.getElementById('btn-forge-cancel');
  const javaBadge = document.getElementById('forge-java-badge');
  const javaStatus = document.getElementById('forge-java-status');
  const selectVer = document.getElementById('forge-inst-version');
  const selectFabric = document.getElementById('fabric-inst-version');
  const selectVanilla = document.getElementById('vanilla-inst-version');
  const selectPaper = document.getElementById('paper-inst-version');
  const inputDir = document.getElementById('forge-inst-dir');

  if (terminal) terminal.textContent = '';
  if (succEl) succEl.style.display = 'none';
  if (progEl) progEl.style.display = 'none';
  if (formEl) formEl.style.display = 'block';
  if (footerEl) footerEl.style.display = 'flex';
  if (btnStart) {
    btnStart.disabled = false;
    btnStart.style.opacity = '1';
  }
  if (btnCancel) btnCancel.style.display = 'inline-block';

  selectServerType('forge');

  // Fetch prerequisites & versions from backend
  try {
    const res = await fetch('/api/forge/prerequisites');
    const data = await res.json();
    
    // Update Java Status
    if (javaBadge && javaStatus) {
      if (data.java_available) {
        javaBadge.className = 'badge';
        javaBadge.style.background = 'rgba(124, 179, 66, 0.2)';
        javaBadge.style.color = '#7cb342';
        javaBadge.style.borderColor = 'rgba(124, 179, 66, 0.4)';
        javaBadge.textContent = t('msg.rilevato');
        javaStatus.innerHTML = `${th('msg.ambiente_java')} <b>${escapeHtml(data.java_version || 'Java')}</b>`;
      } else {
        javaBadge.className = 'badge';
        javaBadge.style.background = 'rgba(229, 86, 111, 0.2)';
        javaBadge.style.color = '#e5566f';
        javaBadge.style.borderColor = 'rgba(229, 86, 111, 0.4)';
        javaBadge.textContent = t('msg.trovato');
        javaStatus.innerHTML = `<b>${th('msg.java_rilevato')}</b> ${th('msg.questo_computer_necessario_java_17')}`;
      }
    }

    // Default Directory
    if (inputDir && (!inputDir.value || inputDir.value.trim() === '')) {
      if (data.desktop_dir) cachedDesktopDir = data.desktop_dir;
      inputDir.value = data.default_dir || getInstallerFolder('Minecraft_Server');
    }

    // Cache version lists
    cachedForgeVersions = data.forge_versions || data.versions || [];
    cachedNeoForgeVersions = data.neoforge_versions || [];
    cachedVanillaVersions = data.vanilla_versions || [];
    cachedPaperVersions = data.paper_versions || [];
    cachedFabricVersions = data.fabric_versions || [];

    const chkNeoForge = document.getElementById('neoforge-inst-include-builds');
    const chkVanilla = document.getElementById('vanilla-inst-include-snapshots');
    const chkPaper = document.getElementById('paper-inst-include-snapshots');
    const chkFabric = document.getElementById('fabric-inst-include-snapshots');

    renderForgeVersions();
    renderNeoForgeVersions(chkNeoForge ? chkNeoForge.checked : false);
    renderVanillaVersions(chkVanilla ? chkVanilla.checked : false);
    renderPaperVersions(chkPaper ? chkPaper.checked : false);
    renderFabricVersions(chkFabric ? chkFabric.checked : false);

    // Check if an install is already running
    checkExistingForgeInstall();
  } catch (err) {
    console.error('Errore caricamento prerequisiti server installer:', err);
    if (javaStatus) javaStatus.textContent = t('msg.errore_connessione_server_backend');
  }
}

async function checkExistingForgeInstall() {
  try {
    const res = await fetch('/api/forge/status');
    const status = await res.json();
    if (status.in_progress) {
      showForgeProgressMode();
      startForgeStatusPolling();
    }
  } catch (e) {
    console.error(e);
  }
}

function closeForgeInstallerModal() {
  if (forgeInstallPollTimer) {
    clearInterval(forgeInstallPollTimer);
    forgeInstallPollTimer = null;
  }
  closeModal('modal-forge-installer');
}

function showForgeProgressMode() {
  const formEl = document.getElementById('forge-inst-form');
  const progEl = document.getElementById('forge-inst-progress-container');
  const btnStart = document.getElementById('btn-forge-start');
  const btnCancel = document.getElementById('btn-forge-cancel');

  if (formEl) formEl.style.display = 'none';
  if (progEl) progEl.style.display = 'block';
  if (btnStart) {
    btnStart.disabled = true;
    btnStart.style.opacity = '0.5';
    btnStart.innerHTML = `<span>${th('msg.creazione_server_corso')}</span>`;
  }
  if (btnCancel) btnCancel.style.display = 'none';
}

async function startForgeInstallation() {
  if (!isLocalHostClient()) {
    showToast(t('msg.creazione_installazione_nuovo_server_disponibile'), 'warning');
    return;
  }
  if (currentServerState === 'ONLINE') {
    if (typeof showToast === 'function') {
      showToast(t('msg.server_minecraft_attualmente_esecuzione_spegni'), 'warning');
    } else {
      alert(t('msg.server_minecraft_attualmente_esecuzione_spegni'));
    }
    return;
  }

  const selectRam = document.getElementById('forge-inst-ram');
  const inputDir = document.getElementById('forge-inst-dir');
  const inputPort = document.getElementById('forge-inst-port');

  const ramGb = selectRam ? parseInt(selectRam.value, 10) : 8;
  const targetDir = inputDir ? inputDir.value.trim() : '';
  const port = inputPort ? parseInt(inputPort.value, 10) : 32769;

  let mcVersion = '1.20.1';

  if (currentServerType === 'forge') {
    const sel = document.getElementById('forge-inst-version');
    mcVersion = sel ? sel.value : '1.20.1';
  } else if (currentServerType === 'neoforge') {
    const sel = document.getElementById('neoforge-inst-version');
    mcVersion = sel ? sel.value : '1.21.1';
  } else if (currentServerType === 'fabric') {
    const sel = document.getElementById('fabric-inst-version');
    mcVersion = sel ? sel.value : '1.21.1';
  } else if (currentServerType === 'paper') {
    const sel = document.getElementById('paper-inst-version');
    mcVersion = sel ? sel.value : '1.20.4';
  } else if (currentServerType === 'vanilla') {
    const sel = document.getElementById('vanilla-inst-version');
    mcVersion = sel ? sel.value : '1.21.1';
  }

  if (!mcVersion) {
    if (typeof showToast === 'function') showToast(t('msg.seleziona_versione_valida_minecraft'), 'warning');
    else alert(t('msg.seleziona_versione_valida_minecraft'));
    return;
  }
  if (!targetDir) {
    if (typeof showToast === 'function') showToast(t('msg.specifica_cartella_valida_l_installazione'), 'warning');
    else alert(t('msg.specifica_cartella_valida_l_installazione'));
    return;
  }

  showForgeProgressMode();
  forgeInstallStartTime = Date.now();

  try {
    const res = await fetch('/api/forge/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        server_type: currentServerType,
        mc_version: mcVersion,
        ram_gb: ramGb,
        target_dir: targetDir,
        port: port
      })
    });
    const data = await res.json();
    if (!res.ok || (!data.success && data.status !== 'started')) {
      const errMsg = data.detail || data.message || t('actions.errore_sconosciuto');
      if (typeof showToast === 'function') {
        showToast(t('msg.errore_avvio_creazione_server', errMsg), 'error');
      } else {
        alert((t('msg.errore_avvio_creazione_server_2') + " ") + errMsg);
      }
      // Restore form
      const formEl = document.getElementById('forge-inst-form');
      const progEl = document.getElementById('forge-inst-progress-container');
      const btnStart = document.getElementById('btn-forge-start');
      const btnCancel = document.getElementById('btn-forge-cancel');
      if (formEl) formEl.style.display = 'block';
      if (progEl) progEl.style.display = 'none';
      if (btnStart) {
        btnStart.disabled = false;
        btnStart.style.opacity = '1';
        selectServerType(currentServerType);
      }
      if (btnCancel) btnCancel.style.display = 'inline-block';
      return;
    }

    startForgeStatusPolling();
  } catch (err) {
    console.error('Errore chiamata installazione:', err);
    if (typeof showToast === 'function') {
      showToast(t('msg.impossibile_comunicare_server_avviare_l'), 'error');
    } else {
      alert(t('msg.impossibile_comunicare_server_avviare_l'));
    }
  }
}

function copyForgeLogs() {
  const terminal = document.getElementById('forge-install-terminal');
  if (!terminal || !terminal.textContent) {
    if (typeof showToast === 'function') showToast(t('msg.nessun_log_copiare'), 'warning');
    return;
  }
  navigator.clipboard.writeText(terminal.textContent).then(() => {
    if (typeof showToast === 'function') showToast(t('msg.log_dell_installatore_copiati_negli'), 'success');
  }).catch(() => {
    if (typeof showToast === 'function') showToast(t('msg.log_copiati'), 'success');
  });
}

async function openInstalledForgeFolder() {
  if (!isLocalHostClient()) {
    showToast(t('actions.apertura_cartella_disponibile_esclusivamente_sul'), 'warning');
    return;
  }
  try {
    const res = await fetch('/api/forge/open_dir', { method: 'POST' });
    const data = await res.json();
    if (res.ok && data.status === 'success') {
      if (typeof showToast === 'function') {
        showToast(t('msg.cartella_nuovo_server_aperta_esplora'), 'success');
      }
    } else {
      if (typeof showToast === 'function') {
        showToast(`${data.detail || t('msg.impossibile_aprire_cartella')}`, 'error');
      }
    }
  } catch (e) {
    if (typeof showToast === 'function') {
      showToast(t('msg.errore_rete', e.message), 'error');
    }
  }
}

function startForgeStatusPolling() {
  if (forgeInstallPollTimer) clearInterval(forgeInstallPollTimer);
  updateForgeStatusOnce();
  forgeInstallPollTimer = setInterval(updateForgeStatusOnce, 1000);
}

async function updateForgeStatusOnce() {
  try {
    const res = await fetch('/api/forge/status');
    const st = await res.json();

    const pBar = document.getElementById('forge-progress-bar');
    const pPct = document.getElementById('forge-progress-pct');
    const stepText = document.getElementById('forge-step-text');
    const terminal = document.getElementById('forge-install-terminal');
    const timerText = document.getElementById('forge-timer-text');
    const succEl = document.getElementById('forge-inst-success-container');
    const footerEl = document.getElementById('forge-inst-footer');
    const succTitle = document.getElementById('installer-success-title');
    const succDesc = document.getElementById('installer-success-desc');

    if (pBar) pBar.style.width = st.progress + '%';
    if (pPct) pPct.textContent = st.progress + '%';
    if (stepText) stepText.textContent = st.step || t('msg.elaborazione_corso');

    if (timerText && forgeInstallStartTime) {
      const elapsedSec = Math.floor((Date.now() - forgeInstallStartTime) / 1000);
      const mins = Math.floor(elapsedSec / 60);
      const secs = elapsedSec % 60;
      timerText.textContent = t('msg.tempo_trascorso_xm_xs', mins, (secs < 10 ? '0' : '') + secs);
    }

    if (terminal && st.logs && st.logs.length > 0) {
      terminal.textContent = st.logs.join('\n');
      terminal.scrollTop = terminal.scrollHeight;
    }

    if (st.completed) {
      clearInterval(forgeInstallPollTimer);
      forgeInstallPollTimer = null;
      if (st.success) {
        if (pBar) {
          pBar.style.width = '100%';
          pBar.style.background = '#7cb342';
        }
        if (pPct) pPct.textContent = '100%';
        if (stepText) {
          stepText.textContent = t('msg.creazione_server_completata_successo');
          stepText.style.color = '#7cb342';
        }
        if (succTitle && succDesc) {
          if (st.server_type === 'neoforge') {
            succTitle.textContent = t('msg.server_neoforge_installato_successo', st.mc_version || '');
            succDesc.textContent = t('msg.server_neoforge_moderno_stato_installato');
          } else if (st.server_type === 'fabric') {
            succTitle.textContent = t('msg.server_fabric_installato_successo', st.mc_version || '');
            succDesc.textContent = t('msg.server_fabricmc_leggero_moderno_stato');
          } else if (st.server_type === 'paper') {
            succTitle.textContent = t('msg.server_paper_installato_successo', st.mc_version || '');
            succDesc.textContent = t('msg.server_papermc_ad_alte_prestazioni');
          } else if (st.server_type === 'vanilla') {
            succTitle.textContent = t('msg.server_vanilla_installato_successo', st.mc_version || '');
            succDesc.textContent = t('msg.server_ufficiale_mojang_stato_scaricato');
          } else {
            succTitle.textContent = t('msg.server_forge_installato_successo', st.mc_version || '');
            succDesc.textContent = t('msg.server_forge_stato_configurato_librerie');
          }
        }
        if (succEl) succEl.style.display = 'block';
        if (footerEl) footerEl.style.display = 'none';

        // Hide server-dir-warning banner if visible
        const banner = document.getElementById('banner-server-dir-warning');
        if (banner) banner.style.display = 'none';

        // Il pannello mostra subito il nuovo server (prima richiamava fetchConfig/checkStatus, che non esistono)
        fetchStatusHttp();
        fetchInitialNetworkInfo();
        currentFileManagerPath = '';
        loadInstancesList(true);
      } else {
        if (stepText) {
          stepText.textContent = (t('msg.installazione_fallita') + " ") + (st.error || t('msg.errore'));
          stepText.style.color = '#e5566f';
        }
        const btnStart = document.getElementById('btn-forge-start');
        const btnCancel = document.getElementById('btn-forge-cancel');
        if (btnStart) {
          btnStart.disabled = false;
          btnStart.style.opacity = '1';
          btnStart.innerHTML = `<span>${th('msg.riprova_creazione_server')}</span>`;
        }
        if (btnCancel) btnCancel.style.display = 'inline-block';
      }
    }
  } catch (err) {
    console.error('Errore polling stato installazione Forge:', err);
  }
}

function finishForgeInstallation() {
  closeForgeInstallerModal();
  fetchStatusHttp();
  loadInstancesList(true);
  showToast(t('msg.server_configurato_ed_associato_successo'), 'success');
}

// =========================================================
// NATIVE WINDOWS FILE & FOLDER BROWSER BRIDGE (IN PRIMO PIANO)
// =========================================================
let nativeBrowseResolver = null;

if (window.chrome && window.chrome.webview) {
  try {
    window.chrome.webview.addEventListener('message', (event) => {
      const data = event.data;
      if (data && data.action === 'native_browse_result') {
        if (typeof nativeBrowseResolver === 'function') {
          nativeBrowseResolver(data);
          nativeBrowseResolver = null;
        }
      }
    });
  } catch (e) {
    console.warn('Errore registrazione listener WebView2 native_browse:', e);
  }
}

async function requestNativeBrowse(targetField = 'server_dir', initialPath = '') {
  // If running inside Controllo Server Minecraft (WebView2 desktop app),
  // communicate directly with C# Launcher for INSTANT (0ms) TopMost native dialog in foreground!
  if (window.chrome && window.chrome.webview) {
    return new Promise((resolve) => {
      nativeBrowseResolver = resolve;
      try {
        window.chrome.webview.postMessage(JSON.stringify({
          action: 'native_browse',
          target: targetField,
          initialPath: initialPath || ''
        }));
      } catch (err) {
        console.error('Errore postMessage a WebView2:', err);
        nativeBrowseResolver = null;
        fetchNativeBrowseApi(targetField, initialPath).then(resolve);
      }
    });
  }

  // Fallback to backend REST endpoint for external browsers / remote
  return await fetchNativeBrowseApi(targetField, initialPath);
}

async function fetchNativeBrowseApi(targetField, initialPath) {
  try {
    const res = await fetch('/api/filesystem/native_browse', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ target: targetField, initial_path: initialPath || '' })
    });
    return await res.json();
  } catch (err) {
    console.error('Errore chiamata /api/filesystem/native_browse:', err);
    return { success: false, error: String(err) };
  }
}

async function openNativeBrowse(targetField = 'server_dir') {
  if (!isLocalHostClient()) {
    showToast(t('msg.selezione_file_nativa_explorer_disponibile'), 'warning');
    return;
  }
  let initPath = '';
  if (targetField === 'server_dir') {
    const input = document.getElementById('cfg-server-dir');
    if (input && input.value) initPath = input.value.trim();
  } else if (targetField === 'run_bat') {
    const input = document.getElementById('cfg-server-run-bat');
    if (input && input.value) initPath = input.value.trim();
  } else if (targetField === 'forge_inst_dir') {
    const input = document.getElementById('forge-inst-dir');
    if (input && input.value) initPath = input.value.trim();
  } else if (targetField === 'instance_dir') {
    const input = document.getElementById('inst-edit-dir');
    if (input && input.value) initPath = input.value.trim();
  }

  try {
    const data = await requestNativeBrowse(targetField, initPath);
    if (data && data.success && data.selected_path) {
      applyBrowseResult(targetField, data);
    } else if (data && data.unavailable) {
      showToast(t('msg.selezione_file_non_disponibile'), 'warning');
    } else if (data && data.error) {
      showToast((t('msg.errore_selezione_windows') + " ") + data.error, 'error');
    }
  } catch (err) {
    console.error('Errore durante la selezione nativa:', err);
  }
}

function applyBrowseResult(targetField, data) {
  const chosenPath = data.selected_path;
  const folderPath = data.folder_path || chosenPath;
  const isFile = data.is_file;

  if (targetField === 'server_dir') {
    const inputDir = document.getElementById('cfg-server-dir');
    const inputBat = document.getElementById('cfg-server-run-bat');
    if (inputDir) {
      inputDir.value = folderPath;
      inputDir.dispatchEvent(new Event('input', { bubbles: true }));
      inputDir.dispatchEvent(new Event('change', { bubbles: true }));
    }
    // If user clicked directly on run.bat (or another .bat file), auto-fill the run_bat field too!
    if (isFile && chosenPath.toLowerCase().endsWith('.bat')) {
      if (inputBat) {
        inputBat.value = chosenPath;
        inputBat.dispatchEvent(new Event('input', { bubbles: true }));
        inputBat.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }
    showToast(t('msg.cartella_server_impostata', folderPath), 'success');
  } else if (targetField === 'run_bat') {
    const inputBat = document.getElementById('cfg-server-run-bat');
    if (inputBat) {
      inputBat.value = chosenPath;
      inputBat.dispatchEvent(new Event('input', { bubbles: true }));
      inputBat.dispatchEvent(new Event('change', { bubbles: true }));
    }
    showToast(t('msg.file_avvio_impostato', chosenPath), 'success');
  } else if (targetField === 'forge_inst_dir') {
    const inputInst = document.getElementById('forge-inst-dir');
    if (inputInst) {
      inputInst.value = folderPath;
      inputInst.dispatchEvent(new Event('input', { bubbles: true }));
      inputInst.dispatchEvent(new Event('change', { bubbles: true }));
    }
    showToast(t('msg.cartella_installazione', folderPath), 'success');
  } else if (targetField === 'instance_dir') {
    const inputDir = document.getElementById('inst-edit-dir');
    if (inputDir) {
      inputDir.value = folderPath;
      inputDir.dispatchEvent(new Event('input', { bubbles: true }));
      inputDir.dispatchEvent(new Event('change', { bubbles: true }));
    }
    triggerInstanceFolderDetection(folderPath);
  }

}

// =========================================================
// COMMERCIAL UPGRADE: MULTI-SERVER INSTANCE MANAGEMENT
// =========================================================
let currentInstancesData = { active_instance_id: 'default', instances: [] };

async function loadInstancesList(renderGrid = false) {
  try {
    const res = await fetch('/api/instances');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentInstancesData = data;
    // Il backend restituisce l'istanza attiva come oggetto (active_instance): ricava l'id usato dal pannello
    if (!currentInstancesData.active_instance_id && data.active_instance && data.active_instance.id) {
      currentInstancesData.active_instance_id = data.active_instance.id;
    }

    renderSubbarInstancePill();
    renderSubbarDropdown();
    // La porta in alto dipende dall'elenco dei server: se la rete era arrivata prima, restava "--"
    if (networkInfo) updateNetworkInfo(networkInfo);

    const badge = document.getElementById('snav-instances-count');
    if (badge) {
      badge.textContent = (data.instances || []).length;
    }

    if (renderGrid || document.getElementById('tab-instances')?.classList.contains('active')) {
      renderInstancesGrid();
    }
  } catch (err) {
    console.error('Errore caricamento istanze:', err);
  }
}

function toggleInstanceDropdown(event) {
  if (event) {
    event.stopPropagation();
    event.preventDefault();
  }
  const menu = document.getElementById('instance-dropdown-menu');
  if (!menu) return;
  const isShown = menu.style.display === 'flex' || menu.style.display === 'block';
  closeAllDropdowns();
  if (!isShown) {
    menu.style.display = 'flex';
  }
}

function renderSubbarInstancePill() {
  const activeId = currentInstancesData.active_instance_id;
  const active = (currentInstancesData.instances || []).find(i => i.id === activeId);

  const nameEl = document.getElementById('subbar-instance-name');
  const titleEl = document.getElementById('subbar-meta-title');
  if (!active) {
    // Nessun server configurato (es. prima installazione): niente server "inventato"
    if (nameEl) nameEl.textContent = t('serverbar.nessun_server');
    if (titleEl) titleEl.textContent = t('serverbar.nessun_server_configurato');
    applyActiveServerToUi(null);
    return;
  }

  if (nameEl) nameEl.textContent = active.name || active.id;

  if (titleEl) {
    // Tipo e versione solo se riconosciuti nel server: altrimenti "Server Minecraft", mai valori di un altro server
    const typeStr = (active.server_type || active.type || '').toUpperCase();
    const verStr = active.mc_version || active.version || '';
    const label = [typeStr, verStr].filter(Boolean).join(' ') || t('serverbar.server_minecraft');
    titleEl.innerHTML = th('tpl.server_gestito', escapeHtml(label));
  }
  applyActiveServerToUi(active);
}

// Adatta al server attivo le parti del pannello che dipendono da tipo e versione (filtri dello Store, pulsante TPS)
function applyActiveServerToUi(active) {
  const type = ((active && (active.server_type || active.type)) || '').toLowerCase();
  const version = (active && (active.mc_version || active.version)) || '';

  const loaderSel = document.getElementById('store-filter-loader');
  if (loaderSel && !loaderSel.dataset.userChanged) {
    const loaderVal = { purpur: 'paper', spigot: 'paper', bukkit: 'paper' }[type] || type;
    loaderSel.value = [...loaderSel.options].some(o => o.value === loaderVal) ? loaderVal : 'all';
  }
  const versionSel = document.getElementById('store-filter-version');
  if (versionSel && !versionSel.dataset.userChanged) {
    if (version && ![...versionSel.options].some(o => o.value === version)) {
      const opt = document.createElement('option');
      opt.value = version;
      opt.textContent = `MC ${version}`;
      versionSel.insertBefore(opt, versionSel.firstChild);
    }
    versionSel.value = version || 'all';
  }

  // "forge tps" esiste solo su Forge/NeoForge, "tps" su Paper/Spigot/Purpur; sugli altri server il pulsante non c'è
  const tpsChip = document.getElementById('chip-cmd-tps');
  if (tpsChip) {
    const cmd = (type === 'forge' || type === 'neoforge') ? 'forge tps'
      : (type === 'paper' || type === 'purpur' || type === 'spigot') ? 'tps' : '';
    tpsChip.dataset.cmd = cmd;
    tpsChip.textContent = cmd;
    tpsChip.style.display = cmd ? '' : 'none';
  }
}

function renderSubbarDropdown() {
  const listEl = document.getElementById('instance-dropdown-list');
  if (!listEl) return;
  const instances = currentInstancesData.instances || [];
  const activeId = currentInstancesData.active_instance_id;

  if (instances.length === 0) {
    listEl.innerHTML = `<div style="padding: 12px; color: #9a9ca2; font-size: 0.85rem; text-align: center;">${th('msg.nessuna_istanza_configurata')}</div>`;
    return;
  }

  listEl.innerHTML = instances.map(inst => {
    const isActive = inst.id === activeId;
    const itype = (inst.server_type || inst.type || 'minecraft').toLowerCase();
    const iver = inst.mc_version || inst.version || '';
    const iport = inst.server_port || inst.port || 25565;
    const icon = itype === 'forge' ? '' : itype === 'fabric' ? '' : (itype === 'paper' || itype === 'purpur' || itype === 'spigot') ? '' : itype === 'neoforge' ? '' : '';
    return `
      <div class="instance-dropdown-item ${isActive ? 'active' : ''}" onclick="switchInstance('${inst.id}')">
        <div class="instance-item-icon">${icon}</div>
        <div class="instance-item-details">
          <div class="instance-item-name">${escapeHtml(inst.name || inst.id)}</div>
          <div class="instance-item-meta">${escapeHtml(inst.server_type || inst.type || 'Minecraft')} ${escapeHtml(iver)} &bull; Porta ${iport}</div>
        </div>
        ${isActive ? `<span class="instance-item-badge">${th('serverbar.attivo')}</span>` : `<span style="font-size: 0.75rem; color: #8a8c92;">${th('msg.seleziona')}</span>`}
      </div>
    `;
  }).join('');
}

function renderInstancesGrid() {
  const grid = document.getElementById('instances-cards-grid');
  const activeId = currentInstancesData.active_instance_id;
  const instances = currentInstancesData.instances || [];

  const active = instances.find(i => i.id === activeId);
  const heroBanner = document.getElementById('instance-active-banner');
  if (heroBanner) heroBanner.style.display = active ? '' : 'none';
  if (active) {
    const heroName = document.getElementById('hero-instance-name');
    const heroType = document.getElementById('hero-instance-type');
    const heroPort = document.getElementById('hero-instance-port');
    const heroPath = document.getElementById('hero-instance-path');
    if (heroName) heroName.textContent = active.name || active.id;
    if (heroType) heroType.textContent = `${active.server_type || active.type || 'Minecraft'} ${active.mc_version || active.version || ''}`;
    if (heroPort) heroPort.textContent = t('msg.porta_2', active.server_port || active.port || 25565);
    if (heroPath) {
      heroPath.textContent = (active.server_dir || t('msg.percorso_specificato')) +
        (active.dir_exists === false ? ("  " + t('msg.cartella_trovata_questo_pc')) : '');
    }
  }

  if (!grid) return;
  if (instances.length === 0) {
    grid.innerHTML = `<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #9a9ca2;">${th('msg.nessun_server_configurato_questo_pc')}</div>`;
    return;
  }

  grid.innerHTML = instances.map(inst => {
    const isActive = inst.id === activeId;
    const itype = (inst.server_type || inst.type || 'minecraft').toLowerCase();
    const iver = inst.mc_version || inst.version || '';
    const iport = inst.server_port || inst.port || 25565;
    const ibat = inst.server_run_bat || inst.run_bat || 'run.bat';
    const icon = itype === 'forge' ? '' : itype === 'fabric' ? '' : (itype === 'paper' || itype === 'purpur' || itype === 'spigot') ? '' : itype === 'neoforge' ? '' : '';
    return `
      <div class="instance-card ${isActive ? 'active-card' : ''}">
        <div class="instance-card-header">
          <div class="instance-card-title-box">
            <span class="instance-card-icon">${icon}</span>
            <div>
              <h4 class="instance-card-title">${escapeHtml(inst.name || inst.id)}</h4>
              <span class="instance-card-badge">${escapeHtml(inst.server_type || inst.type || 'mc')} ${escapeHtml(iver)}</span>
            </div>
          </div>
          ${isActive ? (currentServerState === 'ONLINE' ? `<span class="badge badge-emerald">${th('msg.esecuzione')}</span>` : `<span class="badge badge-secondary">${th('msg.attiva_2')}</span>`) : ''}
        </div>
        <div class="instance-card-body">
          <div class="instance-info-row">
            <span class="instance-info-label">${th('serverbar.porta')}</span>
            <span class="instance-info-value font-mono">${iport}</span>
          </div>
          <div class="instance-info-row">
            <span class="instance-info-label">${th('msg.cartella')}</span>
            <span class="instance-info-value font-mono" title="${escapeHtml(inst.server_dir || '')}">${escapeHtml(inst.server_dir ? (inst.server_dir.length > 28 ? '...' + inst.server_dir.slice(-25) : inst.server_dir) : '--')}</span>
          </div>
          ${inst.dir_exists === false ? `<div class="instance-info-row" style="color: #f0788c; font-weight: 600;">${th('msg.cartella_trovata_questo_pc_2')}</div>` : ''}
          <div class="instance-info-row">
            <span class="instance-info-label">${th('msg.script_avvio')}</span>
            <span class="instance-info-value font-mono">${escapeHtml(ibat)}</span>
          </div>
        </div>
        <div class="instance-card-actions">
          ${isActive ? `
            <button class="btn btn-sm btn-secondary" disabled style="opacity: 0.7; cursor: default;">${th('msg.istanza_attiva')}</button>
          ` : `
            <button class="btn btn-sm btn-emerald" onclick="switchInstance('${inst.id}')">Passa a questa</button>
          `}
          <button class="btn btn-sm btn-secondary" onclick="openEditInstanceModal('${inst.id}')" title="${th('ui.modifica_parametri_istanza')}">${th('files.modifica')}</button>
          ${!isActive && inst.id !== 'default' ? `
            <button class="btn btn-sm btn-danger-subtle" onclick="deleteInstanceProfile('${inst.id}', '${escapeJsString(inst.name || inst.id)}')" title="${th('msg.rimuovi_istanza')}">${th('msg.rimuovi')}</button>
          ` : ''}
        </div>
      </div>
    `;
  }).join('');
}

async function switchInstance(instanceId) {
  if (instanceId === currentInstancesData.active_instance_id) {
    showToast(t('msg.questa_istanza_gia_attiva'), 'info');
    closeAllDropdowns();
    return;
  }
  closeAllDropdowns();
  try {
    const res = await fetch('/api/instances/switch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instance_id: instanceId })
    });
    const result = await res.json();
    if (!res.ok) {
      showToast(result.detail || t('msg.impossibile_cambiare_istanza_assicurati_server'), 'error');
      return;
    }
    showToast(result.message || t('msg.istanza_attivata_successo'), 'success');
    // Il file manager riparte dalla cartella del nuovo server (prima restava nella sottocartella del server precedente)
    currentFileManagerPath = '';
    const filesTab = document.getElementById('tab-files');
    if (filesTab && filesTab.classList.contains('active')) loadDirectoryFiles('');
    await loadInstancesList(true);
    if (typeof fetchStatusHttp === 'function') fetchStatusHttp();
  } catch (err) {
    showToast((t('msg.errore_durante_cambio_istanza') + " ") + err.message, 'error');
  }
}

let instanceDetectTimer = null;

function debounceInstanceFolderDetection() {
  clearTimeout(instanceDetectTimer);
  instanceDetectTimer = setTimeout(() => {
    const input = document.getElementById('inst-edit-dir');
    if (input && input.value.trim().length > 3) {
      triggerInstanceFolderDetection(input.value.trim());
    }
  }, 600);
}

function toggleInstanceAdvancedOptions() {
  const container = document.getElementById('inst-advanced-container');
  const arrow = document.getElementById('inst-adv-arrow');
  if (!container) return;
  const isHidden = container.style.display === 'none' || !container.style.display;
  container.style.display = isHidden ? 'block' : 'none';
  if (arrow) arrow.textContent = isHidden ? '▾' : '▸';
}

async function triggerInstanceFolderDetection(folderPath) {
  const normPath = (folderPath || '').trim();
  const card = document.getElementById('instance-detection-card');
  const loading = document.getElementById('instance-detection-loading');
  const errorBox = document.getElementById('instance-detection-error');
  const resultBox = document.getElementById('instance-detection-result');

  if (!normPath) {
    if (card) card.style.display = 'none';
    return;
  }

  if (card) card.style.display = 'block';
  if (loading) loading.style.display = 'flex';
  if (errorBox) { errorBox.style.display = 'none'; errorBox.textContent = ''; }
  if (resultBox) { resultBox.style.display = 'none'; resultBox.innerHTML = ''; }

  try {
    const res = await fetch('/api/instances/detect', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ folder_path: normPath })
    });
    const data = await res.json();

    if (loading) loading.style.display = 'none';

    if (!res.ok || !data.success) {
      if (errorBox) {
        errorBox.textContent = '' + (data.detail || data.error || t('msg.cartella_server_riconosciuta'));
        errorBox.style.display = 'block';
      }
      return;
    }

    // Auto-populate inputs
    const nameInput = document.getElementById('inst-edit-name');
    if (nameInput && (!nameInput.value || nameInput.value.trim() === '')) {
      nameInput.value = data.suggested_name || '';
    }

    const typeInput = document.getElementById('inst-edit-type');
    if (typeInput) {
      typeInput.value = [...typeInput.options].some(o => o.value === data.server_type) ? data.server_type : '';
    }

    const verInput = document.getElementById('inst-edit-version');
    if (verInput) {
      verInput.value = data.mc_version || '';
    }

    const portInput = document.getElementById('inst-edit-port');
    if (portInput && data.server_port) {
      portInput.value = data.server_port;
    }

    const batInput = document.getElementById('inst-edit-bat');
    if (batInput && data.server_run_bat) {
      batInput.value = data.server_run_bat;
    }

    const cmdInput = document.getElementById('inst-edit-cmd');
    if (cmdInput && data.server_command) {
      cmdInput.value = data.server_command;
    }

    // Render smart detection UI
    const badgesHtml = `
      <div style="display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px;">
        <span class="badge badge-emerald" style="font-weight: 700;">${escapeHtml(data.server_type_display || (data.server_type || '').toUpperCase() || t('msg.tipo_rilevato'))}</span>
        <span class="badge badge-sapphire" style="font-weight: 700;">${data.mc_version ? 'MC ' + escapeHtml(data.mc_version) : t('msg.versione_rilevata')}</span>
        <span class="badge badge-amber" style="font-weight: 700;">${th('msg.porta_2', data.server_port)}</span>
        <span class="badge badge-secondary" style="font-size: 0.72rem;">${data.confidence === 'high' ? t('msg.rilevamento_certo') : t('msg.rilevamento_medio')}</span>
      </div>
    `;

    let findingsHtml = '';
    if (data.key_findings && data.key_findings.length > 0) {
      findingsHtml = `
        <div style="margin-top: 8px; font-size: 0.78rem; color: #9a9ca2; display: flex; flex-wrap: wrap; gap: 6px;">
          ${data.key_findings.map(f => `<span style="background: rgba(255,255,255,0.06); padding: 2px 8px; border-radius: 4px; border: 1px solid rgba(255,255,255,0.08);">${escapeHtml(f)}</span>`).join('')}
        </div>
      `;
    }

    if (resultBox) {
      resultBox.innerHTML = `
        <div class="instance-detect-card-header">
          <div class="instance-detect-icon">${icon}</div>
          <div style="flex: 1; min-width: 0;">
            <div style="display: flex; align-items: center; justify-content: space-between; gap: 6px;">
              <span style="font-weight: 700; color: #8db6e8; font-size: 0.95rem;">${escapeHtml(data.suggested_name)}</span>
              <span style="font-size: 0.75rem; color: #7cb342; font-weight: 600;">${th('msg.rilevato')}</span>
            </div>
            ${badgesHtml}
          </div>
        </div>
        ${findingsHtml}
        ${data.motd ? `<div style="margin-top: 6px; font-size: 0.78rem; color: #c4c4c8; font-style: italic;">MOTD: "${escapeHtml(data.motd)}"</div>` : ''}
      `;
      resultBox.style.display = 'block';
    }

    showToast(t('msg.server_rilevato', data.suggested_name), 'success');
  } catch (err) {
    console.error('Errore rilevamento automatico cartella:', err);
    if (loading) loading.style.display = 'none';
    if (errorBox) {
      errorBox.textContent = t('msg.errore_connessione_durante_l_analisi');
      errorBox.style.display = 'block';
    }
  }
}

function openAddInstanceModal() {
  const modal = document.getElementById('modal-instance-editor');
  if (!modal) return;
  document.getElementById('instance-modal-title').textContent = t('ui.aggiungi_server_esistente');
  document.getElementById('inst-edit-id').value = '';
  document.getElementById('inst-edit-name').value = '';
  document.getElementById('inst-edit-dir').value = '';
  document.getElementById('inst-edit-bat').value = 'run.bat';
  document.getElementById('inst-edit-cmd').value = '';
  document.getElementById('inst-edit-port').value = '25565';
  document.getElementById('inst-edit-type').value = '';
  document.getElementById('inst-edit-version').value = '';

  // Reset smart detection card & advanced container
  const card = document.getElementById('instance-detection-card');
  if (card) card.style.display = 'none';
  const loading = document.getElementById('instance-detection-loading');
  if (loading) loading.style.display = 'none';
  const errBox = document.getElementById('instance-detection-error');
  if (errBox) errBox.style.display = 'none';
  const resBox = document.getElementById('instance-detection-result');
  if (resBox) { resBox.innerHTML = ''; resBox.style.display = 'none'; }
  const advContainer = document.getElementById('inst-advanced-container');
  if (advContainer) advContainer.style.display = 'none';
  const advArrow = document.getElementById('inst-adv-arrow');
  if (advArrow) advArrow.textContent = '▸';

  const saveBtn = document.getElementById('btn-save-instance');
  if (saveBtn) saveBtn.innerHTML = t('ui.aggiungi_istanza_server');

  modal.style.display = 'flex';
  syncModalScrollLock();
}

function openEditInstanceModal(instanceId) {
  const inst = (currentInstancesData.instances || []).find(i => i.id === instanceId);
  if (!inst) return;
  const modal = document.getElementById('modal-instance-editor');
  if (!modal) return;
  document.getElementById('instance-modal-title').textContent = t('msg.modifica_istanza_server');
  document.getElementById('inst-edit-id').value = inst.id;
  document.getElementById('inst-edit-name').value = inst.name || '';
  document.getElementById('inst-edit-dir').value = inst.server_dir || '';
  document.getElementById('inst-edit-bat').value = inst.server_run_bat || inst.run_bat || 'run.bat';
  document.getElementById('inst-edit-cmd').value = inst.server_command || inst.launch_cmd || '';
  document.getElementById('inst-edit-port').value = inst.server_port || inst.port || 25565;
  document.getElementById('inst-edit-type').value = inst.server_type || inst.type || '';
  document.getElementById('inst-edit-version').value = inst.mc_version || inst.version || '';

  const card = document.getElementById('instance-detection-card');
  if (card) card.style.display = 'none';
  const advContainer = document.getElementById('inst-advanced-container');
  if (advContainer) advContainer.style.display = 'block';
  const advArrow = document.getElementById('inst-adv-arrow');
  if (advArrow) advArrow.textContent = '▾';

  const saveBtn = document.getElementById('btn-save-instance');
  if (saveBtn) saveBtn.innerHTML = t('ui.salva_modifiche');

  modal.style.display = 'flex';
  syncModalScrollLock();
}

async function submitInstanceForm() {
  const id = document.getElementById('inst-edit-id').value.trim();
  const dir = document.getElementById('inst-edit-dir').value.trim();
  let name = document.getElementById('inst-edit-name').value.trim();
  const bat = document.getElementById('inst-edit-bat').value.trim();
  const cmd = document.getElementById('inst-edit-cmd').value.trim();
  const port = parseInt(document.getElementById('inst-edit-port').value, 10) || 25565;
  const type = document.getElementById('inst-edit-type').value;
  const version = document.getElementById('inst-edit-version').value.trim();

  if (!dir) {
    showToast(t('msg.cartella_server_obbligatoria'), 'error');
    const inputDir = document.getElementById('inst-edit-dir');
    if (inputDir) inputDir.focus();
    return;
  }

  // If name is still empty, let backend or basename assign it
  if (!name) {
    const parts = dir.replace(/\\/g, '/').split('/').filter(Boolean);
    name = parts.length > 0 ? parts[parts.length - 1] : t('serverbar.server_minecraft');
  }

  const payload = {
    name,
    server_dir: dir,
    server_run_bat: bat,
    server_command: cmd,
    server_port: port,
    server_type: type,
    mc_version: version,
    run_bat: bat,
    launch_cmd: cmd,
    port,
    type,
    version
  };

  try {
    let res;
    if (id) {
      payload.instance_id = id;
      res = await fetch('/api/instances/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    } else {
      res = await fetch('/api/instances/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
    }

    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || t('msg.errore_salvataggio_istanza'), 'error');
      return;
    }

    showToast(data.message || t('msg.istanza_salvata'), 'success');
    closeModal('modal-instance-editor');
    loadInstancesList(true);
  } catch (err) {
    console.error((t('msg.errore_salvataggio_istanza') + ":"), err);
    showToast(t('msg.errore_comunicazione_server'), 'error');
  }
}

async function deleteInstanceProfile(instanceId, instanceName) {
  if (!confirm(t('msg.sei_sicuro_voler_rimuovere_profilo', instanceName))) {
    return;
  }
  try {
    const res = await fetch('/api/instances/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ instance_id: instanceId })
    });
    const data = await res.json();
    if (!res.ok) {
      showToast(data.detail || t('msg.impossibile_eliminare_l_istanza'), 'error');
      return;
    }
    showToast(data.message || t('msg.istanza_rimossa'), 'success');
    loadInstancesList(true);
  } catch (err) {
    showToast((t('msg.errore_eliminazione') + " ") + err.message, 'error');
  }
}

// =========================================================
// COMMERCIAL UPGRADE: FULL WEB FILE MANAGER
// =========================================================
let currentFileManagerPath = '';
let currentFileManagerFiles = [];
let activeEditingFilePath = '';

async function loadDirectoryFiles(subpath = '', showToastOnSuccess = false) {
  currentFileManagerPath = subpath || '';
  const tbody = document.getElementById('fm-table-body');
  if (tbody) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; padding: 30px; color: #9a9ca2;">${th('ui.caricamento_file_corso')}</td></tr>`;
  }

  try {
    const res = await fetch(`/api/files/list?subpath=${encodeURIComponent(currentFileManagerPath)}`);
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    currentFileManagerFiles = data.files || [];

    renderFileManagerBreadcrumbs(data.breadcrumbs || []);
    renderFileManagerTable(currentFileManagerFiles);
    if (showToastOnSuccess) showToast(t('msg.cartella_aggiornata'), 'info');
  } catch (err) {
    if (tbody) {
      tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; padding: 30px; color: #e5566f;">${th('tpl.errore', escapeHtml(err.message))}</td></tr>`;
    }
    showToast((t('msg.errore_file_manager') + " ") + err.message, 'error');
  }
}

function renderFileManagerBreadcrumbs(breadcrumbs) {
  const container = document.getElementById('fm-breadcrumbs');
  if (!container) return;
  if (!breadcrumbs || breadcrumbs.length === 0) {
    breadcrumbs = [{ name: 'Root Server', path: '' }];
  }

  container.innerHTML = breadcrumbs.map((bc, idx) => {
    const isLast = idx === breadcrumbs.length - 1;
    if (isLast) {
      return `<span class="fm-bc-item active">${escapeHtml(bc.name)}</span>`;
    }
    return `
      <span class="fm-bc-item" onclick="loadDirectoryFiles('${escapeJsAttr(bc.path)}')">${escapeHtml(bc.name)}</span>
      <span class="fm-bc-sep">/</span>
    `;
  }).join('');
}

function renderFileManagerTable(files) {
  const tbody = document.getElementById('fm-table-body');
  if (!tbody) return;

  if (!files || files.length === 0) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align: center; padding: 40px; color: #9a9ca2;">${th('msg.cartella_vuota')}</td></tr>`;
    return;
  }

  tbody.innerHTML = files.map(f => {
    const isDir = f.is_dir;
    const isEditable = !isDir && (f.is_text || f.extension === '.txt' || f.extension === '.json' || f.extension === '.properties' || f.extension === '.yml' || f.extension === '.yaml' || f.extension === '.log' || f.extension === '.bat' || f.extension === '.sh' || f.extension === '.toml' || f.extension === '.cfg');
    const isZip = !isDir && f.extension === '.zip';

    let icon = '<svg class="ui-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M3.5 1.5h6l3 3v10h-9z M9.5 1.5v3h3" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';
    if (isDir) icon = '<svg class="ui-icon" viewBox="0 0 16 16" width="16" height="16" aria-hidden="true"><path d="M1.5 4.5v8h13v-7H7.2L5.8 4.5z" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>';

    const safeRel = escapeJsString(f.rel_path);
    const safeName = escapeHtml(f.name);

    return `
      <tr class="fm-row" data-name="${safeName.toLowerCase()}">
        <td class="fm-icon-cell">${icon}</td>
        <td>
          ${isDir ? `
            <a href="javascript:void(0)" class="fm-item-link is-dir" onclick="loadDirectoryFiles('${escapeJsAttr(f.rel_path)}')">${safeName}</a>
          ` : isEditable ? `
            <a href="javascript:void(0)" class="fm-item-link" onclick="openFileForEditing('${escapeJsAttr(f.rel_path)}', '${escapeJsAttr(f.name)}')">${safeName}</a>
          ` : `
            <span class="fm-item-name">${safeName}</span>
          `}
        </td>
        <td class="font-mono text-muted" style="font-size: 0.8rem;">${isDir ? '--' : (f.size_formatted || '--')}</td>
        <td class="font-mono text-muted" style="font-size: 0.78rem;">${f.modified_formatted || '--'}</td>
        <td style="text-align: right;">
          <div class="fm-row-actions">
            ${isEditable ? `
              <button class="btn btn-xs btn-secondary btn-fm-action" onclick="openFileForEditing('${escapeJsAttr(f.rel_path)}', '${escapeJsAttr(f.name)}')" title="${th('msg.modifica_file_nel_browser')}">${th('files.modifica')}</button>
            ` : ''}
            ${isZip ? `
              <button class="btn btn-xs btn-secondary btn-fm-action" onclick="unzipArchiveItem('${escapeJsAttr(f.rel_path)}')" title="${th('msg.estrai_archivio_zip_qui')}">${th('files.estrai')}</button>
            ` : ''}
            ${!isDir ? `
              <a href="/api/files/download?subpath=${encodeURIComponent(f.rel_path)}" class="btn btn-xs btn-secondary btn-fm-action" download title="${th('msg.scarica_file_2')}">${th('ui.scarica')}</a>
            ` : ''}
            <button class="btn btn-xs btn-secondary btn-fm-action" onclick="promptRenameItem('${escapeJsAttr(f.rel_path)}', '${escapeJsAttr(f.name)}')" title="${th('ui.rinomina')}">${th('ui.rinomina')}</button>
            <button class="btn btn-xs btn-danger-subtle btn-fm-action btn-fm-delete" onclick="deleteFileManagerItem('${escapeJsAttr(f.rel_path)}', '${escapeJsAttr(f.name)}', ${isDir})" title="${th('msg.elimina')}">${th('msg.elimina')}</button>
          </div>
        </td>
      </tr>
    `;
  }).join('');
}

function filterFileManagerTable() {
  const query = (document.getElementById('fm-search-input')?.value || '').trim().toLowerCase();
  const rows = document.querySelectorAll('#fm-table-body .fm-row');
  rows.forEach(r => {
    const name = r.getAttribute('data-name') || '';
    r.style.display = (!query || name.includes(query)) ? '' : 'none';
  });
}

async function openFileForEditing(subpath, filename) {
  activeEditingFilePath = subpath;
  const modal = document.getElementById('modal-file-editor');
  if (!modal) return;
  document.getElementById('fm-editor-filename').textContent = t('msg.modifica', filename);
  document.getElementById('fm-editor-filepath').textContent = subpath;
  document.getElementById('fm-editor-status').textContent = t('msg.caricamento_file');
  document.getElementById('fm-editor-textarea').value = '';
  modal.style.display = 'flex';
  syncModalScrollLock();

  try {
    const res = await fetch(`/api/files/read?subpath=${encodeURIComponent(subpath)}`);
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
    document.getElementById('fm-editor-textarea').value = data.content || '';
    document.getElementById('fm-editor-status').textContent = t('msg.caricato_caratteri', data.size_formatted || '', (data.content || '').length);
  } catch (err) {
    document.getElementById('fm-editor-status').textContent = t('tpl.errore', err.message);
    showToast((t('msg.impossibile_aprire_file') + " ") + err.message, 'error');
  }
}

async function saveActiveFileManagerFile() {
  if (!activeEditingFilePath) return;
  const content = document.getElementById('fm-editor-textarea').value;
  const statusEl = document.getElementById('fm-editor-status');
  if (statusEl) statusEl.textContent = t('msg.salvataggio_corso');

  try {
    const res = await fetch('/api/files/save', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subpath: activeEditingFilePath, content: content })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.errore_salvataggio_file_2'));
    if (statusEl) statusEl.textContent = t('msg.salvato_successo_backup_bak_creato');
    showToast(data.message || t('msg.file_salvato_successo_2'), 'success');
  } catch (err) {
    if (statusEl) statusEl.textContent = t('tpl.errore', err.message);
    showToast((t('msg.errore_salvataggio') + " ") + err.message, 'error');
  }
}

async function promptCreateNewItem(isFolder = false) {
  const title = isFolder ? t('msg.inserisci_nome_nuova_cartella') : t('msg.inserisci_nome_nuovo_file_es');
  const name = prompt(title);
  if (!name || !name.trim()) return;

  try {
    const res = await fetch('/api/files/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        subpath: currentFileManagerPath,
        name: name.trim(),
        is_dir: isFolder
      })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.errore_creazione_elemento'));
    showToast(data.message || t('msg.creato_successo'), 'success');
    loadDirectoryFiles(currentFileManagerPath);
  } catch (err) {
    showToast((t('msg.errore_2') + " ") + err.message, 'error');
  }
}

async function deleteFileManagerItem(subpath, name, isDir) {
  const typeStr = isDir ? t('msg.cartella_tutto_suo_contenuto') : t('msg.file_2');
  if (!confirm(t('msg.sei_sicuro_voler_eliminare_definitivamente_5', typeStr, name))) {
    return;
  }

  try {
    const res = await fetch('/api/files/delete', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subpath })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.errore_eliminazione_2'));
    showToast(data.message || t('msg.eliminato_successo'), 'success');
    loadDirectoryFiles(currentFileManagerPath);
  } catch (err) {
    showToast((t('msg.errore_eliminazione') + " ") + err.message, 'error');
  }
}

async function promptRenameItem(subpath, oldName) {
  const newName = prompt(t('msg.rinomina', oldName), oldName);
  if (!newName || !newName.trim() || newName.trim() === oldName) return;

  try {
    const res = await fetch('/api/files/rename', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subpath, new_name: newName.trim() })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.errore_durante_ridenominazione'));
    showToast(data.message || t('msg.rinominato_successo'), 'success');
    loadDirectoryFiles(currentFileManagerPath);
  } catch (err) {
    showToast((t('msg.errore_2') + " ") + err.message, 'error');
  }
}

async function unzipArchiveItem(subpath) {
  if (!confirm(t('msg.vuoi_estrarre_questo_archivio_zip'))) {
    return;
  }
  showToast(t('msg.estrazione_archivio_zip_corso'), 'info');
  try {
    const res = await fetch('/api/files/unzip', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ subpath })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.errore_estrazione_zip'));
    showToast(data.message || t('msg.archivio_estratto_successo'), 'success');
    loadDirectoryFiles(currentFileManagerPath);
  } catch (err) {
    showToast((t('msg.errore_estrazione') + " ") + err.message, 'error');
  }
}

async function handleFileManagerUpload(event) {
  const fileInput = event.target;
  if (!fileInput.files || fileInput.files.length === 0) return;
  const file = fileInput.files[0];

  showToast(t('msg.caricamento_corso_2', file.name), 'info');
  try {
    const url = `/api/files/upload?subpath=${encodeURIComponent(currentFileManagerPath)}&filename=${encodeURIComponent(file.name)}`;
    const res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/octet-stream' },
      body: file
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.errore_durante_caricamento'));
    showToast(data.message || t('msg.file_caricato_successo'), 'success');
    fileInput.value = '';
    loadDirectoryFiles(currentFileManagerPath);
  } catch (err) {
    showToast((t('msg.errore_caricamento') + " ") + err.message, 'error');
    fileInput.value = '';
  }
}

// =========================================================
// COMMERCIAL UPGRADE: MODRINTH MOD & PLUGIN STORE (1-CLICK)
// =========================================================
let currentStoreResults = [];
let currentStoreOffset = 0;
let currentStoreTotalHits = 0;
let currentStoreLimit = 24;
let currentStoreActiveView = 'catalog'; // 'catalog' | 'installed'
let currentStoreInstalledList = [];

function switchStoreView(view) {
  currentStoreActiveView = view;
  const btnCatalog = document.getElementById('btn-store-view-catalog');
  const btnInstalled = document.getElementById('btn-store-view-installed');
  const catalogContainer = document.getElementById('store-view-catalog-container');
  const installedContainer = document.getElementById('store-view-installed-container');

  if (view === 'installed') {
    if (btnCatalog) btnCatalog.classList.remove('active');
    if (btnInstalled) btnInstalled.classList.add('active');
    if (catalogContainer) catalogContainer.style.display = 'none';
    if (installedContainer) installedContainer.style.display = 'block';
    loadInstalledStoreView();
  } else {
    if (btnCatalog) btnCatalog.classList.add('active');
    if (btnInstalled) btnInstalled.classList.remove('active');
    if (catalogContainer) catalogContainer.style.display = 'block';
    if (installedContainer) installedContainer.style.display = 'none';
    if (currentStoreResults.length === 0) {
      executeStoreSearch(0);
    }
  }
}

async function executeStoreSearch(offset = 0, append = false) {
  const query = (document.getElementById('store-search-query')?.value || '').trim();
  const loader = document.getElementById('store-filter-loader')?.value || 'all';
  const version = document.getElementById('store-filter-version')?.value || '1.20.1';
  const category = document.getElementById('store-filter-category')?.value || 'all';
  const sort = document.getElementById('store-filter-sort')?.value || 'downloads';
  const limit = parseInt(document.getElementById('store-filter-limit')?.value, 10) || 24;

  currentStoreOffset = offset;
  currentStoreLimit = limit;

  // Toggle clear search button
  const clearBtn = document.getElementById('btn-store-clear');
  if (clearBtn) clearBtn.style.display = query ? 'inline-block' : 'none';

  const container = document.getElementById('store-results-container');
  if (container && !append) {
    container.innerHTML = `<div style="grid-column: 1/-1; padding: 50px; text-align: center; color: #9a9ca2;"><div class="spinner-inline" style="margin-bottom: 10px;"></div>${th('msg.ricerca_nel_catalogo_modrinth_corso')}</div>`;
  }

  const targetBadge = document.getElementById('store-target-badge');
  if (targetBadge) {
    const isPlugin = loader === 'paper' || loader === 'purpur' || loader === 'spigot';
    const filterLabel = [loader === 'all' ? '' : loader.toUpperCase(), version === 'all' ? '' : version].filter(Boolean).join(' ');
    targetBadge.textContent = `${t('msg.destinazione_xx', isPlugin ? 'plugins/' : 'mods/', filterLabel ? ` (${filterLabel})` : '')}`;
  }

  try {
    const params = new URLSearchParams({
      query,
      loader,
      game_version: version,
      category,
      sort,
      limit: String(limit),
      offset: String(offset)
    });

    const res = await fetch(`/api/store/search?${params.toString()}`);
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    currentStoreTotalHits = data.total_hits || 0;

    if (append) {
      currentStoreResults = currentStoreResults.concat(data.hits || []);
    } else {
      currentStoreResults = data.hits || [];
    }

    renderStoreResults(currentStoreResults, currentStoreTotalHits);
    renderStorePagination(currentStoreTotalHits, currentStoreOffset, currentStoreLimit);
    updateInstalledStoreCountBadge();
  } catch (err) {
    if (container && !append) {
      container.innerHTML = `<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #e5566f;">${th('msg.errore_ricerca_store', escapeHtml(err.message))}</div>`;
    }
    showToast((t('msg.errore_store_modrinth') + " ") + err.message, 'error');
  }
}

function clearStoreSearch() {
  const queryInput = document.getElementById('store-search-query');
  if (queryInput) queryInput.value = '';
  const clearBtn = document.getElementById('btn-store-clear');
  if (clearBtn) clearBtn.style.display = 'none';
  executeStoreSearch(0);
}

function applyStoreQuickFilter(category) {
  const select = document.getElementById('store-filter-category');
  if (select) select.value = category;

  document.querySelectorAll('.store-chip-btn').forEach(btn => btn.classList.remove('active'));
  const activeChip = document.getElementById(`chip-filter-${category}`);
  if (activeChip) activeChip.classList.add('active');

  executeStoreSearch(0);
}

function renderStoreResults(hits, totalHits) {
  const container = document.getElementById('store-results-container');
  if (!container) return;

  const totalEl = document.getElementById('store-results-total');
  if (totalEl) totalEl.textContent = Number(totalHits).toLocaleString('it-IT');

  if (!hits || hits.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1/-1; padding: 50px 20px; text-align: center; color: #9a9ca2; background: rgba(33, 32, 35, 0.4); border-radius: 12px; border: 1px dashed rgba(255, 255, 255, 0.1);">
        <div style="font-size: 2rem; margin-bottom: 10px;"></div>
        <h4 style="color: #f4f4f5; margin: 0 0 6px;">${th('msg.nessuna_mod_o_plugin_trovato')}</h4>
        <p style="font-size: 0.85rem; margin: 0 0 14px; color: #9a9ca2;">${th('msg.nessun_risultato_corrisponde_ai_filtri')}</p>
        <button class="btn btn-secondary btn-sm" onclick="clearStoreSearch()">${th('msg.reimposta_ricerca')}</button>
      </div>
    `;
    return;
  }

  container.innerHTML = hits.map(hit => {
    const iconUrl = hit.icon_url || '';
    const title = escapeHtml(hit.title || hit.slug || 'Mod');
    const author = escapeHtml(hit.author || t('msg.anonimo'));
    const desc = escapeHtml(hit.description || t('msg.nessuna_descrizione_fornita'));
    const downloads = formatDownloadCount(hit.downloads || 0);
    const pType = hit.project_type || 'mod';
    const isInstalled = Boolean(hit.is_installed);
    const instInfo = hit.installed_info || {};
    const instFilename = instInfo.filename || '';
    const hasUpdate = Boolean(hit.update_available) || (availableModUpdates && instFilename && Boolean(availableModUpdates[instFilename]));
    const updInfo = hit.update_info || (instFilename ? availableModUpdates[instFilename] : null) || {};
    const updateVer = updInfo.version_number || hit.latest_version;

    const categories = (hit.categories || []).slice(0, 3).map(c =>
      `<span class="store-badge-tag">${escapeHtml(c)}</span>`
    ).join('');

    const btnId = `btn-store-${hit.project_id}`;

    return `
      <div class="store-card ${isInstalled ? 'is-installed-card' : ''}" id="card-store-${hit.project_id}">
        <div class="store-card-header">
          <div class="store-card-thumb">
            ${iconUrl ? `<img src="${escapeHtml(iconUrl)}" alt="${title}" loading="lazy" onerror="this.src=''; this.parentElement.innerHTML='';">` : ''}
          </div>
          <div class="store-card-title-box">
            <h4 class="store-card-title store-card-title-link" title="${th('ui.informazioni', title)}" onclick="openStoreProjectInfo('${escapeJsAttr(hit.project_id)}')">${title}</h4>
            <div class="store-card-meta">
              <span class="store-author" title="${th('ui.autore', author)}">${th('msg.di')} <b>${author}</b></span>
              <span class="store-downloads" title="${th('ui.download_totali', Number(hit.downloads || 0).toLocaleString())}">${downloads}</span>
            </div>
          </div>
        </div>
        <p class="store-card-desc" title="${desc}">${desc}</p>
        <div class="store-card-tags-row">
          ${isInstalled ? (
            hasUpdate ? `<span class="badge badge-amber" style="font-size: 0.68rem; padding: 2px 7px; background: rgba(245,158,11,0.22); color: #e3b35a; border: 1px solid rgba(245,158,11,0.4);">${th('msg.aggiornamento_vx_2', escapeHtml(updateVer || ''))}</span>`
                      : `<span class="badge badge-emerald" style="font-size: 0.68rem; padding: 2px 7px;">${th('msg.installata')}</span>`
          ) : ''}
          ${categories}
        </div>
        <div class="store-card-footer">
          <div style="display: flex; gap: 8px; align-items: center;">
            <span style="font-size: 0.72rem; color: #8a8c92; font-family: var(--font-mono); text-transform: uppercase;">${escapeHtml(pType)}</span>
            <button class="btn-mod-info" onclick="openStoreProjectInfo('${escapeJsAttr(hit.project_id)}')" title="${th('ui.informazioni_questa', pType === 'plugin' ? 'plugin' : 'mod')}">Info</button>
          </div>
          <div id="action-box-${hit.project_id}">
            ${isInstalled ? (
              hasUpdate ? `
                <div style="display: flex; gap: 6px; align-items: center;">
                  <button class="btn btn-sm btn-amber store-btn-action" id="btn-upd-${hit.project_id}" onclick="updateStoreProjectFromCatalog('${hit.project_id}', '${escapeJsString(hit.title || hit.slug)}', 'btn-upd-${hit.project_id}')" title="${th('ui.scarica_aggiorna_1_clic_alla', escapeHtml(updateVer || ''))}">
                    ${th('pc.versions.aggiorna_1_click')}
                  </button>
                  <button class="btn btn-sm btn-danger-subtle store-btn-action" id="${btnId}" onclick="uninstallStoreProject('${hit.project_id}', '${escapeJsString(hit.title || hit.slug)}', '${escapeJsString(instFilename)}', '${btnId}')" title="${th('msg.disinstalla_questo_file_dal_server')}">
                    ${th('msg.disinstalla')}
                  </button>
                </div>
              ` : `
                <div style="display: flex; gap: 6px; align-items: center;">
                  <button class="btn btn-sm btn-danger-subtle store-btn-action" id="${btnId}" onclick="uninstallStoreProject('${hit.project_id}', '${escapeJsString(hit.title || hit.slug)}', '${escapeJsString(instFilename)}', '${btnId}')" title="${th('msg.disinstalla_questo_file_dal_server')}">
                    ${th('msg.disinstalla')}
                  </button>
                </div>
              `
            ) : `
              <button class="btn btn-sm btn-emerald store-btn-action" id="${btnId}" onclick="installStoreProject('${hit.project_id}', '${escapeJsString(hit.title || hit.slug)}', '${pType}', '${btnId}')" title="${th('msg.scarica_installa_nel_server')}">
                ${th('msg.installa_1_click')}
              </button>
            `}
          </div>
        </div>
      </div>
    `;
  }).join('');
}

function renderStorePagination(totalHits, currentOffset, limit) {
  const container = document.getElementById('store-pagination-bar');
  if (!container) return;

  const totalPages = Math.max(1, Math.ceil(totalHits / limit));
  const currentPage = Math.floor(currentOffset / limit) + 1;

  const curEl = document.getElementById('store-page-current');
  const totEl = document.getElementById('store-page-total');
  if (curEl) curEl.textContent = currentPage;
  if (totEl) totEl.textContent = totalPages;

  if (totalPages <= 1) {
    container.innerHTML = '';
    return;
  }

  let html = '';

  // First & Prev buttons
  html += `
    <button class="store-page-btn" onclick="executeStoreSearch(0)" ${currentPage === 1 ? 'disabled' : ''} title="${th('ui.prima_pagina')}">${th('msg.prima')}</button>
    <button class="store-page-btn" onclick="executeStoreSearch(${Math.max(0, (currentPage - 2) * limit)})" ${currentPage === 1 ? 'disabled' : ''} title="${th('ui.pagina_precedente')}">${th('msg.prec')}</button>
  `;

  // Page number buttons (sliding window of 5 pages)
  let startP = Math.max(1, currentPage - 2);
  let endP = Math.min(totalPages, startP + 4);
  if (endP - startP < 4) {
    startP = Math.max(1, endP - 4);
  }

  for (let p = startP; p <= endP; p++) {
    const pOffset = (p - 1) * limit;
    html += `
      <button class="store-page-btn ${p === currentPage ? 'active' : ''}" onclick="executeStoreSearch(${pOffset})">
        ${p}
      </button>
    `;
  }

  // Next & Last buttons
  html += `
    <button class="store-page-btn" onclick="executeStoreSearch(${Math.min((totalPages - 1) * limit, currentPage * limit)})" ${currentPage === totalPages ? 'disabled' : ''} title="${th('ui.pagina_successiva')}">${th('msg.succ')} </button>
    <button class="store-page-btn" onclick="executeStoreSearch(${(totalPages - 1) * limit})" ${currentPage === totalPages ? 'disabled' : ''} title="${th('ui.ultima_pagina')}">${th('msg.ultima')} </button>
  `;

  // Load more button (append style)
  if (currentPage < totalPages) {
    const nextOffset = currentPage * limit;
    html += `
      <button class="btn btn-sm btn-secondary" onclick="executeStoreSearch(${nextOffset}, true)" style="margin-left: 12px;" title="${th('ui.aggiungi_altri_risultati_sotto_quelli')}">
        ${th('msg.mostra_altri', limit)}
      </button>
    `;
  }

  container.innerHTML = html;
}

function formatDownloadCount(n) {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

async function installStoreProject(projectId, projectTitle, projectType, btnId) {
  const loader = document.getElementById('store-filter-loader')?.value || 'all';
  const version = document.getElementById('store-filter-version')?.value || '1.20.1';

  if (!confirm(t('msg.vuoi_scaricare_installare_direttamente_nel', projectTitle, loader.toUpperCase(), version))) {
    return;
  }

  const btn = document.getElementById(btnId || `btn-store-${projectId}`);
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('msg.installazione');
  }

  showToast(t('msg.installazione_corso', projectTitle), 'info');

  try {
    const res = await fetch('/api/store/install', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        project_type: projectType,
        loader: loader === 'all' ? 'forge' : loader,
        game_version: version === 'all' ? '1.20.1' : version
      })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.installazione_fallita_2'));

    showToast(t('msg.installato_successo', projectTitle, data.dest_folder), 'success');

    // Update card state in DOM
    const card = document.getElementById(`card-store-${projectId}`);
    if (card) {
      card.classList.add('is-installed-card');
      const actionBox = document.getElementById(`action-box-${projectId}`);
      if (actionBox) {
        actionBox.innerHTML = `
          <div style="display: flex; gap: 6px; align-items: center;">
            <button class="btn btn-sm btn-danger-subtle store-btn-action" id="${btnId}" onclick="uninstallStoreProject('${projectId}', '${escapeJsString(projectTitle)}', '${escapeJsString(data.filename || '')}', '${btnId}')" title="${th('msg.disinstalla_questo_file_dal_server')}">
              ${th('msg.disinstalla')}
            </button>
          </div>
        `;
      }
    }

    updateInstalledStoreCountBadge();
    if (typeof loadModsList === 'function') loadModsList();
  } catch (err) {
    showToast(t('msg.errore_installazione', err.message), 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('msg.installa_1_click');
    }
  }
}

async function uninstallStoreProject(projectId, projectTitle, filename, btnId) {
  if (!confirm(t('msg.sei_sicuro_voler_rimuovere_dal', projectTitle))) {
    return;
  }

  const btn = document.getElementById(btnId);
  if (btn) {
    btn.disabled = true;
    btn.textContent = t('msg.rimozione');
  }

  showToast(t('msg.disinstallazione_corso', projectTitle), 'info');

  try {
    const res = await fetch('/api/store/uninstall', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        project_id: projectId,
        filename: filename || null
      })
    });

    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || t('msg.disinstallazione_fallita'));

    showToast(data.message || t('msg.rimosso_successo', projectTitle), 'success');

    // Update card in catalog view
    const card = document.getElementById(`card-store-${projectId}`);
    if (card) {
      card.classList.remove('is-installed-card');
      const actionBox = document.getElementById(`action-box-${projectId}`);
      if (actionBox) {
        actionBox.innerHTML = `
          <button class="btn btn-sm btn-emerald store-btn-action" id="${btnId}" onclick="installStoreProject('${projectId}', '${escapeJsString(projectTitle)}', 'mod', '${btnId}')" title="${th('msg.scarica_installa_nel_server')}">
            ${th('msg.installa_1_click')}
          </button>
        `;
      }
    }

    // If currently in installed view, reload it
    if (currentStoreActiveView === 'installed') {
      loadInstalledStoreView(true);
    }

    updateInstalledStoreCountBadge();
    if (typeof loadModsList === 'function') loadModsList();
  } catch (err) {
    showToast(t('msg.errore_disinstallazione', err.message), 'error');
    if (btn) {
      btn.disabled = false;
      btn.textContent = t('msg.disinstalla');
    }
  }
}

async function loadInstalledStoreView(force = false) {
  const container = document.getElementById('store-installed-list');
  if (container) {
    container.innerHTML = `<div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #9a9ca2;"><div class="spinner-inline" style="margin-bottom: 8px;"></div>${th('msg.caricamento_mod_plugin_installati_sul')}</div>`;
  }

  try {
    const res = await fetch('/api/store/installed');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    currentStoreInstalledList = data.items || [];

    if (data.updates_count !== undefined) {
      availableModUpdatesCount = data.updates_count;
      syncModUpdateBadges();
    }

    const badge = document.getElementById('store-installed-pill-count');
    if (badge) badge.textContent = currentStoreInstalledList.length;

    renderInstalledStoreList(currentStoreInstalledList);
  } catch (err) {
    if (container) {
      container.innerHTML = `<div style="grid-column: 1/-1; padding: 30px; text-align: center; color: #e5566f;">${th('msg.errore_recupero_mod_installate', escapeHtml(err.message))}</div>`;
    }
  }
}

function renderInstalledStoreList(items) {
  const container = document.getElementById('store-installed-list');
  if (!container) return;

  if (!items || items.length === 0) {
    container.innerHTML = `
      <div style="grid-column: 1/-1; padding: 40px; text-align: center; color: #9a9ca2; background: rgba(33, 32, 35, 0.4); border-radius: 12px;">
        <div style="font-size: 2rem; margin-bottom: 8px;"></div>
        <h4 style="color: #f4f4f5; margin: 0 0 6px;">${th('msg.nessuna_mod_o_plugin_installato')}</h4>
        <p style="font-size: 0.85rem; margin: 0 0 14px;">${th('msg.cartelle')} <code>mods/</code> e <code>plugins/</code> ${th('msg.server_sono_vuote')}</p>
        <button class="btn btn-emerald btn-sm" onclick="switchStoreView('catalog')">${th('msg.esplora_catalogo_online')}</button>
      </div>
    `;
    return;
  }

  container.innerHTML = items.map((itm, idx) => {
    const isPlugin = itm.folder === 'plugins';
    const folderBadge = isPlugin ? '<span class="badge badge-sapphire" style="font-size: 0.68rem;">PLUGIN</span>' : '<span class="badge badge-emerald" style="font-size: 0.68rem;">MOD</span>';
    const cardId = `inst-row-${idx}`;
    const upd = itm.update_info || availableModUpdates[itm.filename];
    const updBadge = upd ? `<span class="badge badge-amber" style="font-size: 0.68rem; padding: 2px 7px; background: rgba(245,158,11,0.22); color: #e3b35a; border: 1px solid rgba(245,158,11,0.4);" title="${th('ui.nuova_versione', escapeHtml(upd.version_number), escapeHtml(upd.new_filename))}">${th('msg.aggiornamento_vx_2', escapeHtml(upd.version_number))}</span>` : '';
    const updBtn = upd ? `
      <button class="btn btn-sm btn-amber" onclick="updateStoreProjectFromInstalled('${escapeJsString(itm.filename)}', '${escapeJsString(itm.project_id || '')}', this)" title="${th('ui.scarica_aggiorna_1_clic_alla', escapeHtml(upd.version_number))}">
        ${th('ui.aggiorna')}
      </button>
    ` : '';
    // Mod: scheda letta dal .jar (come nella scheda Mod); plugin: dati del progetto Modrinth, se si conosce
    const infoBtn = !isPlugin
      ? `<button class="btn-mod-info" onclick="openModInfo('${escapeJsAttr(itm.filename)}')" title="${th('ui.informazioni_mod')}">Info</button>`
      : (itm.project_id ? `<button class="btn-mod-info" onclick="openStoreProjectInfo('${escapeJsAttr(itm.project_id)}')" title="${th('ui.informazioni_plugin')}">Info</button>` : '');

    return `
      <div class="store-installed-card" id="${cardId}" data-name="${escapeHtml(itm.filename.toLowerCase())}">
        <div style="font-size: 1.5rem; flex-shrink: 0;">${isPlugin ? '' : ''}</div>
        <div class="store-installed-info">
          <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
            ${folderBadge}
            <span class="store-installed-name" title="${escapeHtml(itm.filename)}">${escapeHtml(itm.filename)}</span>
            ${updBadge}
          </div>
          <div class="store-installed-meta">
            <span>${th('msg.dimensione')} <b>${itm.size_formatted || '--'}</b></span>
            <span>&bull;</span>
            <span>${th('msg.modificato_2')} <b>${itm.modified || '--'}</b></span>
          </div>
        </div>
        <div style="display: flex; gap: 6px; align-items: center;">
          ${infoBtn}
          ${updBtn}
          <button class="btn btn-sm btn-danger-subtle" onclick="uninstallStoreProject('${escapeJsString(itm.project_id || itm.filename)}', '${escapeJsString(itm.filename)}', '${escapeJsString(itm.filename)}', 'btn-del-inst-${idx}')" id="btn-del-inst-${idx}" title="${th('ui.elimina_questo_file_dal_server')}">
            ${th('msg.disinstalla')}
          </button>
        </div>
      </div>
    `;
  }).join('');
}

function filterInstalledStoreList() {
  const query = (document.getElementById('store-installed-filter')?.value || '').trim().toLowerCase();
  const rows = document.querySelectorAll('.store-installed-card');
  rows.forEach(r => {
    const name = r.getAttribute('data-name') || '';
    r.style.display = (!query || name.includes(query)) ? 'flex' : 'none';
  });
}

async function updateInstalledStoreCountBadge() {
  try {
    const res = await fetch('/api/store/installed');
    if (res.ok) {
      const data = await res.json();
      const badge = document.getElementById('store-installed-pill-count');
      if (badge) badge.textContent = (data.items || []).length;
    }
  } catch (e) {}
}

// =========================================================
// MOD UPDATES ENGINE (CHECK & 1-CLICK ACTIONS)
// =========================================================
async function checkModUpdates(isManual = false) {
  const icon = document.getElementById('icon-check-mod-upd');
  if (icon) icon.textContent = '';

  try {
    const res = await fetch('/api/store/updates?force=true');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    if (data.success === false) throw new Error(data.error || t('msg.controllo_riuscito'));
    availableModUpdates = data.updates_by_file || {};
    availableModUpdatesCount = data.updates_count || Object.keys(availableModUpdates).length;
    syncModUpdateBadges();

    // Re-render views
    renderMods();
    if (typeof currentStoreActiveView !== 'undefined' && currentStoreActiveView === 'installed') {
      renderInstalledStoreList(currentStoreInstalledList);
    }

    if (isManual) {
      if (availableModUpdatesCount > 0) {
        showToast(t('msg.trovati_aggiornamenti_disponibili_mod_installate', availableModUpdatesCount), 'info');
      } else {
        showToast(t('msg.tutte_mod_plugin_sono_gia'), 'success');
      }
    }
  } catch (err) {
    if (isManual) {
      showToast(t('msg.impossibile_verificare_aggiornamenti_mod', err.message), 'error');
    }
  } finally {
    if (icon) icon.textContent = '';
  }
}

async function updateSingleMod(filename, btnEl) {
  if (currentServerState === 'ONLINE') {
    const proceed = confirm(t('msg.attenzione_server_minecraft_attualmente_online_2'));
    if (!proceed) return;
  }

  const upd = availableModUpdates[filename];
  const verStr = upd ? ` ${t('msg.alla_versione_vx', upd.version_number)}` : '';
  if (!confirm(t('msg.vuoi_aggiornare_mod', filename, verStr))) {
    return;
  }

  const originalHtml = btnEl ? btnEl.innerHTML : '';
  if (btnEl) {
    btnEl.disabled = true;
    btnEl.innerHTML = t('msg.aggiornamento');
  }

  try {
    const payload = {
      current_filename: filename,
      folder: upd ? upd.folder : 'mods',
      download_url: upd ? upd.download_url : null,
      new_filename: upd ? upd.new_filename : null,
      project_id: upd ? upd.project_id : null,
      version_number: upd ? upd.version_number : null
    };

    const res = await fetch('/api/store/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();

    if (res.ok && data.success) {
      showToast(`${data.message || t('msg.mod_aggiornata_successo')}`, 'success');
      notifyModsRestartRequired();
      delete availableModUpdates[filename];
      availableModUpdatesCount = Math.max(0, availableModUpdatesCount - 1);
      syncModUpdateBadges();
      await loadModsList();
      if (typeof loadInstalledStoreView === 'function') loadInstalledStoreView(true);
    } else {
      showToast(`${data.detail || data.error || t('msg.errore_durante_aggiornamento_mod')}`, 'error');
      if (btnEl) {
        btnEl.disabled = false;
        btnEl.innerHTML = originalHtml;
      }
    }
  } catch (err) {
    showToast(t('msg.errore_rete', err.message), 'error');
    if (btnEl) {
      btnEl.disabled = false;
      btnEl.innerHTML = originalHtml;
    }
  }
}

async function updateStoreProjectFromCatalog(projectId, projectTitle, btnId) {
  if (currentServerState === 'ONLINE') {
    const proceed = confirm(t('msg.attenzione_server_minecraft_online_file'));
    if (!proceed) return;
  }

  const btn = document.getElementById(btnId);
  const origHtml = btn ? btn.innerHTML : '';
  if (btn) {
    btn.disabled = true;
    btn.innerHTML = t('msg.aggiornamento');
  }

  try {
    const res = await fetch('/api/store/update', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project_id: projectId })
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(t('msg.mod_aggiornata_successo_2', projectTitle, data.new_filename || ''), 'success');
      notifyModsRestartRequired();
      await checkModUpdates(false);
      await executeStoreSearch(currentStoreOffset);
      if (typeof loadModsList === 'function') loadModsList();
    } else {
      showToast(`${data.detail || data.error || t('msg.errore_aggiornamento_mod')}`, 'error');
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = origHtml;
      }
    }
  } catch (err) {
    showToast(t('msg.errore_rete', err.message), 'error');
    if (btn) {
      btn.disabled = false;
      btn.innerHTML = origHtml;
    }
  }
}

function updateStoreProjectFromInstalled(filename, projectId, btnEl) {
  updateSingleMod(filename, btnEl);
}

async function updateAllModsPrompt() {
  if (availableModUpdatesCount === 0) {
    showToast(t('msg.tutte_mod_sono_gia_aggiornate'), 'info');
    return;
  }

  if (currentServerState === 'ONLINE') {
    const proceed = confirm(t('msg.attenzione_server_minecraft_attualmente_online_3'));
    if (!proceed) return;
  }

  if (!confirm(t('msg.vuoi_aggiornare_tutte_mod_versione', availableModUpdatesCount))) {
    return;
  }

  showToast(t('msg.aggiornamento_massivo_corso_mod', availableModUpdatesCount), 'info');

  try {
    const res = await fetch('/api/store/update_all', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json();
    if (res.ok && data.success) {
      showToast(`${data.message || t('msg.tutte_mod_sono_state_aggiornate')}`, 'success');
      notifyModsRestartRequired();
      availableModUpdates = {};
      availableModUpdatesCount = 0;
      syncModUpdateBadges();
      await loadModsList();
      if (typeof loadInstalledStoreView === 'function') loadInstalledStoreView(true);
      if (typeof currentStoreActiveView !== 'undefined' && currentStoreActiveView === 'catalog') {
        executeStoreSearch(currentStoreOffset);
      }
    } else {
      showToast(`${data.detail || data.error || t('msg.errore_aggiornamento_massivo')}`, 'error');
    }
  } catch (err) {
    showToast(t('msg.errore_durante_l_aggiornamento_massivo', err.message), 'error');
  }
}

