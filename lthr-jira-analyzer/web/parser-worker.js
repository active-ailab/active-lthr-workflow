const FIT_EPOCH = 631065600;
const CSV_HEADER = [
  'TIMESTAMP', 'DATE_TIME', 'timeZoneOffset', 'PPG1', 'PPG2', 'ACC_X', 'ACC_Y', 'ACC_Z',
  'ppgLength', 'accLength', 'Longitude', 'Latitude', 'Altitude', 'gpsSpeed', 'gpsAccuracy',
  'HR', 'SPEED', 'wristOff', 'speedSourceOut', 'speedOut', 'updateIndexRet',
  'StartInput.workoutType', 'StartInput.rtc', 'userInfo', 'EndInput.workoutDate',
  'EndOutput.pace0', 'EndOutput.pace1', 'EndOutput.hrEstorOut', 'ACTIVE_TIMESTAMP',
];

function readFit(buffer, filename) {
  const bytes = new Uint8Array(buffer);
  const view = new DataView(buffer);
  if (bytes.length < 12 || String.fromCharCode(...bytes.slice(8, 12)) !== '.FIT') throw new Error(`${filename}: FIT 文件头无效。`);
  const headerSize = bytes[0];
  const dataSize = view.getUint32(4, true);
  const end = Math.min(headerSize + dataSize, bytes.length);
  if (headerSize < 12 || end <= headerSize) throw new Error(`${filename}: FIT 文件长度无效。`);

  const definitions = new Map();
  const records = [];
  const sessions = [];
  const timerEvents = [];
  let activity = null;
  let lastTimestamp = null;
  let offset = headerSize;

  const getNumber = (start, size, baseType, littleEndian) => {
    const type = baseType & 0x1f;
    if (start + size > end || size === 0) return null;
    try {
      let value;
      if (type === 0x00 || type === 0x02 || type === 0x0a || type === 0x0d) value = view.getUint8(start);
      else if (type === 0x01) value = view.getInt8(start);
      else if (type === 0x03) value = view.getInt16(start, littleEndian);
      else if (type === 0x04 || type === 0x0b) value = view.getUint16(start, littleEndian);
      else if (type === 0x05) value = view.getInt32(start, littleEndian);
      else if (type === 0x06 || type === 0x0c) value = view.getUint32(start, littleEndian);
      else if (type === 0x08) value = view.getFloat32(start, littleEndian);
      else if (type === 0x09) value = view.getFloat64(start, littleEndian);
      else return null;
      if (!Number.isFinite(value)) return null;
      const sentinels = { 0x00: 0xff, 0x01: -0x80, 0x02: 0xff, 0x03: -0x8000, 0x04: 0xffff, 0x05: -0x80000000, 0x06: 0xffffffff, 0x0a: 0, 0x0b: 0xffff, 0x0c: 0 };
      return sentinels[type] === value ? null : value;
    } catch {
      return null;
    }
  };

  while (offset < end) {
    const header = bytes[offset++];
    const compressed = (header & 0x80) !== 0;
    const isDefinition = !compressed && (header & 0x40) !== 0;
    const localNumber = compressed ? ((header >> 5) & 0x03) : (header & 0x0f);

    if (isDefinition) {
      const hasDeveloperFields = (header & 0x20) !== 0;
      if (offset + 5 > end) throw new Error(`${filename}: FIT 定义消息不完整。`);
      offset += 1;
      const littleEndian = bytes[offset++] === 0;
      const globalNumber = littleEndian ? view.getUint16(offset, true) : view.getUint16(offset, false);
      offset += 2;
      const fieldCount = bytes[offset++];
      const fields = [];
      const developerFields = [];
      for (let i = 0; i < fieldCount; i += 1) {
        if (offset + 3 > end) throw new Error(`${filename}: FIT 字段定义不完整。`);
        fields.push({ number: bytes[offset], size: bytes[offset + 1], baseType: bytes[offset + 2] });
        offset += 3;
      }
      if (hasDeveloperFields) {
        const developerCount = bytes[offset++];
        for (let i = 0; i < developerCount; i += 1) {
          developerFields.push({ size: bytes[offset + 1] });
          offset += 3;
        }
      }
      definitions.set(localNumber, { globalNumber, fields, developerFields, littleEndian });
      continue;
    }

    const definition = definitions.get(localNumber);
    if (!definition) throw new Error(`${filename}: 找不到 FIT 本地消息定义（offset=${offset - 1}, header=${header.toString(16)}, local=${localNumber}, known=${[...definitions.keys()].join(',')}）。`);
    const values = new Map();
    for (const field of definition.fields) {
      const value = getNumber(offset, field.size, field.baseType, definition.littleEndian);
      if (value !== null) values.set(field.number, value);
      offset += field.size;
    }
    for (const field of definition.developerFields) offset += field.size;
    if (compressed) {
      const timeOffset = header & 0x1f;
      if (lastTimestamp !== null) {
        let next = (lastTimestamp & ~0x1f) + timeOffset;
        if (timeOffset < (lastTimestamp & 0x1f)) next += 0x20;
        values.set(253, next);
      }
    }
    if (values.has(253)) lastTimestamp = values.get(253);

    const globalNumber = definition.globalNumber;
    if (globalNumber === 20) {
      const rawSpeed = values.has(73) ? values.get(73) : values.get(6);
      records.push({
        timestamp: values.has(253) ? values.get(253) + FIT_EPOCH : null,
        hr: values.has(3) ? values.get(3) : null,
        speed: rawSpeed === undefined ? null : rawSpeed / 1000 * 3.6,
      });
    } else if (globalNumber === 18) {
      sessions.push({
        start: values.has(2) ? values.get(2) + FIT_EPOCH : null,
        end: values.has(253) ? values.get(253) + FIT_EPOCH : null,
      });
    } else if (globalNumber === 34) {
      activity = {
        timestamp: values.has(253) ? values.get(253) + FIT_EPOCH : null,
        localTimestamp: values.has(5) ? values.get(5) + FIT_EPOCH : null,
      };
    } else if (globalNumber === 21 && values.get(0) === 0 && values.has(1) && values.has(253)) {
      timerEvents.push({ timestamp: values.get(253) + FIT_EPOCH, eventType: values.get(1) });
    }
  }

  const validRecords = records.filter((record) => Number.isFinite(record.timestamp));
  if (!validRecords.length) throw new Error(`${filename}: FIT 中没有运动记录。`);
  const timezoneSeconds = activity?.timestamp && activity?.localTimestamp
    ? activity.localTimestamp - activity.timestamp : 0;
  const normalizedSessions = sessions.length ? sessions : [{ start: validRecords[0].timestamp, end: validRecords.at(-1).timestamp }];
  const pauseIntervals = [];
  let pauseStart = null;
  for (const event of timerEvents.sort((a, b) => a.timestamp - b.timestamp)) {
    if ([1, 4, 5, 6].includes(event.eventType)) {
      if (pauseStart === null) pauseStart = event.timestamp;
    } else if (event.eventType === 0 && pauseStart !== null) {
      if (event.timestamp > pauseStart) pauseIntervals.push([pauseStart, event.timestamp]);
      pauseStart = null;
    }
  }
  return normalizedSessions.map((session, index) => {
    const start = session.start ?? validRecords[0].timestamp;
    const end = session.end ?? validRecords.at(-1).timestamp;
    const sessionRecords = validRecords.filter((record) => record.timestamp >= start && record.timestamp <= end);
    return {
      key: `${filename}#${index + 1}`,
      filename,
      index: index + 1,
      start,
      end,
      localStart: start + timezoneSeconds,
      timezoneMinutes: Math.round(timezoneSeconds / 60),
      records: sessionRecords,
      pauseIntervals: pauseIntervals.filter(([a, b]) => b > start && a < end),
    };
  }).filter((session) => session.records.length);
}

