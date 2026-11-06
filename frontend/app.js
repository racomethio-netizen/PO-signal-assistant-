const API = "";

const TIMEFRAMES = { "2m": 120, "3m": 180, "15m": 900 };
const LIVE_CANDLE_LIMIT = 200;
// Give the data vendor a few seconds to publish a just-closed candle.
const PUBLISH_GRACE_MS = 10000;
const AUTO_UPDATE_MS = 20000;
const ERROR_BACKOFF_MS = 60000;
// Newest candle this old means the market is closed (weekend/holiday):
// re-checking every tick would only burn API credits on unchanged data.
const MARKET_CLOSED_AFTER_MS = 10 * 60 * 1000;
const MARKET_CLOSED_BACKOFF_MS = 10 * 60 * 1000;

function el(id) { return document.getElementById(id); }
function safeClass(s) { return (s || "").replace(/\s+/g, "_"); }

function errorText(data, resp) {
  const d = data.detail;
  if (typeof d === "string") return d;
  if (d && d.message) return [d.message, ...(d.errors || [])].join(" ");
  if (d) return JSON.stringify(d);
  return resp.statusText || `HTTP ${resp.status}`;
}

async function apiPost(path, body) {
  const resp = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(errorText(data, resp));
  return data;
}

async function apiGet(path) {
  const resp = await fetch(API + path);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(errorText(data, resp));
  return data;
}

function currentAssetTf() {
  return { asset: el("asset").value.trim(), timeframe: el("timeframe").value };
}

function playAlertSound() {
  try {
    const ctx = new (window.AudioContext || window.webkitAudioContext)();
    const osc = ctx.createOscillator();
    const gain = ctx.createGain();
    osc.frequency.value = 880;
    osc.connect(gain);
    gain.connect(ctx.destination);
    gain.gain.setValueAtTime(0.15, ctx.currentTime);
    osc.start();
    osc.stop(ctx.currentTime + 0.18);
  } catch (e) { /* audio unavailable in this browser context */ }
}

function maybeBrowserNotify(signal, alertDecision) {
  if (!alertDecision || !alertDecision.browser) return;
  if (!("Notification" in window)) return;
  const fire = () => {
    new Notification(`${signal.asset} ${signal.timeframe} — ${signal.direction}`, {
      body: `Confidence ${signal.confidence} · Score ${Math.round(signal.score)}/100\nAnalysis only — no trade executed.`,
    });
  };
  if (Notification.permission === "granted") fire();
  else if (Notification.permission !== "denied") {
    Notification.requestPermission().then((p) => { if (p === "granted") fire(); });
  }
  if (alertDecision.sound) playAlertSound();
}

