#!/usr/bin/env node
const fs = require('node:fs');
const path = require('node:path');

global.window = {};
require('../web/parser-worker.js');

const [outDir, ...inputs] = process.argv.slice(2);
if (!outDir || inputs.length === 0) {
  process.stderr.write('Usage: analyze_lthr_sources.js OUT_DIR [log|fit:PATH ...]\n');
  process.exit(2);
}
const logs = [];
const fits = [];
for (const input of inputs) {
  const separator = input.indexOf(':');
  const kind = input.slice(0, separator);
  const filePath = input.slice(separator + 1);
  const buffer = Uint8Array.from(fs.readFileSync(filePath)).buffer;
  const file = { name: path.basename(filePath), buffer };
  if (kind === 'log') logs.push(file);
  else if (kind === 'fit') fits.push(file);
  else throw new Error(`Unknown source type: ${kind}`);
}
fs.mkdirSync(outDir, { recursive: true });
const result = window.LTHRAnalyzer.analyze({ logs, fits });
const output = result.results.map((sample, index) => {
  const filename = sample.filename || `gomore_data_${index + 1}.csv`;
  const filePath = path.join(outDir, filename);
  fs.writeFileSync(filePath, `\uFEFF${sample.csv}`, 'utf8');
  return {
    index: index + 1,
    csv: filePath,
    filename,
    rowCount: sample.rowCount,
    startTimestamp: sample.startTimestamp,
    sources: sample.sources,
    matchRate: sample.matchRate,
    fitOnlyCount: sample.fitOnlyCount,
    logOnlyCount: sample.logOnlyCount,
    statusFilteredCount: sample.statusFilteredCount,
    userInfo: sample.userInfo,
    userInfoSource: sample.userInfoSource,
    alg: sample.alg,
    sdkVersion: sample.sdkVersion,
    startTime: sample.startTime,
  };
});
process.stdout.write(JSON.stringify({ results: output, issues: result.issues }, null, 2));
