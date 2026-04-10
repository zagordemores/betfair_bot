/**
 * dashboard_api.js
 * Aggiunge endpoints di monitoring al proxy Express esistente.
 * Gira sulla porta 8091 (separata dal proxy 8090).
 *
 * Avvio: node dashboard_api.js
 * PM2:   pm2 start dashboard_api.js --name dashboard
 */

const express = require('express');
const fs      = require('fs');
const path    = require('path');
const { execSync, spawn } = require('child_process');

const app  = express();
const PORT = 8091;

const BOT_DIR    = '/home/opc/betfair_bot';
const BETS_FILE  = path.join(BOT_DIR, 'value_bets.json');
const HIST_FILE  = path.join(BOT_DIR, 'bets_history.csv');
const LOG_FILE   = path.join(BOT_DIR, 'bot.log');
const DASH_FILE  = path.join(BOT_DIR, 'dashboard.html');

// ── CORS ──────────────────────────────────────────────────────────────────
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET,POST,OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.sendStatus(200);
  next();
});

app.use(express.json());

// ── DASHBOARD HTML ────────────────────────────────────────────────────────
app.get('/', (req, res) => {
  if (fs.existsSync(DASH_FILE)) {
    res.sendFile(DASH_FILE);
  } else {
    res.status(404).send('Dashboard non trovata. Copia dashboard.html in ' + BOT_DIR);
  }
});