const App = {
  async refreshAll({ fetchLive = true } = {}) {
    if (fetchLive) await this.fetchLiveData();
    await this.loadInstrumentList();
    await this.loadInstrumentInfo();
    await this.loadDataStatus();
    await this.loadConnectionStatus();
    await this.generateSignal(false);
    await this.loadMultiTimeframe();
    await this.loadPaperAccount();
    await this.loadRiskStatus();
    await this.loadSignalHistory();
    await this.loadPaperHistory();
  },

  async loadDataStatus() {
    const { asset, timeframe } = currentAssetTf();
    try {
      const d = await apiGet(`/api/data/status?asset=${encodeURIComponent(asset)}&timeframe=${timeframe}`);
      el("data-status").textContent = d.candle_count
        ? `${d.candle_count} candles loaded · last: ${new Date(d.last_timestamp).toLocaleString()}`
        : "No data loaded for this asset/timeframe yet.";
    } catch (e) { el("data-status").textContent = ""; }
  },

  async loadConnectionStatus(probe = false) {
    const pill = el("conn-pill");
    try {
      const d = await apiGet("/api/market-data/status" + (probe ? "?probe=true" : ""));
      pill.className = "conn-pill conn-" + safeClass(d.status);
      pill.textContent = `Market data: ${d.status}${d.provider ? " (" + d.provider + ")" : ""}`;
      const last = d.last_successful_update ? new Date(d.last_successful_update).toLocaleString() : "never";
      el("conn-detail").textContent = d.configured
        ? `${d.message} · last successful update: ${last} · tap the status to re-test`
        : "No live data provider is configured on the server — set MARKET_DATA_PROVIDER (see .env.example).";
    } catch (e) {
      pill.className = "conn-pill conn-DISCONNECTED";
      pill.textContent = "Market data: DISCONNECTED";
      el("conn-detail").textContent = "Could not reach the market-data status endpoint: " + e.message;
    }
  },

  // Which fetch endpoint can supply live candles for this instrument, if any.
  liveSourceFor(info, conn) {
    if (info && info.instrument_type === "OTC") return null;
    const category = info ? info.category : null;
    if (category === "CRYPTO") {
      return conn.provider === "binance" ? "/api/data/fetch/provider" : "/api/data/fetch/binance";
    }
    if (!conn.provider) return null;
    if (category === "FOREX" && conn.provider !== "twelvedata") return null;
    return "/api/data/fetch/provider";
  },

  noLiveSourceMessage(info, conn) {
    if (info && info.instrument_type === "OTC") {
      return "OTC pairs have no live data feed (no data vendor sells Pocket Option's OTC prices) — upload OTC candles yourself (CSV/JSON) to analyse them.";
    }
    if (!conn.provider) {
      return "No live data provider is configured on the server — set MARKET_DATA_PROVIDER (see .env.example) or upload candles manually.";
    }
    return `The configured provider (${conn.provider}) doesn't supply this instrument — upload candles manually.`;
  },

  // Fetch fresh closed candles for every dashboard timeframe whose next
  // candle has closed since the last fetch. Returns true if anything new
  // was loaded. Timeframes that are still current cost no API credits.
  async fetchLiveData() {
    const { asset } = currentAssetTf();
    if (!asset || this._fetching) return false;
    this._fetching = true;
    try {
      const conn = await apiGet("/api/market-data/status").catch(() => ({}));
      const info = await apiGet(`/api/instruments/${encodeURIComponent(asset)}`).catch(() => null);
      const path = this.liveSourceFor(info, conn);
      if (!path) {
        el("live-status").textContent = this.noLiveSourceMessage(info, conn);
        return false;
      }
      const source = path.endsWith("/binance") ? "Binance" : conn.provider;
      let fetched = false;
      let newestClose = 0;
      const uploaded = [];
      for (const [tf, seconds] of Object.entries(TIMEFRAMES)) {
        const s = await apiGet(`/api/data/status?asset=${encodeURIComponent(asset)}&timeframe=${tf}`).catch(() => ({}));
        if (s.source === "upload") { uploaded.push(tf); continue; }  // never overwrite the user's own data
        let last = s.last_timestamp ? new Date(s.last_timestamp).getTime() : 0;
        if (!last || Date.now() >= last + 2 * seconds * 1000 + PUBLISH_GRACE_MS) {
          try {
            const data = await apiPost(path, { asset, timeframe: tf, limit: LIVE_CANDLE_LIMIT, keep_uploads: true });
            if (data.kept_upload) { uploaded.push(tf); continue; }
            last = new Date(data.last_timestamp).getTime();
            fetched = true;
          } catch (e) {
            el("live-status").textContent = `Live data error (${source}, ${asset} ${tf}): ${e.message}`;
            this._retryAfter = Date.now() + ERROR_BACKOFF_MS;
            await this.loadConnectionStatus();
            return fetched;
          }
        }
        newestClose = Math.max(newestClose, last + seconds * 1000);
      }
      const checked = new Date().toLocaleTimeString();
      const live = Object.keys(TIMEFRAMES).filter((tf) => !uploaded.includes(tf));
      const keptNote = uploaded.length
        ? ` Your uploaded ${uploaded.join(" · ")} candles are kept (never replaced by live data).` : "";
      if (!live.length) {
        el("live-status").textContent = `Using your uploaded ${asset} candles.${keptNote}`;
      } else if (Date.now() - newestClose > MARKET_CLOSED_AFTER_MS) {
        this._retryAfter = Date.now() + MARKET_CLOSED_BACKOFF_MS;
        el("live-status").textContent =
          `Market appears closed — newest ${asset} candle ended ${new Date(newestClose).toLocaleString()}. ` +
          `Signals below use that last real data. Re-checking every 10 minutes (checked ${checked}).${keptNote}`;
      } else {
        el("live-status").textContent =
          `Live data: ${asset} ${live.join(" · ")} from ${source} — up to date (checked ${checked}).${keptNote}`;
      }
      return fetched;
    } finally {
      this._fetching = false;
    }
  },

  async autoTick() {
    if (this._retryAfter && Date.now() < this._retryAfter) return;
    this._retryAfter = 0;
    if (await this.fetchLiveData()) await this.refreshAll({ fetchLive: false });
  },

  setAutoUpdate(on) {
    try { localStorage.setItem("po.autoUpdate", on ? "1" : "0"); } catch (e) { /* storage unavailable */ }
    clearInterval(this._autoTimer);
    this._autoTimer = on ? setInterval(() => this.autoTick(), AUTO_UPDATE_MS) : null;
  },

  async fetchFromProvider() {
    const { asset, timeframe } = currentAssetTf();
    const limit = parseInt(el("provider-limit").value) || 200;
    if (!asset) { alert("Enter/select an asset first."); return; }
    try {
      const data = await apiPost("/api/data/fetch/provider", { asset, timeframe, limit });
      alert(`Fetched ${data.candle_count} real candles from the configured provider for ${asset} ${timeframe}.`);
      await this.refreshAll({ fetchLive: false });
    } catch (e) {
      alert("Provider fetch failed: " + e.message);
      await this.loadConnectionStatus();
    }
  },

  async loadInstrumentList() {
    const category = el("instrument-filter-category").value;
    const type = el("instrument-filter-type").value;
    const query = el("instrument-search").value.trim();
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (type) params.set("instrument_type", type);
    if (query) params.set("query", query);
    try {
      const list = await apiGet(`/api/instruments?${params.toString()}`);
      const datalist = el("instrument-datalist");
      datalist.innerHTML = list.map((i) =>
        `<option value="${i.symbol}">${i.display_name} — ${i.category}${i.instrument_type === "OTC" ? " (OTC)" : ""}</option>`
      ).join("");
    } catch (e) { /* ignore */ }
  },

  async onAssetInput() {
    await this.loadInstrumentInfo();
  },

  async loadInstrumentInfo() {
    const { asset } = currentAssetTf();
    if (!asset) return;
    try {
      const info = await apiGet(`/api/instruments/${encodeURIComponent(asset)}`);
      const freshness = info.last_successful_data_timestamp
        ? new Date(info.last_successful_data_timestamp).toLocaleString()
        : "no data loaded";
      el("instrument-info").innerHTML =
        `<b>${info.display_name}</b> · ${info.instrument_type} · source: ${info.data_source} · ` +
        `status: <b>${info.availability_status}</b> · last data: ${freshness}`;
    } catch (e) {
      el("instrument-info").textContent =
        `'${asset}' is not a registered instrument yet — uploading data for it will register it automatically.`;
    }
  },

  async runComparison() {
    const timeframe = el("compare-timeframe").value;
    el("compare-body").innerHTML = "";
    el("compare-note").textContent = "Running...";
    try {
      const result = await apiPost("/api/instruments/compare", { timeframe });
      if (result.message) {
        el("compare-note").textContent = result.message;
        return;
      }
      el("compare-note").textContent = `${result.note} (sample size: ${result.sample_size})`;
      el("compare-body").innerHTML = result.candidates.map((c) => `<tr>
        <td>${c.display_name}</td>
        <td class="dir-${safeClass(c.direction)}">${c.direction}</td>
        <td>${c.confidence}</td>
        <td>${c.score}</td>
        <td>${c.market_regime}</td>
        <td>${c.data_quality_score}</td>
        <td>${c.composite_rank_score}</td>
        </tr>`).join("");
    } catch (e) {
      el("compare-note").textContent = "Error: " + e.message;
    }
  },

  async uploadCsv() {
    const { asset, timeframe } = currentAssetTf();
    const fileInput = el("csv-file");
    if (!fileInput.files.length) { alert("Choose a CSV file first."); return; }
    const form = new FormData();
    form.append("asset", asset);
    form.append("timeframe", timeframe);
    form.append("file", fileInput.files[0]);
    try {
      const resp = await fetch("/api/data/upload/csv", { method: "POST", body: form });
      const data = await resp.json();
      if (!resp.ok) throw new Error(JSON.stringify(data.detail));
      alert(`Loaded ${data.candle_count} candles.`);
      await this.loadDataStatus();
      await this.loadInstrumentInfo();
      await this.loadInstrumentList();
    } catch (e) { alert("Upload failed: " + e.message); }
  },

  async uploadJson() {
    const { asset, timeframe } = currentAssetTf();
    let candles;
    try { candles = JSON.parse(el("json-input").value); }
    catch (e) { alert("Invalid JSON."); return; }
    try {
      const data = await apiPost("/api/data/upload/json", { asset, timeframe, candles });
      alert(`Loaded ${data.candle_count} candles.`);
      await this.loadDataStatus();
      await this.loadInstrumentInfo();
      await this.loadInstrumentList();
    } catch (e) { alert("Upload failed: " + e.message); }
  },

  async fetchBinance() {
    const { timeframe } = currentAssetTf();
    const asset = el("binance-symbol").value.trim();
    const limit = parseInt(el("binance-limit").value) || 200;
    if (!asset) { alert("Enter a Binance symbol, e.g. BTCUSDT."); return; }
    try {
      const data = await apiPost("/api/data/fetch/binance", { asset, timeframe, limit });
      el("asset").value = asset;
      alert(`Fetched ${data.candle_count} real candles from Binance for ${asset} ${timeframe}.`);
      await this.refreshAll();
    } catch (e) { alert("Binance fetch failed: " + e.message); }
  },

  async generateSignal(explain) {
    const { asset, timeframe } = currentAssetTf();
    try {
      const data = await apiPost("/api/signal/generate", { asset, timeframe, explain_with_llm: !!explain });
      this.renderSignal(data.signal, data.alert);
    } catch (e) {
      el("signal-meta").textContent = "Error generating signal: " + e.message;
    }
  },

  renderSignal(signal, alertDecision) {
    const dirEl = el("signal-direction");
    dirEl.textContent = signal.direction;
    dirEl.className = "signal-direction dir-" + safeClass(signal.direction);

    el("signal-meta").textContent =
      `${signal.asset} · ${signal.timeframe} · Confidence: ${signal.confidence} · ` +
      `Score: ${Math.round(signal.score)}/100 · Regime: ${signal.market_regime} · ` +
      `${new Date(signal.generated_at).toLocaleString()}`;

    const fill = el("signal-score-fill");
    fill.style.width = Math.min(100, signal.score) + "%";
    fill.style.background = signal.direction.includes("CALL") ? "var(--call)"
      : signal.direction.includes("PUT") ? "var(--put)" : "var(--neutral)";

    el("signal-expiry").textContent = signal.suggested_expiry_window
      ? `Suggested expiry window: ${signal.suggested_expiry_window}` : "";

    el("signal-explanation").textContent = signal.explanation || "";

    el("reasons-supporting").innerHTML = (signal.reasons_supporting || [])
      .map((r) => `<li>✓ ${r}</li>`).join("") || "<li>None</li>";
    el("reasons-against").innerHTML = (signal.reasons_against || [])
      .map((r) => `<li>⚠ ${r}</li>`).join("") || "<li>None</li>";

    el("invalidation").textContent = signal.invalidation_condition || "";
    el("risk-warning").textContent = signal.risk_warning || "";

    this.renderTaCards(signal);

    maybeBrowserNotify(signal, alertDecision);
  },

  renderTaCards(signal) {
    const macd = signal.macd || {};
    el("card-macd").innerHTML = this.kv([
      ["MACD", fmt(macd.macd)], ["Signal", fmt(macd.signal)], ["Histogram", fmt(macd.histogram)],
    ]);

    const rsi = signal.rsi || {};
    el("card-rsi").innerHTML = this.kv([["RSI", fmt(rsi.value)], ["Period", fmt(rsi.period)]]);

    const bb = signal.bollinger || {};
    el("card-bollinger").innerHTML = this.kv([
      ["Upper", fmt(bb.upper)], ["Middle", fmt(bb.middle)], ["Lower", fmt(bb.lower)],
    ]);

    const sar = signal.sar || {};
    el("card-sar").innerHTML = this.kv([["Value", fmt(sar.value)], ["Trend", sar.trend || "n/a"]]);

    const ma = signal.moving_averages || {};
    const emaRows = Object.entries(ma.ema || {}).map(([p, v]) => [`EMA${p}`, fmt(v)]);
    const smaRows = Object.entries(ma.sma || {}).map(([p, v]) => [`SMA${p}`, fmt(v)]);
    el("card-ma").innerHTML = this.kv([...emaRows, ...smaRows]);

    const vol = signal.volatility || {};
    el("card-volatility").innerHTML = this.kv([
      ["Score", fmt(vol.score)], ["ATR", fmt(vol.atr)], ["Detail", vol.detail || ""],
    ]);

    const sr = signal.support_resistance || {};
    const levels = (sr.levels || []).map((l) => `<div class="card-kv"><span>${l.kind} $${fmt(l.price)}</span><span>${l.distance_pct}% away</span></div>`).join("");
    el("card-sr").innerHTML = levels || "<div class='card-kv'><span>No clear levels</span></div>";

    const pa = signal.price_action || {};
    el("card-price-action").innerHTML = this.kv([["Pattern", fmt(pa.pattern)], ["Bias", fmt(pa.bias)]]);

    const adx = signal.adx || {};
    el("card-adx").innerHTML = this.kv([
      ["ADX", fmt(adx.adx)], ["+DI", fmt(adx.plus_di)], ["-DI", fmt(adx.minus_di)],
      ["Regime", signal.market_regime || "n/a"],
    ]);

    const stoch = signal.stochastic || {};
    el("card-stochastic").innerHTML = this.kv([["%K", fmt(stoch.percent_k)], ["%D", fmt(stoch.percent_d)]]);

    const wr = signal.williams_r || {};
    el("card-williams").innerHTML = this.kv([["%R", fmt(wr.value)]]);

    const cq = signal.candle_quality || {};
    el("card-candle-quality").innerHTML = this.kv([
      ["Body ratio", fmt(cq.body_ratio)], ["Close position", fmt(cq.close_position)],
      ["Pin bar", cq.is_pin_bar ? "yes" : "no"], ["Outside bar", cq.is_outside_bar ? "yes" : "no"],
      ["Consecutive bull/bear", `${fmt(cq.consecutive_bullish)} / ${fmt(cq.consecutive_bearish)}`],
      ["Range vs avg", cq.volatility_state || "n/a"],
      ["Gap", cq.gap || "none"], ["Rejection", cq.rejection_at || "none"],
      ["False breakout", cq.false_breakout || "none"],
    ]);

    el("card-data-quality").innerHTML = this.kv([
      ["Score", `${fmt(signal.data_quality_score)} / 100`],
      ["Notes", (signal.data_quality_notes || []).length ? (signal.data_quality_notes || []).join("; ") : "none"],
    ]);
  },

  kv(pairs) {
    return pairs.map(([k, v]) => `<div class="card-kv"><span>${k}</span><span>${v}</span></div>`).join("");
  },

  async loadMultiTimeframe() {
    const { asset } = currentAssetTf();
    try {
      const data = await apiGet(`/api/signal/multi-timeframe?asset=${encodeURIComponent(asset)}`);
      const rows = Object.entries(data).map(([tf, d]) => {
        if (!d.available) return `<tr><td>${tf}</td><td colspan="4">No data uploaded</td></tr>`;
        return `<tr><td>${tf}</td><td>${d.trend.detail || ""}</td><td>${d.momentum.detail || ""}</td>` +
          `<td class="dir-${safeClass(d.signal)}">${d.signal}</td><td>${d.confidence}</td></tr>`;
      });
      el("mtf-body").innerHTML = rows.join("");
    } catch (e) { /* ignore */ }
  },

  async loadPaperAccount() {
    try {
      const d = await apiGet("/api/paper/account");
      el("paper-summary").innerHTML = [
        ["Balance", "$" + fmt(d.balance)],
        ["Daily P/L", `$${fmt(d.daily_pl)} (${fmt(d.daily_pl_pct)}%)`],
        ["Max Drawdown", "$" + fmt(d.max_drawdown)],
        ["Trades", d.total_trades], ["Wins", d.wins], ["Losses", d.losses],
        ["Win rate", d.win_rate != null ? (d.win_rate * 100).toFixed(1) + "%" : "n/a"],
        ["Consecutive losses", d.consecutive_losses],
        ["Current stake", "$" + fmt(d.current_stake)],
        ["Status", d.status],
      ].map(([k, v]) => `<div class="panel"><div class="card-kv"><span>${k}</span><span>${v}</span></div></div>`).join("");
    } catch (e) { /* ignore */ }
  },

  async loadPaperHistory() {
    try {
      const rows = await apiGet("/api/paper/history");
      el("paper-history-body").innerHTML = rows.slice().reverse().map((t) => `<tr>
        <td>${new Date(t.timestamp).toLocaleTimeString()}</td><td>${t.asset}</td><td>${t.timeframe}</td>
        <td>${t.signal}</td><td>${fmt(t.entry_price)}</td><td>${t.exit_price != null ? fmt(t.exit_price) : "-"}</td>
        <td>${t.status}</td><td>${fmt(t.profit)}</td><td>${t.balance_after != null ? fmt(t.balance_after) : "-"}</td>
        </tr>`).join("");
    } catch (e) { /* ignore */ }
  },

  async loadRiskStatus() {
    try {
      const d = await apiGet("/api/risk/status");
      const pill = el("risk-pill");
      const cls = safeClass(d.status);
      pill.className = "risk-pill risk-" + cls;
      pill.textContent = d.status;
      el("risk-detail").innerHTML = this.kv([
        ["Status", d.status], ["Reason", d.reason], ["Balance", "$" + fmt(d.balance)],
        ["Daily P/L", "$" + fmt(d.daily_pl)], ["Trades today", d.trades_today],
        ["Consecutive losses", d.consecutive_losses], ["Cooldown remaining", d.cooldown_remaining],
        ["Current stake", "$" + fmt(d.current_stake)],
      ]);
    } catch (e) { /* ignore */ }
  },

  async loadSignalHistory() {
    try {
      const rows = await apiGet("/api/signal/history?limit=50");
      el("history-body").innerHTML = rows.map((r) => `<tr>
        <td>${new Date(r.timestamp).toLocaleTimeString()}</td><td>${r.asset}</td><td>${r.timeframe}</td>
        <td class="dir-${safeClass(r.signal)}">${r.signal}</td><td>${r.confidence}</td><td>${r.score}</td><td>${r.result}</td>
        </tr>`).join("");
    } catch (e) { /* ignore */ }
  },

  async openPaperTrade() {
    const { asset, timeframe } = currentAssetTf();
    const entry_price = parseFloat(el("paper-entry-price").value);
    if (!entry_price) { alert("Enter an entry price."); return; }
    if (!confirm(`Confirm opening a PAPER (simulated) trade for ${asset} ${timeframe} at ${entry_price}? This never places a real trade.`)) return;
    try {
      const trade = await apiPost("/api/paper/open", { asset, timeframe, entry_price, confirm: true });
      if (trade.id) el("settle-trade-id").value = trade.id;
      await this.loadPaperAccount();
      await this.loadPaperHistory();
    } catch (e) { alert("Could not open paper trade: " + e.message); }
  },

  async settlePaperTrade() {
    const trade_id = el("settle-trade-id").value.trim();
    const exit_price = parseFloat(el("settle-exit-price").value);
    if (!trade_id || !exit_price) { alert("Provide trade ID and exit price."); return; }
    try {
      await apiPost("/api/paper/settle", { trade_id, exit_price });
      await this.loadPaperAccount();
      await this.loadPaperHistory();
      await this.loadRiskStatus();
    } catch (e) { alert("Could not settle trade: " + e.message); }
  },

  async resetPaper() {
    if (!confirm("Reset the entire paper trading session (balance and history)?")) return;
    try {
      await apiPost("/api/paper/reset", { confirm: true });
      await this.loadPaperAccount();
      await this.loadPaperHistory();
      await this.loadRiskStatus();
    } catch (e) { alert("Reset failed: " + e.message); }
  },

  async runBacktest() {
    const { asset, timeframe } = currentAssetTf();
    const payout_ratio = parseFloat(el("bt-payout").value) || 0.8;
    const min_score_threshold = parseFloat(el("bt-threshold").value);
    const walk_forward = el("bt-walkforward").value === "true";
    const n_windows = parseInt(el("bt-windows").value) || 4;
    el("bt-result").textContent = "Running...";
    try {
      const report = await apiPost("/api/backtest/run", {
        asset, timeframe, payout_ratio, min_score_threshold, walk_forward, n_windows,
      });
      el("bt-disclaimer").textContent = report.disclaimer || "";
      el("bt-result").textContent = JSON.stringify(report.combined_stats || report.stats, null, 2);
    } catch (e) {
      el("bt-result").textContent = "Error: " + e.message;
    }
  },
};

function fmt(v) {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "number") return v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return v;
}

document.addEventListener("DOMContentLoaded", () => {
  let auto = true;
  try { auto = localStorage.getItem("po.autoUpdate") !== "0"; } catch (e) { /* storage unavailable */ }
  el("auto-update").checked = auto;
  App.setAutoUpdate(auto);
  App.refreshAll();
});