function lineClock(text, position) {
  const begin = text.lastIndexOf('\n', position - 1) + 1;
  let end = text.indexOf('\n', position);
  if (end < 0) end = text.length;
  const line = text.slice(begin, end);
  const match = line.match(/(?:I>|\b)(\d{1,2})\/(\d{1,2})\s+(\d{1,2}):(\d{2}):(\d{2})(?:\.(\d+))?/);
  if (!match) return null;
  return {
    month: Number(match[1]), day: Number(match[2]), hour: Number(match[3]),
    minute: Number(match[4]), second: Number(match[5]),
    millis: Number(`0.${match[6] || 0}`) * 1000,
  };
}

function secondsOfDay(clock) {
  return clock.hour * 3600 + clock.minute * 60 + clock.second + clock.millis / 1000;
}

function localDeltaSeconds(startClock, currentClock) {
  const start = Date.UTC(2000, startClock.month - 1, startClock.day, startClock.hour, startClock.minute, startClock.second, startClock.millis);
  let current = Date.UTC(2000, currentClock.month - 1, currentClock.day, currentClock.hour, currentClock.minute, currentClock.second, currentClock.millis);
  let delta = (current - start) / 1000;
  if (delta < -43200) delta += 86400;
  if (delta > 43200) delta -= 86400;
  return delta;
}

