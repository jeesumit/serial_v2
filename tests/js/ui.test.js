// Drives the REAL static/index.html in jsdom with a fake backend: UI flows, hostile data, file opening, limits.
const { test } = require('node:test'); const assert = require('node:assert/strict');
const fs = require('fs'), path = require('path'); const { JSDOM, VirtualConsole } = require('jsdom');
const ROOT = path.join(__dirname, '..', '..'); const HTML = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');
const SAMPLES = path.join(ROOT, 'samples');
const J = (w, e) => JSON.parse(w.eval('JSON.stringify(' + e + ')'));   // cross-realm safe
const sleep = ms => new Promise(r => setTimeout(r, ms));

function boot(over = {}) {
  const calls = [], errors = [];
  const st = { connected: false, cfg: {}, bytes: 0, part: 0, logpath: '', logdir: 'C:\\Users\\me\\AppData\\Local\\SerialMonitor\\logs', warn: '', err: '', lost: 0, dropped: 0, sinkerr: '', ...over.status };
  const ports = over.ports || [{ dev: 'SIMULATOR', vid: '', pid: '', desc: 'built-in fake sensor', mfr: '', sn: '' }, { dev: 'COM3', vid: '0403', pid: '6001', desc: 'FT232R USB UART', mfr: 'FTDI', sn: 'A50285BI' }];
  const vc = new VirtualConsole(); vc.on('jsdomError', e => errors.push(String(e.message || e)));
  const ES = class { constructor(u) { this.u = u; this.closed = false; ES.all.push(this) } close() { this.closed = true } }; ES.all = [];
  const dom = new JSDOM(HTML, {
    runScripts: 'dangerously', pretendToBeVisual: true, url: 'http://127.0.0.1:47653/', virtualConsole: vc,
    beforeParse(w) {
      w.fetch = async (url, init = {}) => {
        const body = init.body ? JSON.parse(init.body) : null; calls.push({ url, method: init.method || 'GET', body, headers: init.headers });
        let r = over.handlers && over.handlers[url] ? over.handlers[url](body, st) : null;
        if (!r) {
          if (url.startsWith('/api/ports')) r = { json: ports };
          else if (url === '/api/status') r = { json: { ...st } };
          else if (url === '/api/connect') { Object.assign(st, { connected: true, cfg: { port: body.port, baud: body.baud, mode: body.mode, fmt: body.fmt, split: body.split, frame: '8N1' } }); r = { json: { ok: true } } }
          else if (url === '/api/disconnect') { st.connected = false; r = { json: { ok: true } } }
          else r = { json: { ok: true } };
        }
        return { status: r.status || 200, json: async () => r.json };
      };
      w.EventSource = ES;
      const ctx = new Proxy({}, { get: (t, k) => k === 'measureText' ? () => ({ width: 10 }) : () => { }, set: () => true });
      w.HTMLCanvasElement.prototype.getContext = () => ctx;
      Object.defineProperty(w.Element.prototype, 'clientWidth', { get() { return 900 } });
      Object.defineProperty(w.Element.prototype, 'clientHeight', { get() { return 300 } });
      w.print = () => calls.push({ print: true });
    },
  });
  const w = dom.window, q = s => w.document.querySelector(s), qa = s => [...w.document.querySelectorAll(s)];
  const live = () => ES.all.filter(e => !e.closed).pop();
  const rec = (seq, text, dir = 'RX', t = 1000 + seq * 0.1) => ({ seq, t, ts: '2026-11-08 10:00:00.000', delta: 0.1, dir, text });
  const push = recs => live().onmessage({ data: JSON.stringify({ r: recs }) });
  const settle = (ms = 120) => sleep(ms);
  const toasts = () => qa('#toast div').map(d => d.textContent);
  const setv = (sel, v, ev = 'change') => { const e = q(sel); e.value = v; e.dispatchEvent(new w.Event(ev, { bubbles: true })) };
  const file = (name, data) => ({ name, text: async () => data.toString('utf8'), arrayBuffer: async () => data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) });
  const sample = n => file(n, fs.readFileSync(path.join(SAMPLES, n)));
  return { w, dom, calls, errors, st, ES, q, qa, live, rec, push, settle, toasts, setv, file, sample, close: () => w.close() };
}
async function app(fn, over) { const a = boot(over); try { await a.settle(150); await fn(a); assert.deepEqual(a.errors, [], 'page script errors') } finally { a.close() } }
const T = (n, off = 0) => Array.from({ length: n }, (_, i) => ({ seq: off + i + 1, t: 1000 + (off + i) * 0.01, ts: '2026-11-08 10:00:00.000', delta: 0.01, dir: 'RX', text: `SENSOR,${off + i},T=${20 + (i % 50) / 10},H=${50 + i % 7},P=1004.${i % 9}` }));