// ── VALUE BETS ────────────────────────────────────────────────────────────
app.get('/bets', (req, res) => {
  try {
    if (!fs.existsSync(BETS_FILE)) return res.json([]);
    const raw  = fs.readFileSync(BETS_FILE, 'utf8');
    const data = JSON.parse(raw);
    const bets = Array.isArray(data) ? data : (data.bets || []);
    res.json(bets);
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// ── STORICO SCOMMESSE (CSV → JSON) ───────────────────────────────────────
app.get('/history', (req, res) => {
  try {
    if (!fs.existsSync(HIST_FILE)) return res.json([]);
    const lines = fs.readFileSync(HIST_FILE, 'utf8').split('\n').filter(Boolean);
    if (lines.length < 2) return res.json([]);

    const headers = lines[0].split(',').map(h => h.trim());
    const rows = lines.slice(1).map(line => {
      const vals = line.split(',');
      const obj = {};
      headers.forEach((h, i) => {
        const v = vals[i] ? vals[i].trim() : '';
        // Converti numeri
        obj[h] = isNaN(v) || v === '' ? v : parseFloat(v);
      });
      return obj;
    });

    // Calcola P&L se non presente
    rows.forEach(r => {
      if (r.pnl === undefined && r.stake && r.status) {
        if (r.status === 'dry_run') r.pnl = null; // in attesa
      }
    });

    res.json(rows.reverse()); // più recenti prima
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// ── LOG (ultime 100 righe) ────────────────────────────────────────────────
app.get('/logs', (req, res) => {
  try {
    if (!fs.existsSync(LOG_FILE)) return res.send('');
    // Leggi ultime 100 righe efficientemente
    const content = fs.readFileSync(LOG_FILE, 'utf8');
    const lines   = content.split('\n').filter(Boolean);
    res.send(lines.slice(-100).join('\n'));
  } catch(e) {
    res.status(500).send('');
  }
});

// ── STATUS BOT ────────────────────────────────────────────────────────────
app.get('/status', (req, res) => {
  try {
    const running = execSync("pgrep -f bot_manager.py | wc -l").toString().trim();
    const isRunning = parseInt(running) > 0;

    // Leggi ultime righe del log per estrarre metriche
    let betsPlaced = 0;
    let liability  = 0;
    if (fs.existsSync(LOG_FILE)) {
      const content = fs.readFileSync(LOG_FILE, 'utf8');
      const dryRuns = (content.match(/\[DRY RUN\]/g) || []).length;
      const lives   = (content.match(/\[LIVE\] LAY piazzato/g) || []).length;
      betsPlaced = dryRuns + lives;
    }

    res.json({
      running: isRunning,
      mode: 'dry_run',  // leggi da config se vuoi dinamico
      bets_placed: betsPlaced,
      liability: liability,
      timestamp: new Date().toISOString(),
    });
  } catch(e) {
    res.json({ running: false, error: e.message });
  }
});

// ── AZIONI ───────────────────────────────────────────────────────────────
// POST /run/export → lancia export_value_bets.py
app.post('/run/export', (req, res) => {
  try {
    const proc = spawn('python3', ['export_value_bets.py', '--all-leagues'], {
      cwd: BOT_DIR,
      detached: true,
      stdio: 'ignore',
    });
    proc.unref();
    res.json({ ok: true, message: 'Export avviato' });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// POST /run/start → avvia bot in dry run
app.post('/run/start', (req, res) => {
  try {
    const proc = spawn('python3', ['bot_manager.py', '--dry-run'], {
      cwd: BOT_DIR,
      detached: true,
      stdio: ['ignore', fs.openSync(LOG_FILE, 'a'), fs.openSync(LOG_FILE, 'a')],
    });
    proc.unref();
    res.json({ ok: true, pid: proc.pid });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// POST /run/stop → ferma bot
app.post('/run/stop', (req, res) => {
  try {
    execSync("pkill -f bot_manager.py || true");
    res.json({ ok: true });
  } catch(e) {
    res.json({ ok: true });
  }
});

// GET /config → legge DRY_RUN dal config.py
app.get('/config', (req, res) => {
  try {
    const config = fs.readFileSync(path.join(BOT_DIR, 'config.py'), 'utf8');
    const match = config.match(/DRY_RUN\s*=\s*(True|False)/);
    const dryRun = match ? match[1] === 'True' : true;
    const bankrollMatch = config.match(/BANKROLL\s*=\s*([\d.]+)/);
    const bankroll = bankrollMatch ? parseFloat(bankrollMatch[1]) : 100;
    res.json({ dry_run: dryRun, bankroll });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// POST /run/toggle-mode → cambia DRY_RUN in config.py e riavvia il bot
app.post('/run/toggle-mode', (req, res) => {
  try {
    const { live } = req.body;
    const configPath = path.join(BOT_DIR, 'config.py');
    let config = fs.readFileSync(configPath, 'utf8');

    // Cambia DRY_RUN
    const newValue = live ? 'False' : 'True';
    config = config.replace(/DRY_RUN\s*=\s*(True|False)/, `DRY_RUN = ${newValue}`);
    fs.writeFileSync(configPath, config);

    // Ferma il bot
    try { execSync("pkill -f bot_manager.py || true"); } catch(e) {}

    // Riavvia con PM2
    setTimeout(() => {
      try { execSync("cd " + BOT_DIR + " && pm2 restart betfair-bot"); } catch(e) {}
    }, 2000);

    res.json({ ok: true, dry_run: !live, message: live ? 'Passato a LIVE' : 'Passato a DRY RUN' });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// ── METRICHE AGGREGATE ────────────────────────────────────────────────────
app.get('/metrics', (req, res) => {
  try {
    const bets = fs.existsSync(BETS_FILE)
      ? (() => { const d = JSON.parse(fs.readFileSync(BETS_FILE,'utf8')); return Array.isArray(d)?d:(d.bets||[]); })()
      : [];

    const byLeague = {};
    let totalEdge  = 0;
    bets.forEach(b => {
      byLeague[b.league] = (byLeague[b.league] || 0) + 1;
      totalEdge += (b.edge_pct || 0);
    });

    res.json({
      total_bets: bets.length,
      avg_edge: bets.length ? (totalEdge / bets.length).toFixed(1) : 0,
      by_league: byLeague,
      generated_at: new Date().toISOString(),
    });
  } catch(e) {
    res.status(500).json({ error: e.message });
  }
});

// ── START ─────────────────────────────────────────────────────────────────
app.listen(PORT, '0.0.0.0', () => {
  console.log(`Dashboard API in ascolto su http://0.0.0.0:${PORT}`);
  console.log(`Dashboard: http://92.4.217.252:${PORT}`);
  console.log(`Endpoints: /bets /history /logs /status /metrics`);
});