function findSportStarts(content) {
  const matches = [...content.matchAll(/sport\s+cmd\s*=\s*1\b|sport\s+start\s+time|(?:cmd|command)\s+START\b/gi)];
  return matches.map((match) => ({ position: match.index, clock: lineClock(content, match.index) })).filter((item) => item.clock);
}

function findLogSamples(name, content) {
  const events = [];
  function extractUserInfo(sourceText) {
    const oldUserInfo = sourceText.match(/_gomoreSdkConfig[^\r\n]*/i);
    const cUserInfo = sourceText.match(/ALG_GM[^\n]*\bc\s*([-+\d.eE, \t]+)/i);
    const userSource = oldUserInfo?.[0] || cUserInfo?.[1] || '';
    if (!userSource) return null;
    const values = userSource.match(/[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?/g)?.map(Number) || [];
    const looksDoubled = (values.length >= 3 && Math.abs(values[2]) > 250) || values.some((value) => Math.abs(value) > 300);
    if (oldUserInfo || looksDoubled) for (let i = 0; i < values.length; i += 1) values[i] /= 2;
    if (!values.length) return null;
    while (values.length < 7) values.push(-1);
    const normalized = values.slice(0, 7).map((value) => Number(value).toFixed(1));
    const profileValues = [0, 2, 3, 5].map((index) => Number(normalized[index]));
    return profileValues.some((value) => value > 0) ? normalized.join(',') : null;
  }
  const stateMatches = [...content.matchAll(/SPRT_GMS\s+rh\s*=\s*([+-]?[\d.]+)\s*,\s*sd\s*=\s*([+-]?[\d.]+)\s*,\s*state\s*=\s*(\d+)/gi)];
  const statusEvents = stateMatches.map((match) => ({ position: match.index, state: Number(match[3]), clock: lineClock(content, match.index) }));
  if (stateMatches.length) {
    stateMatches.filter((match) => Number(match[3]) === 2).forEach((match) => events.push({ position: match.index, hr: Number(match[1]), speed: Number(match[2]), clock: lineClock(content, match.index), state: 2 }));
  } else {
    [...content.matchAll(/_processGomoreAlgo/g)].forEach((match) => {
      const context = content.slice(Math.max(0, match.index - 200), Math.min(content.length, match.index + 500));
      const number = (regex) => {
        const found = context.match(regex);
        return found ? Number(found[1]) : -1;
      };
      events.push({ position: match.index, hr: number(/(?:hrRef|HR|hr|rh)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)/i), speed: number(/(?:speedRef|SPEED|sd)\s*[:=]?\s*([0-9]+(?:\.[0-9]+)?)/i), clock: lineClock(content, match.index) });
    });
  }

  const starts = [...content.matchAll(/GoMore\s+enable|enable.*Gomore|ALG_GM[^\n]*enable|GoMore\s+enabled|\benable\s+GoMore|_enableGomoreAlgo/gi)];
  const ends = [...content.matchAll(/_disableGomoreAlgo|disable.*Gomore/gi)];
  const ranges = [];
  let endIndex = 0;
  let previousEnd = -1;
  for (const start of starts) {
    if (start.index <= previousEnd) continue;
    while (endIndex < ends.length && ends[endIndex].index <= start.index) endIndex += 1;
    if (endIndex >= ends.length) break;
    const end = ends[endIndex++];
    previousEnd = end.index;
    const rows = events.filter((event) => event.position >= start.index && event.position < end.index);
    if (!rows.length) continue;
    const sportStart = findNearestSportStart(content, start.index);
    const outputs = [...content.slice(start.index, end.index).matchAll(/ALG_GM[^\n]*/gi)].map((match) => {
      const line = match[0];
      const get = (regex) => { const m = line.match(regex); return m ? Number(m[1]) : null; };
      return { ltSpeed: get(/ltSpeed\s*=\s*(-?[\d.]+)/i), lh: get(/\blh\s*=\s*(-?[\d.]+)/i), mh: get(/\bmh\s*=\s*(-?[\d.]+)/i), guideResult: get(/\bguideResult\s*=\s*(-?\d+)/i) };
    });
    const alg = outputs.filter((output) => Object.values(output).some((value) => value !== null)).at(-1) || null;
    const previousDisable = ends.filter((item) => item.index < start.index).at(-1);
    const metadataText = content.slice(previousDisable?.index || 0, end.index);
    const userInfo = extractUserInfo(metadataText);
    const sdkVersion = metadataText.match(/ALG_GM\s+SDK Version\s*:\s*([^\r\n]+)/i)?.[1]?.trim() || null;
    const startFitLtPaceMatch = metadataText.match(/ALG_GM[^\r\n]*\[GoMoRe\]\[startFit\]\[ltPace\]\s*=\s*(-?[\d.]+)/i);
    const startFitLtPace = startFitLtPaceMatch ? Number(startFitLtPaceMatch[1]) : null;
    const startFitLtHrMatch = metadataText.match(/ALG_GM[^\r\n]*\[GoMoRe\]\[startFit\]\[ltPace\]\s*=[^\r\n]*?\[ltHr\]\s*=\s*(-?[\d.]+)/i);
    const startFitLtHr = startFitLtHrMatch ? Number(startFitLtHrMatch[1]) : null;
    const numericValues = (line) => line?.match(/[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?/g)?.map(Number) || [];
    const valueCountLines = [...metadataText.matchAll(/\[GoMoRe\]\[fit\]\[valueCount\]\s*=\s*([^\r\n]+)/gi)];
    const valueCountValues = numericValues(valueCountLines.at(-1)?.[1]);
    const fitAppLines = [...metadataText.matchAll(/\[GoMoRe\]\[fitApp\]\s*=\s*([^\r\n]+)/gi)];
    const fitAppValues = numericValues(fitAppLines.at(-1)?.[1]);
    const vo2MaxHistory = [...metadataText.matchAll(/\[GoMoRe\]\[HisPd\]\[vo2max\]\s*=\s*([^\r\n]+)/gi)]
      .flatMap((match) => numericValues(match[1]));
    const maxHrHistory = [...metadataText.matchAll(/\[GoMoRe\]\[EHisPd\]\[maxHr\]\s*=\s*([^\r\n]+)/gi)]
      .flatMap((match) => numericValues(match[1]));
    const outputEvidence = {
      validPointCount: valueCountValues[0] ?? null,
      outputVo2Max: valueCountValues[1] ?? null,
      finalVo2Max: fitAppValues.at(-1) ?? null,
      vo2MaxHistory,
      maxHrHistory,
    };
    const typeContext = content.slice(start.index, Math.min(content.length, start.index + 500));
    const typeMatch = typeContext.match(/\bworkoutType\s*=\s*(\d+)/i) || typeContext.match(/\btype\s*=\s*(\d+)/i);
    const zoneContext = content.slice(Math.max(0, start.index - 200), Math.min(content.length, start.index + 700));
    const timezoneMatches = [...zoneContext.matchAll(/(?:timeZoneOffset|\btz)\s*[:=]\s*([+-]?\d+)/gi)];
    const timezoneMinutes = timezoneMatches.length ? Number(timezoneMatches.at(-1)[1]) : 0;
    ranges.push({ name, start: start.index, end: end.index, rows, statusEvents: statusEvents.filter((event) => event.position >= start.index && event.position < end.index), sportStart, alg, userInfo, userInfoSource: userInfo ? '日志样本关联范围（含前序上下文）' : null, sdkVersion, startFitLtPace, startFitLtHr, outputEvidence, workoutType: typeMatch ? Number(typeMatch[1]) : 31, timezoneMinutes });
  }

  if (ranges.length) return ranges;
  if (!events.length) return [];
  const userInfo = extractUserInfo(content);
  return [{ name, start: events[0].position, end: events.at(-1).position, rows: events, sportStart: findNearestSportStart(content, events[0].position), alg: null, userInfo, userInfoSource: userInfo ? '日志样本关联范围（含前序上下文）' : null, workoutType: 31, timezoneMinutes: 0 }];
}

function findNearestSportStart(content, position) {
  const starts = findSportStarts(content).filter((item) => item.position <= position);
  return starts.at(-1) || null;
}

function fitLocalClock(timestamp) {
  const date = new Date(timestamp * 1000);
  return { month: date.getUTCMonth() + 1, day: date.getUTCDate(), hour: date.getUTCHours(), minute: date.getUTCMinutes(), second: date.getUTCSeconds(), millis: 0 };
}

function startDifferenceSeconds(logClock, fitClock) {
  if (!logClock || logClock.month !== fitClock.month || logClock.day !== fitClock.day) return Infinity;
  return Math.abs(secondsOfDay(logClock) - secondsOfDay(fitClock));
}

function filenameTimestamp(timestamp, timezoneMinutes) {
  const date = new Date((timestamp + timezoneMinutes * 60) * 1000);
  const pad = (v) => String(v).padStart(2, '0');
  return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())}_${pad(date.getUTCHours())}${pad(date.getUTCMinutes())}${pad(date.getUTCSeconds())}`;
}

function formatLocalDateTime(timestamp, timezoneMinutes) {
  const date = new Date((timestamp + timezoneMinutes * 60) * 1000);
  const pad = (v) => String(v).padStart(2, '0');
  return `${date.getUTCFullYear()}${pad(date.getUTCMonth() + 1)}${pad(date.getUTCDate())} ${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}`;
}

function csvEscape(value) {
  const text = String(value ?? '');
  return /[",\r\n]/.test(text) ? `"${text.replaceAll('"', '""')}"` : text;
}