test('boots cleanly, lists ports with VID/PID, never calls outside /api/', () => app(async a => {
  assert.equal(a.qa('#port option').length, 2); a.setv('#port', 'COM3');
  const info = a.q('#pinfo').textContent; assert.match(info, /0403/); assert.match(info, /6001/); assert.match(info, /FTDI/); assert.match(info, /COM3/);
  assert.ok(a.q('.badge img').getAttribute('src').startsWith('data:image/png;base64,'));
  assert.ok(a.calls.every(c => c.url.startsWith('/api/')), 'only same-origin API calls'); assert.ok(a.live(), 'stream opened');
}));

test('baud widget: stepper, wheel, presets, custom value, validation', () => app(async a => {
  const bn = () => a.q('#bn').textContent;
  assert.equal(bn(), '115200'); a.q('#bp').click(); assert.equal(bn(), '230400'); a.q('#bm').click(); a.q('#bm').click(); assert.equal(bn(), '57600');
  a.q('#bv').dispatchEvent(new a.w.WheelEvent('wheel', { deltaY: -100, cancelable: true })); assert.equal(bn(), '115200');
  a.q('#bv').dispatchEvent(new a.w.WheelEvent('wheel', { deltaY: 100, cancelable: true })); assert.equal(bn(), '57600');
  a.q('#bv').click(); assert.equal(a.q('#bpop').hidden, false);
  [...a.qa('#chips button')].find(b => b.textContent === '9600').click(); assert.equal(bn(), '9600'); assert.equal(a.q('#bpop').hidden, true);
  a.q('#bc').value = '250000'; a.q('#bok').click(); assert.equal(bn(), '250000'); a.q('#bp').click(); assert.equal(bn(), '460800');
  for (const bad of ['10', 'abc', '', '99999999', '-5']) { a.q('#bc').value = bad; a.q('#bok').click(); assert.equal(bn(), '460800', bad) }
  assert.ok(a.toasts().some(t => /50.*4,000,000/.test(t)));
  for (let i = 0; i < 30; i++) a.q('#bp').click(); assert.equal(bn(), '2000000'); for (let i = 0; i < 30; i++) a.q('#bm').click(); assert.equal(bn(), '300');
}));

test('connect sends the right payload, locks controls, disconnect unlocks', () => app(async a => {
  a.setv('#split', '5'); a.setv('#fmt', 'json'); a.setv('#dbits', '7'); a.setv('#par', 'E'); a.setv('#sb', '2'); a.setv('#mode', 'hex');
  const before = a.live(); a.q('#conn').click(); await a.settle(100);
  const c = a.calls.find(x => x.url === '/api/connect');
  assert.deepEqual(c.body, { port: 'SIMULATOR', baud: 115200, mode: 'hex', fmt: 'json', split: 5, bytesize: '7', parity: 'E', stopbits: '2' });
  assert.equal(c.headers['Content-Type'], 'application/json'); assert.notEqual(a.live(), before, 'stream reopened');
  await a.w.status(); for (const s of ['#port', '#bv', '#bm', '#bp', '#split', '#fmt', '#ldir', '#lset', '#mode']) assert.equal(a.q(s).disabled, true, s);
  assert.match(a.q('#conn').textContent, /Disconnect/); a.q('#conn').click(); await a.settle(80); await a.w.status();
  assert.equal(a.q('#port').disabled, false); assert.match(a.q('#conn').textContent, /Connect/);
}));

test('connect error is shown and the session is not started', () => app(async a => {
  const before = a.live(); a.st.connected = false;
  a.w.fetch = (f => async (u, i) => u === '/api/connect' ? { status: 400, json: async () => ({ error: 'baud must be a whole number from 50 to 4,000,000' }) } : f(u, i))(a.w.fetch);
  a.q('#conn').click(); await a.settle(100); assert.ok(a.toasts().some(t => /baud must be/.test(t))); assert.equal(a.live(), before);
}));

test('401/403 from the backend shows an access message once, no crash', () => app(async a => {
  a.w.fetch = async () => ({ status: 401, json: async () => ({ error: 'unauthorized' }) });
  await a.w.status(); await a.w.status(); await a.w.status();
  assert.equal(a.toasts().filter(t => /Access denied/.test(t)).length, 1);
}));

