#!/usr/bin/env node
/*
  pending.js - manage pending food-inventory submissions.

  Stores pending submissions in pending.json beside food.db (same lookup as
  fooddb_sqlite.py: FOODDB_PATH, GT_DATA, the skill's grubtrack.json, then
  ~/.local/share/grubtrack).

  Why:
  - When a user submits a photo, we may be missing expiration dates, quantities, etc.
  - Create a pending entry, ask follow-up immediately, then finalize when user replies.

  No external deps.

  Usage:
    node pending.js init
    node pending.js create --chatId <chat id> --channel telegram --storage fridge --image images/x.jpg [--caption "fridge"]
    node pending.js list
    node pending.js get --id <pendingId>
    node pending.js resolve --id <pendingId> --answers '{"expirationDate":"2026-02-10"}'
    node pending.js close --id <pendingId>
*/

const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const os = require('os');

function dataRoot() {
  const home = os.homedir();
  const tilde = (p) => p.replace(/^~(?=$|\/)/, home);
  if (process.env.FOODDB_PATH) return path.dirname(tilde(process.env.FOODDB_PATH));
  if (process.env.GT_DATA) return tilde(process.env.GT_DATA);
  try {
    const pinned = JSON.parse(fs.readFileSync(path.join(__dirname, '..', 'grubtrack.json'), 'utf8'));
    if (pinned.db) return path.dirname(tilde(pinned.db));
  } catch {}
  return path.join(process.env.XDG_DATA_HOME || path.join(home, '.local', 'share'), 'grubtrack');
}

const ROOT = dataRoot();
const PENDING = path.join(ROOT, 'pending.json');

function die(msg, code = 1) {
  process.stderr.write(msg + '\n');
  process.exit(code);
}

function ensure() {
  fs.mkdirSync(ROOT, { recursive: true });
  if (!fs.existsSync(PENDING)) fs.writeFileSync(PENDING, JSON.stringify({ version: 1, items: [] }, null, 2));
}

function load() {
  ensure();
  return JSON.parse(fs.readFileSync(PENDING, 'utf8'));
}

function save(db) {
  fs.writeFileSync(PENDING, JSON.stringify(db, null, 2));
}

function nowIsoLocal() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, '0');
  const off = -d.getTimezoneOffset();
  const sign = off >= 0 ? '+' : '-';
  const hh = pad(Math.floor(Math.abs(off) / 60));
  const mm = pad(Math.abs(off) % 60);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}${sign}${hh}:${mm}`;
}

function makeId() {
  const ts = new Date().toISOString().replace(/[-:.TZ]/g, '').slice(0, 14);
  return `pend_${ts}_${crypto.randomBytes(3).toString('hex')}`;
}

function parseArgs(argv) {
  const out = { _: [] };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (!a.startsWith('--')) { out._.push(a); continue; }
    const k = a.slice(2);
    const v = argv[i + 1];
    if (v == null || v.startsWith('--')) out[k] = true;
    else { out[k] = v; i++; }
  }
  return out;
}

function cmdInit() {
  ensure();
  process.stdout.write('OK\n');
}

function cmdCreate(args) {
  const channel = args.channel;
  const chatId = args.chatId;
  if (!channel || !chatId) die('create requires --channel and --chatId');

  const db = load();
  const entry = {
    id: makeId(),
    createdAt: nowIsoLocal(),
    channel,
    chatId,
    storage: args.storage,
    caption: args.caption,
    image: args.image,
    extracted: args.extracted ? JSON.parse(args.extracted) : undefined,
    missing: args.missing ? JSON.parse(args.missing) : undefined,
    status: 'open',
    answers: {},
  };
  db.items.push(entry);
  save(db);
  process.stdout.write(entry.id + '\n');
}

function cmdList() {
  const db = load();
  process.stdout.write(JSON.stringify(db.items.filter(i => i.status === 'open').slice(-50).reverse(), null, 2) + '\n');
}

function cmdGet(args) {
  const id = args.id;
  if (!id) die('get requires --id');
  const db = load();
  const it = db.items.find(i => i.id === id);
  if (!it) die('not found', 2);
  process.stdout.write(JSON.stringify(it, null, 2) + '\n');
}

function cmdResolve(args) {
  const id = args.id;
  if (!id) die('resolve requires --id');
  const answers = args.answers ? JSON.parse(args.answers) : {};
  const db = load();
  const it = db.items.find(i => i.id === id);
  if (!it) die('not found', 2);
  it.answers = { ...(it.answers || {}), ...answers };
  it.resolvedAt = nowIsoLocal();
  save(db);
  process.stdout.write('OK\n');
}

function cmdClose(args) {
  const id = args.id;
  if (!id) die('close requires --id');
  const db = load();
  const it = db.items.find(i => i.id === id);
  if (!it) die('not found', 2);
  it.status = 'closed';
  it.closedAt = nowIsoLocal();
  save(db);
  process.stdout.write('OK\n');
}

function main() {
  const args = parseArgs(process.argv);
  const cmd = args._[0];
  if (!cmd) die('Usage: pending.js <init|create|list|get|resolve|close>');
  if (cmd === 'init') return cmdInit();
  if (cmd === 'create') return cmdCreate(args);
  if (cmd === 'list') return cmdList();
  if (cmd === 'get') return cmdGet(args);
  if (cmd === 'resolve') return cmdResolve(args);
  if (cmd === 'close') return cmdClose(args);
  die('Unknown command: ' + cmd);
}

main();