function makeCsv(sample, fitSession = null, mergedEvents = []) {
  let rows;
  let matchCount = 0;
  const pauseIntervals = mergeIntervals([
    ...(fitSession?.pauseIntervals || []).map(([start, end]) => [Math.max(start, fitSession.start), Math.min(end, fitSession.end)]),
    ...mergedEvents.flatMap((source) => logPauseIntervals(source.sample, fitSession)),
  ]);
  if (fitSession) {
    const logByTimestamp = new Map();
    for (const source of mergedEvents) {
      let lastTimestamp = null;
      for (const event of source.sample.rows) {
        if (!event.clock || !source.sample.sportStart?.clock) continue;
        const delta = localDeltaSeconds(fitLocalClock(fitSession.localStart), event.clock);
        let timestamp = Math.round(fitSession.start + delta);
        if (lastTimestamp !== null && timestamp <= lastTimestamp) timestamp = lastTimestamp + 1;
        lastTimestamp = timestamp;
        if (!logByTimestamp.has(timestamp)) logByTimestamp.set(timestamp, event);
      }
    }
    const activeFitRecords = fitSession.records.filter((record) => !isNonRunning(record.timestamp, pauseIntervals));
    const activeLogEntries = [...logByTimestamp].filter(([timestamp]) => !isNonRunning(timestamp, pauseIntervals));
    const matchedTimes = new Set();
    rows = activeFitRecords.map((record) => {
      const event = logByTimestamp.get(record.timestamp);
      if (event) matchedTimes.add(record.timestamp);
      const hr = record.hr ?? event?.hr;
      const speed = record.speed ?? event?.speed;
      return { timestamp: record.timestamp, row: csvRow(record.timestamp, fitSession.timezoneMinutes, hr, speed, sample.workoutType, sample.userInfo, activeTimestamp(record.timestamp, pauseIntervals)) };
    }).map(({ row }) => row);
    matchCount = matchedTimes.size;
    const fitTimes = new Set(activeFitRecords.map((record) => record.timestamp));
    for (const [timestamp, event] of activeLogEntries) {
      if (fitTimes.has(timestamp)) continue;
      if (!isNonRunning(timestamp, pauseIntervals)) rows.push(csvRow(timestamp, fitSession.timezoneMinutes, event.hr, event.speed, sample.workoutType, sample.userInfo, activeTimestamp(timestamp, pauseIntervals)));
    }
    rows.sort((a, b) => Number(a[0]) - Number(b[0]));
    return {
      rows, matchCount, logCount: activeLogEntries.length,
      logOnlyCount: activeLogEntries.length - matchCount,
      fitOnlyCount: activeFitRecords.length - matchCount,
      statusFilteredCount: fitSession.records.length - activeFitRecords.length + logByTimestamp.size - activeLogEntries.length,
    };
  }

  const dateMatch = sample.name.match(/(20\d{2})(\d{2})(\d{2})/);
  const baseDate = dateMatch ? Date.UTC(Number(dateMatch[1]), Number(dateMatch[2]) - 1, Number(dateMatch[3])) / 1000 : Math.floor(Date.now() / 1000);
  const logIntervals = mergeIntervals([...pauseIntervals, ...logPauseIntervals(sample)]);
  rows = sample.rows.map((event, index) => {
    const timestamp = timestampForLogClock(sample, event.clock) || baseDate + index - (sample.timezoneMinutes || 0) *  60;
    return { event, timestamp };
  }).filter(({ timestamp }) => !isNonRunning(timestamp, logIntervals)).map(({ event, timestamp }) =>
    csvRow(timestamp, sample.timezoneMinutes || 0, event.hr, event.speed, sample.workoutType, sample.userInfo, activeTimestamp(timestamp, logIntervals)));
  return { rows, matchCount: rows.length, logCount: rows.length, logOnlyCount: 0, fitOnlyCount: 0, statusFilteredCount: sample.rows.length - rows.length };
}