test('live data: table, plot channels, counters; only RX numbers are plotted', () => app(async a => {
  a.push([a.rec(1, 'Connected', 'SYS'), a.rec(2, 'SENSOR,1,T=21.5,H=50.1,P=1004.2'), a.rec(3, 'PING', 'TX'), a.rec(4, 'T=22,H=51,P=1005')]); await a.settle();
  assert.equal(a.qa('#tb tr').length, 4); assert.match(a.q('#cnt').textContent, /^4 records/);
  assert.deepEqual(J(a.w, 'Object.keys(ch)'), ['T', 'H', 'P']); assert.equal(a.w.eval('ch.T.t.length'), 2);
  assert.equal(a.qa('#leg label').length, 3); assert.equal(a.qa('#tb tr.SYS').length, 1);
  a.push([a.rec(5, '1.5,2.5,3')]); await a.settle(); assert.deepEqual(J(a.w, 'Object.keys(ch)').sort(), ['H', 'P', 'T', 'ch1', 'ch2', 'ch3']);
}));

test('hostile device data cannot break the plot or inject HTML', () => app(async a => {
  const evil = ['__proto__=1,constructor=2,toString=3,hasOwnProperty=4,valueOf=5', '<img src=x onerror="window.__pwned=1">=5', '<script>window.__pwned=1</script>',
    '"><svg onload=window.__pwned=1>', 'a=NaN,b=Infinity,c=1e999,d=-0,e=1e-999', 'x'.repeat(100000), 'k=' + '9'.repeat(400), '\u0000\u202e\ud800', '`${alert(1)}`'];
  a.push(evil.map((t, i) => a.rec(i + 1, t))); await a.settle(200);
  assert.equal(a.w.__pwned, undefined); assert.equal(a.qa('#tb img, #tb svg, #tb script, #leg img, #leg script, #leg svg').length, 0);
  assert.equal(a.qa('#tb tr')[2].cells[4].textContent, evil[2]); assert.equal(a.w.eval('Object.getPrototypeOf(ch)'), null);
  assert.ok(['__proto__', 'constructor', 'toString', 'hasOwnProperty', 'valueOf'].every(k => a.w.eval(`'${k}' in ch`)));
  a.push(T(50, 10)); await a.settle(); assert.equal(a.w.eval('ch.T.t.length') >= 50, true);   // plotting still works afterwards
}));

test('hostile port metadata is escaped in the info bar', () => app(async a => {
  const bad = '"><img src=x onerror=window.__pwned=1><b id=pwn>'; a.close();
  const b = boot({ ports: [{ dev: 'COM9', vid: '1234', pid: 'ABCD', desc: bad, mfr: bad, sn: bad }] });
  try { await b.settle(150); assert.equal(b.qa('#pinfo img, #pinfo #pwn').length, 0); assert.equal(b.w.__pwned, undefined); assert.ok(b.q('#pinfo').textContent.includes('<img')) } finally { b.close() }
}));

test('Print / Plot / Both: classes, no wasted work, rebuild from history', () => app(async a => {
  const mode = m => [...a.qa('#out button')].find(b => b.dataset.o === m).click();
  mode('plot'); assert.ok(a.w.document.body.classList.contains('o-plot')); a.push(T(5)); await a.settle();
  assert.equal(a.qa('#tb tr').length, 0); assert.equal(a.w.eval('Object.keys(ch).length'), 3);          // plot-only: no DOM rows
  mode('both'); assert.equal(a.qa('#tb tr').length, 5);                                                   // table rebuilt from history
  mode('print'); a.w.eval('clearPlot()'); a.push(T(5, 5)); await a.settle(); assert.equal(a.w.eval('Object.keys(ch).length'), 0);  // print-only: no plot work
  assert.equal(a.qa('#tb tr').length, 10); mode('both'); assert.equal(a.w.eval('ch.T.t.length'), 10);
}));

