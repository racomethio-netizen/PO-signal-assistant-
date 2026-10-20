const API = "";

function el(id) { return document.getElementById(id); }
function safeClass(s) { return (s || "").replace(/\s+/g, "_"); }

async function apiPost(path, body) {
  const resp = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : resp.statusText);
  return data;
}

async function apiGet(path) {
  const resp = await fetch(API + path);
  const data = await resp.json().catch(() => ({}));
  if (!resp.ok) throw new Error(data.detail ? JSON.stringify(data.detail) : resp.statusText);
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
  async refreshAll() {
    await this.loadDataStatus();
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

document.addEventListener("DOMContentLoaded", () => App.refreshAll());