function calculateEstimate(sample, rows) {
  const userValues = (sample.userInfo || '').split(',').map(Number);
  const age = userValues[0];
  const restHr = userValues[5];
  const maxHrHistory = (sample.outputEvidence?.maxHrHistory || []).filter((value) => value !== -1);
  const useMax = maxHrHistory.length >= 2
    ? maxHrHistory.at(-2)
    : Number.isFinite(age) && age > 0 ? 220 - age : null;
  const hrLine = Number.isFinite(useMax) && Number.isFinite(restHr) && restHr > 0
    ? (useMax - restHr) * 0.7 + restHr
    : null;
  let hrPassCount = null;
  let speedPassCount = null;
  let estimatePoints = null;
  if (Number.isFinite(hrLine)) {
    hrPassCount = 0;
    speedPassCount = 0;
    estimatePoints = 0;
    rows.forEach((row) => {
      const hr = Number(row[15]);
      const speedKmh = Number(row[16]);
      const hrPass = hr > 0 && hr < 255 && hr > hrLine;
      const speedPass = speedKmh >= 0 && speedKmh > 6;
      if (hrPass) hrPassCount += 1;
      if (speedPass) speedPassCount += 1;
      if (hrPass && speedPass) estimatePoints += 1;
    });
  }
  return { useMax, hrLine, hrPassCount, speedPassCount, estimatePoints, meetsTimeCondition: estimatePoints !== null ? estimatePoints > 720 : null };
}

