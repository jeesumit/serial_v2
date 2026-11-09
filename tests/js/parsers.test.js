// File-format parsers: every format, quoting, generic CSV, fuzzing, speed, time zones (incl. 30/45-minute offsets and DST).
const { test } = require('node:test'); const assert = require('node:assert/strict'); const fs = require('fs'), path = require('path'); const cp = require('child_process');
const ROOT = path.join(__dirname, '..', '..'); const html = fs.readFileSync(path.join(ROOT, 'static', 'index.html'), 'utf8');
const CODE = html.split('//PARSERS-START')[1].split('//PARSERS-END')[0];
const P = new Function(CODE + ';return{recsFromCSV,recsFromJSON,recsFromTXT,recsFromBIN,fmtTs,tsToT,parseCSV}')();
const S = path.join(ROOT, 'samples'), rd = f => fs.readFileSync(path.join(S, f), 'utf8'), ab = b => b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);

test('all four formats parse to identical records', () => {
  const R = { csv: P.recsFromCSV(rd('sample_log.csv')), json: P.recsFromJSON(rd('sample_log.json')), txt: P.recsFromTXT(rd('sample_log.txt')), bin: P.recsFromBIN(ab(fs.readFileSync(path.join(S, 'sample_log.bin')))) };
  for (const k of ['json', 'txt', 'bin']) { assert.equal(R[k].length, R.csv.length, k); R[k].forEach((r, i) => { assert.equal(r.text, R.csv[i].text); assert.equal(r.dir, R.csv[i].dir); assert.ok(Math.abs(r.t - R.csv[i].t) < 0.01, `${k}#${i}`) }) }
  assert.equal(R.csv.length, 455); assert.equal(R.csv.filter(r => r.dir === 'TX').length, 4);
});

test('CSV: quotes, commas, CRLF, embedded newlines, BOM-less odd input', () => {
  const r = P.recsFromCSV('seq,timestamp,delta_s,dir,data\r\n1,2026-11-08 10:00:00.100,0.0,RX,"a,b ""quoted"""\r\n2,2026-11-08 10:00:00.200,0.1,TX,plain\r\n3,2026-11-08 10:00:00.300,0.1,RX,"multi\nline"');
  assert.deepEqual(r.map(x => x.text), ['a,b "quoted"', 'plain', 'multi\nline']); assert.deepEqual(r.map(x => x.dir), ['RX', 'TX', 'RX']);
  assert.deepEqual(P.recsFromCSV(''), []); assert.deepEqual(P.recsFromCSV('\n\n\n'), []);
});

test('generic CSV: header names become channels; time column used when present', () => {
  const g = P.recsFromCSV('time,temp,hum,note\n0,20.5,40,ok\n0.5,21.0,41,fine\n1.0,21.4,,x\n'); assert.deepEqual(g.map(r => r.text), ['temp=20.5,hum=40', 'temp=21.0,hum=41', 'temp=21.4']); assert.deepEqual(g.map(r => r.t), [0, 0.5, 1]);
  const m = P.recsFromCSV('ms,v\n0,1\n500,2\n'); assert.deepEqual(m.map(r => r.t), [0, 0.5]); const n = P.recsFromCSV('a,b\n1,2\n3,4\n'); assert.deepEqual(n.map(r => r.t), [0, 1]);
});

test('JSON: our format, plain objects, wrapped arrays, bad input', () => {
  assert.equal(P.recsFromJSON('[{"seq":1,"t":5,"ts":"x","delta":0.1,"dir":"RX","text":"a=1"}]')[0].text, 'a=1');
  assert.equal(P.recsFromJSON('[{"temp":21.5,"name":"x"}]')[0].text, 'temp=21.5'); assert.equal(P.recsFromJSON('{"records":[{"text":"z"}]}')[0].text, 'z');
  assert.throws(() => P.recsFromJSON('{broken')); assert.deepEqual(P.recsFromJSON('[]'), []);
});