test('pause queues data, resume shows it, clear resets, filter, row click marks the plot', () => app(async a => {
  a.push(T(3)); await a.settle(); a.q('#pz').click(); a.push(T(4, 3)); await a.settle(); assert.equal(a.qa('#tb tr').length, 3);
  assert.match(a.q('#pz').textContent, /Resume`?/); a.q('#pz').click(); await a.settle(); assert.equal(a.qa('#tb tr').length, 7);
  a.setv('#flt', 'H=51', 'input'); const vis = a.qa('#tb tr').filter(r => !r.hidden).length; assert.ok(vis > 0 && vis < 7);
  a.setv('#flt', '', 'input'); assert.equal(a.qa('#tb tr').filter(r => !r.hidden).length, 7);
  a.qa('#tb tr')[2].click(); assert.ok(a.qa('#tb tr')[2].classList.contains('sel')); assert.equal(typeof a.w.eval('mark'), 'number');
  a.q('#clr').click(); assert.equal(a.qa('#tb tr').length, 0); assert.equal(a.w.eval('n'), 0); assert.equal(a.w.eval('Object.keys(ch).length'), 0);
}));

test('burst of 20,000 records stays bounded and fast; plot memory capped', () => app(async a => {
  const t0 = Date.now(); for (let i = 0; i < 4; i++) a.push(T(5000, i * 5000)); await a.settle(400);
  assert.ok(Date.now() - t0 < 8000); assert.ok(a.qa('#tb tr').length <= 1500); assert.equal(a.w.eval('n'), 20000);
  assert.match(a.q('#sk').textContent, /rows not drawn/); assert.equal(a.w.eval('ch.T.t.length'), 20000);
  for (let i = 4; i < 16; i++) a.push(T(5000, i * 5000)); await a.settle(900);
  const len = a.w.eval('ch.T.t.length'); assert.ok(len <= 75000 && len >= 50000, 'plot buffer capped, got ' + len);
  a.w.eval('sched()'); await a.settle(100);                                                                // decimated draw with 70k points
}));

test('open each saved format: identical data, banner, plot channels, close returns to live', () => app(async a => {
  for (const f of ['sample_log.csv', 'sample_log.json', 'sample_log.txt', 'sample_log.bin']) {
    const live1 = a.live(); await a.w.openFiles([a.sample(f)]); await a.settle(50);
    assert.equal(a.w.eval('fileMode'), true, f); assert.equal(a.q('#hb').style.display, 'flex'); assert.match(a.q('#ht').textContent, new RegExp(f));
    assert.equal(a.w.eval('FV.length'), 455, f); assert.equal(a.w.eval('ch.T.t.length'), 450, f); assert.equal(a.q('#pg').textContent, '1–455 of 455');
    assert.equal(a.qa('#tb tr').length, 455); assert.equal(live1 && live1.closed, true, 'live stream paused while viewing');
    a.q('#live').click(); await a.settle(50); assert.equal(a.w.eval('fileMode'), false); assert.equal(a.q('#hb').style.display, 'none'); assert.ok(a.live() && !a.live().closed);
  }
}));

test('opened file: paging, filter across all pages, print-only and plot-only modes', () => app(async a => {
  const rows = ['seq,timestamp,delta_s,dir,data']; for (let i = 0; i < 2500; i++) rows.push(`${i + 1},2026-11-08 10:00:${String(Math.floor(i / 10) % 60).padStart(2, '0')}.${String(i % 10).padStart(1, '0')}00,0.1,RX,"T=${i % 40},id=${i}"`);
  await a.w.openFiles([a.file('big.csv', Buffer.from(rows.join('\r\n')))]); await a.settle(100);
  assert.equal(a.q('#pg').textContent, '1–1,000 of 2,500'); assert.equal(a.qa('#tb tr').length, 1000);
  a.q('#pn').click(); assert.match(a.q('#pg').textContent, /1.?001–2.?000/); a.q('#pn').click(); assert.equal(a.qa('#tb tr').length, 500);
  a.q('#pn').click(); assert.equal(a.qa('#tb tr').length, 500); a.q('#pp').click(); a.q('#pp').click(); a.q('#pp').click(); assert.equal(a.qa('#tb tr')[0].cells[0].textContent, '1');
  a.setv('#flt', 'id=2499', 'input'); await a.settle(400); assert.equal(a.qa('#tb tr').length, 1); assert.match(a.q('#pg').textContent, /of 1$/);
  a.setv('#flt', '', 'input'); await a.settle(400);
  [...a.qa('#out button')].find(b => b.dataset.o === 'print').click(); [...a.qa('#out button')].find(b => b.dataset.o === 'plot').click(); assert.equal(a.w.eval('ch.T.t.length'), 2500);
}));

test('multi-file open merges split parts in time order', () => app(async a => {
  const parts = fs.readdirSync(path.join(SAMPLES, 'split_demo_1min')).sort().reverse().map(n => a.file(n, fs.readFileSync(path.join(SAMPLES, 'split_demo_1min', n))));
  await a.w.openFiles(parts); await a.settle(80);
  const t = J(a.w, 'FV.map(r=>r.t)'); assert.ok(t.every((x, i) => i === 0 || x >= t[i - 1]));
  const expect = fs.readdirSync(path.join(SAMPLES, 'split_demo_1min')).reduce((s, n) => s + fs.readFileSync(path.join(SAMPLES, 'split_demo_1min', n), 'utf8').trim().split('\n').length - 1, 0);
  assert.equal(a.w.eval('FV.length'), expect); assert.equal(expect, 904);
  assert.equal(a.w.eval('FV[0].seq'), 1); assert.equal(a.w.eval('FV[FV.length-1].seq'), a.w.eval('FV.length')); assert.match(a.q('#ht').textContent, /3 files/);
}));

test('bad / empty / hostile files give a message and leave the app usable', () => app(async a => {
  const live = a.live();
  for (const f of [a.file('x.bin', Buffer.from('not a log at all')), a.file('x.json', Buffer.from('{broken')), a.file('x.csv', Buffer.from('')), a.file('x.txt', Buffer.from('')),
    a.file('x.bin', Buffer.from([83, 76, 71, 49, 1, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255, 255])), a.file('e.csv', Buffer.from('<script>window.__pwned=1</script>,b\n"unterminated,1\n'))]) {
    await a.w.openFiles([f]); await a.settle(20);
  }
  assert.ok(a.toasts().length >= 3); assert.equal(a.w.__pwned, undefined); a.q('#live').click(); a.push(T(3)); await a.settle(); assert.equal(a.qa('#tb tr').length, 3); assert.ok(live);
}));

test('print button, beforeprint/afterprint restore theme, title line', () => app(async a => {
  a.q('#prt').click(); assert.ok(a.calls.some(c => c.print)); a.w.document.documentElement.dataset.theme = 'dark'; a.push(T(3)); await a.settle();
  a.w.dispatchEvent(new a.w.Event('beforeprint')); assert.equal(a.w.document.documentElement.dataset.theme, 'light'); assert.match(a.q('#ptl').textContent, /Serial Monitor.*printed/);
  a.w.dispatchEvent(new a.w.Event('afterprint')); assert.equal(a.w.document.documentElement.dataset.theme, 'dark');
}));

test('keyboard, history, quick commands, theme', () => app(async a => {
  a.q('#th').click(); assert.ok(a.w.document.documentElement.dataset.theme);
  a.w.document.body.dispatchEvent(new a.w.KeyboardEvent('keydown', { code: 'Space', bubbles: true })); assert.match(a.q('#pz').textContent, /Resume/);
  a.q('#msg').dispatchEvent(new a.w.KeyboardEvent('keydown', { code: 'Space', bubbles: true })); assert.match(a.q('#pz').textContent, /Resume/, 'space in input ignored');
  a.q('#pz').click(); a.st.connected = true;
  for (const t of ['one', 'two']) { a.q('#msg').value = t; a.q('#send').click(); await a.settle(30) }
  const key = k => a.q('#msg').dispatchEvent(new a.w.KeyboardEvent('keydown', { key: k })); key('ArrowUp'); assert.equal(a.q('#msg').value, 'two'); key('ArrowUp'); assert.equal(a.q('#msg').value, 'one'); key('ArrowDown'); assert.equal(a.q('#msg').value, 'two');
  a.q('#msg').value = 'PING'; a.q('#star').click(); assert.deepEqual(JSON.parse(a.w.localStorage.getItem('sm_q')), ['PING']);
  const chip = a.q('#qs button'); chip.click(); await a.settle(30); assert.ok(a.calls.filter(c => c.url === '/api/send').some(c => c.body.data === 'PING'));
  chip.dispatchEvent(new a.w.MouseEvent('contextmenu', { bubbles: true, cancelable: true })); assert.equal(a.qa('#qs button').length, 0);
}));

test('log folder: shown, saved, errors reported; warning surfaced once', () => app(async a => {
  assert.equal(a.q('#ldir').value, 'C:\\Users\\me\\AppData\\Local\\SerialMonitor\\logs');
  a.q('#ldir').value = 'D:\\Approved\\Logs'; a.q('#lset').click(); await a.settle(30); assert.deepEqual(a.calls.find(c => c.url === '/api/logdir').body, { path: 'D:\\Approved\\Logs' });
  a.w.fetch = async () => ({ status: 409, json: async () => ({ error: 'That folder is not writable' }) }); a.q('#lset').click(); await a.settle(30); assert.ok(a.toasts().some(t => /not writable/.test(t)));
}, { status: { warn: 'Data folder is not writable; using temp' } }).then(() => app(async a => { assert.ok(a.toasts().some(t => /using temp/.test(t))) }, { status: { warn: 'Data folder is not writable; using temp' } })));