function mergeIntervals(intervals) {
  const merged = [];
  for (const [start, end] of intervals.filter(([a, b]) => b > a).sort((a, b) => a[0] - b[0])) {
    const last = merged.at(-1);
    if (last && start <= last[1]) last[1] = Math.max(last[1], end);
    else merged.push([start, end]);
  }
  return merged;
}

function timestampForLogClock(sample, clock, fitSession = null) {
  if (!clock) return null;
  if (fitSession) return Math.round(fitSession.start + localDeltaSeconds(fitLocalClock(fitSession.localStart), clock));
  const dateMatch = sample.name.match(/(20\d{2})(\d{2})(\d{2})/);
  const startClock = sample.sportStart?.clock || sample.rows[0]?.clock;
  if (!startClock) return null;
  const baseDate = dateMatch
    ? [Number(dateMatch[1]), Number(dateMatch[2]) - 1, Number(dateMatch[3])]
    : [new Date().getUTCFullYear(), new Date().getUTCMonth(), new Date().getUTCDate()];
  const localStart = Date.UTC(...baseDate, startClock.hour, startClock.minute, startClock.second);
  return Math.round(localStart / 1000 + localDeltaSeconds(startClock, clock) - (sample.timezoneMinutes || 0) * 60);
}

function logPauseIntervals(sample, fitSession = null) {
  const events = (sample.statusEvents || []).map((event) => ({ ...event, timestamp: timestampForLogClock(sample, event.clock, fitSession) }))
    .filter((event) => Number.isFinite(event.timestamp)).sort((a, b) => a.timestamp - b.timestamp);
  const intervals = [];
  let pauseStart = null;
  for (const event of events) {
    if (event.state !== 2) {
      if (pauseStart === null) pauseStart = event.timestamp;
    } else if (pauseStart !== null) {
      if (event.timestamp > pauseStart) intervals.push([pauseStart, event.timestamp]);
      pauseStart = null;
    }
  }
  return intervals;
}