test('TXT and BIN edge cases', () => {
  assert.equal(P.recsFromTXT('#000001 2026-11-08 10:00:00.123 +0.080s RX  | SENSOR,1,T=22\nfree text line\n').length, 2);
  assert.equal(P.recsFromTXT('#000001 2026-11-08 10:00:00.123 +0.080s SYS | Connected\n')[0].dir, 'SYS');
  assert.throws(() => P.recsFromBIN(new ArrayBuffer(20)), /not a Serial Monitor/); assert.throws(() => P.recsFromBIN(new ArrayBuffer(0)));
  const trunc = Buffer.concat([Buffer.from([83, 76, 71, 49, 1]), Buffer.alloc(15, 255), Buffer.from('abc')]); assert.doesNotThrow(() => P.recsFromBIN(ab(trunc)));
});

test('fuzz: random input never hangs or throws unexpectedly', () => {
  let seed = 7; const rnd = () => (seed = (seed * 1103515245 + 12345) & 0x7fffffff) / 0x7fffffff;
  for (let i = 0; i < 300; i++) {
    const len = Math.floor(rnd() * 400), s = Array.from({ length: len }, () => String.fromCharCode(Math.floor(rnd() * 128))).join('');
    P.recsFromCSV(s); P.recsFromTXT(s); try { P.recsFromJSON(s) } catch (e) { assert.ok(e instanceof SyntaxError || e instanceof Error) }
    const b = Buffer.concat([Buffer.from([83, 76, 71, 49, 1]), Buffer.from(Array.from({ length: Math.floor(rnd() * 200) }, () => Math.floor(rnd() * 256)))]); P.recsFromBIN(ab(b));
  }
});

test('speed: 200,000-row CSV and 200,000-record BIN parse in a few seconds', () => {
  const rows = ['seq,timestamp,delta_s,dir,data']; for (let i = 0; i < 200000; i++) rows.push(`${i + 1},2026-11-08 10:00:00.000,0.01,RX,"T=${i % 40},H=50"`);
  let t0 = Date.now(); const r = P.recsFromCSV(rows.join('\n')); const dt = Date.now() - t0; assert.equal(r.length, 200000); assert.ok(dt < 15000, 'csv took ' + dt);
  const hdr = Buffer.from([83, 76, 71, 49, 1]); const rec = Buffer.alloc(15 + 10); rec.writeDoubleLE(1e9, 0); rec.writeUInt32LE(100, 8); rec.writeUInt8(0, 12); rec.writeUInt16LE(10, 13); rec.write('T=21.5,H=5', 15);
  const bin = Buffer.concat([hdr, ...Array(200000).fill(rec)]); t0 = Date.now(); assert.equal(P.recsFromBIN(ab(bin)).length, 200000); assert.ok(Date.now() - t0 < 15000);
});

test('time zones: local timestamp text round-trips (DST-fold hour excepted)', () => {
  const script = `const P=new Function(${JSON.stringify(CODE)}+';return{fmtTs,tsToT}')();let bad=0,fold=0,n=0;
    for(let t=1767225600;t<1767225600+365*86400;t+=1799.7){n++;const back=P.tsToT(P.fmtTs(t));if(Math.abs(back-t)<0.002)continue;
      const prev=P.fmtTs(t-3600)===P.fmtTs(t)||P.fmtTs(t-1800)===P.fmtTs(t)||P.fmtTs(t-2700)===P.fmtTs(t);prev?fold++:bad++}
    console.log(JSON.stringify({zone:Intl.DateTimeFormat().resolvedOptions().timeZone,n,bad,fold,off:new Date(1767225600e3).getTimezoneOffset()}))`;
  let honoured = 0;
  for (const tz of ['UTC', 'Asia/Kolkata', 'America/New_York', 'Europe/London', 'Australia/Lord_Howe', 'Pacific/Chatham', 'Asia/Kathmandu', 'America/St_Johns', 'Pacific/Kiritimati']) {
    const out = JSON.parse(cp.execFileSync(process.execPath, ['-e', script], { env: { ...process.env, TZ: tz }, encoding: 'utf8' }).trim());
    if (out.zone !== tz) continue;                       // platform ignores TZ env: nothing to verify for this zone
    honoured++; assert.equal(out.bad, 0, `${tz}: ${JSON.stringify(out)}`); assert.ok(out.n > 17000);
  }
  assert.ok(honoured >= 1);
});