function isNonRunning(timestamp, intervals) {
  return intervals.some(([start, end]) => timestamp >= start && timestamp < end);
}

function activeTimestamp(timestamp, intervals) {
  return Math.round(timestamp - intervals.reduce((sum, [start, end]) => sum + Math.max(0, Math.min(timestamp, end) - start), 0));
}

function normalizeHr(value) {
  if (value === null || value === undefined || value === '') return 255;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 && number < 255 ? Math.round(number) : 255;
}

function normalizeSpeed(value) {
  if (value === null || value === undefined || value === '') return 0;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? Number(number.toFixed(2)) : 0;
}

function csvRow(timestamp, timezone, hr, speedKmh, workoutType, userInfo, activeTs = timestamp) {
  const profile = userInfo || '-1.0,-1.0,-1.0,-1.0,-1.0,-1.0,-1.0';
  return [timestamp, formatLocalDateTime(timestamp, timezone), timezone, '-1.0', '-1.0', '-1.0', '-1.0', '-1.0', '0', '0', '-1.0', '-1.0', '-1.0', '-1.0', '-1.0', normalizeHr(hr), normalizeSpeed(speedKmh).toFixed(2), '0', '-1.0', '-1.0', '0', workoutType ?? -1, timestamp, profile, '0', '-1.00', '-1.00', '-1.00', activeTs];
}

function analyze({ logs, fits }) {
  const logSamples = [];
  for (const log of logs) {
    const text = new TextDecoder('utf-8', { fatal: false }).decode(log.buffer);
    logSamples.push(...findLogSamples(log.name, text));
  }
  const fitSessions = [];
  for (const fit of fits) fitSessions.push(...readFit(fit.buffer, fit.name));
  if (!logSamples.length && !fitSessions.length) throw new Error('请至少提供一份可解析的日志或 FIT 文件。');
  const results = [];
  const issues = [];
  const usedLogs = new Set();

  if (fitSessions.length) {
    fitSessions.sort((a, b) => a.start - b.start);
    for (const session of fitSessions) {
      const fitClock = fitLocalClock(session.localStart);
      const candidates = logSamples.filter((sample) => !usedLogs.has(sample))
        .map((sample) => ({ sample, delta: startDifferenceSeconds(sample.sportStart?.clock, fitClock) }))
      .filter((entry) => entry.delta <= 30)
        .sort((a, b) => a.delta - b.delta);
      const bestDelta = candidates[0]?.delta;
      const matching = candidates.filter((entry) => entry.delta - bestDelta <= 1);
      if (!matching.length) {
        const sample = { name: session.filename, userInfo: null, alg: null, sdkVersion: null, startFitLtPace: null, startFitLtHr: null, outputEvidence: {}, workoutType: -1, timezoneMinutes: session.timezoneMinutes };
        const merged = makeCsv(sample, session, []);
        const csv = [CSV_HEADER, ...merged.rows].map((row) => row.map(csvEscape).join(',')).join('\r\n') + '\r\n';
        results.push({ userInfo: null, userInfoSource: null, profileMissing: true, alg: null, filename: `gomore_data_${filenameTimestamp(session.start, session.timezoneMinutes)}.csv`, csv, preview: [CSV_HEADER, ...merged.rows.slice(0, 10)], rowCount: merged.rows.length, sources: [session.filename], matchRate: null, fitOnlyCount: merged.fitOnlyCount, statusFilteredCount: merged.statusFilteredCount, startTime: formatLocalDateTime(session.start, session.timezoneMinutes) });
        continue;
      }
      matching.forEach((entry) => usedLogs.add(entry.sample));
      const primary = matching.find((entry) => entry.sample.userInfo)?.sample || matching[0].sample;
      const merged = makeCsv(primary, session, matching.map((entry) => ({ sample: entry.sample })));
      const estimate = calculateEstimate(primary, merged.rows);
      const previewRows = [CSV_HEADER, ...merged.rows.slice(0, 10)];
      const csv = [CSV_HEADER, ...merged.rows].map((row) => row.map(csvEscape).join(',')).join('\r\n') + '\r\n';
      const percentage = merged.logCount ? Math.round(merged.matchCount / merged.logCount * 100) : 0;
      results.push({ userInfo: primary.userInfo, userInfoSource: primary.userInfoSource, profileMissing: !primary.userInfo, alg: primary.alg, sdkVersion: primary.sdkVersion, startFitLtPace: primary.startFitLtPace, startFitLtHr: primary.startFitLtHr, outputEvidence: primary.outputEvidence, estimate, filename: `gomore_data_${filenameTimestamp(session.start, session.timezoneMinutes)}.csv`, csv, preview: previewRows, rowCount: merged.rows.length, sources: [session.filename, ...matching.map((entry) => entry.sample.name)], matchRate: percentage, fitOnlyCount: merged.fitOnlyCount, logOnlyCount: merged.logOnlyCount, statusFilteredCount: merged.statusFilteredCount, startTime: formatLocalDateTime(session.start, session.timezoneMinutes) });
    }
    logSamples.filter((sample) => !usedLogs.has(sample)).forEach((sample) => {
      const output = makeCsv(sample);
      const estimate = calculateEstimate(sample, output.rows);
      const csv = [CSV_HEADER, ...output.rows].map((row) => row.map(csvEscape).join(',')).join('\r\n') + '\r\n';
      results.push({ userInfo: sample.userInfo, userInfoSource: sample.userInfoSource, profileMissing: !sample.userInfo, alg: sample.alg, sdkVersion: sample.sdkVersion, startFitLtPace: sample.startFitLtPace, startFitLtHr: sample.startFitLtHr, outputEvidence: sample.outputEvidence, estimate, filename: `gomore_data_${filenameTimestamp(output.rows[0][0], sample.timezoneMinutes || 0)}.csv`, csv, preview: [CSV_HEADER, ...output.rows.slice(0, 10)], rowCount: output.rows.length, sources: [sample.name], matchRate: null, fitOnlyCount: 0, statusFilteredCount: output.statusFilteredCount, startTime: formatLocalDateTime(output.rows[0][0], sample.timezoneMinutes || 0) });
    });
  } else {
    for (const sample of logSamples) {
      const output = makeCsv(sample);
      const estimate = calculateEstimate(sample, output.rows);
      const csv = [CSV_HEADER, ...output.rows].map((row) => row.map(csvEscape).join(',')).join('\r\n') + '\r\n';
      results.push({ userInfo: sample.userInfo, userInfoSource: sample.userInfoSource, profileMissing: !sample.userInfo, alg: sample.alg, sdkVersion: sample.sdkVersion, startFitLtPace: sample.startFitLtPace, startFitLtHr: sample.startFitLtHr, outputEvidence: sample.outputEvidence, estimate, filename: `gomore_data_${filenameTimestamp(output.rows[0][0], sample.timezoneMinutes || 0)}.csv`, csv, preview: [CSV_HEADER, ...output.rows.slice(0, 10)], rowCount: output.rows.length, sources: [sample.name], matchRate: null, fitOnlyCount: 0, statusFilteredCount: output.statusFilteredCount, startTime: formatLocalDateTime(output.rows[0][0], sample.timezoneMinutes || 0) });
    }
  }
  return { results, issues, logCount: logs.length, fitCount: fits.length };
}

if (typeof WorkerGlobalScope !== 'undefined' && self instanceof WorkerGlobalScope) {
  self.onmessage = (event) => {
    try {
      self.postMessage(analyze(event.data));
    } catch (error) {
      self.postMessage({ error: error.message || '分析失败。' });
    }
  };
} else if (typeof window !== 'undefined') {
  window.LTHRAnalyzer = { analyze };
}
