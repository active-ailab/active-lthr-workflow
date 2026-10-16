const jiraHealthUrl = 'https://www.active-ai.cloud/lthr-jira-api/health';
const jiraHealthCheckKey = 'lthrJiraHealthChecked';
const jiraHealthRedirected = !sessionStorage.getItem(jiraHealthCheckKey)
  && location.hostname === 'active-ailab.github.io';
if (jiraHealthRedirected) {
  sessionStorage.setItem(jiraHealthCheckKey, '1');
  location.assign(jiraHealthUrl);
}

const selectedFiles = { log: [], fit: [] };
const acceptedExtensions = { log: ['.log', '.txt'], fit: ['.fit'] };
const uploadMessage = document.querySelector('.upload-message');
const analyzeButton = document.querySelector('#analyze-button') || document.querySelector('.primary-button');
const resultsSection = document.querySelector('#analysis-results');
const previewSection = document.querySelector('.result-preview');
const isLocalPage = ['localhost', '127.0.0.1'].includes(location.hostname) && location.port === '8000';
const jiraServiceBase = isLocalPage
  ? 'http://127.0.0.1:8080/api/v1'
  : 'https://www.active-ai.cloud/lthr-jira-api/api/v1';

function clearAnalysisResults() {
  resultsSection.hidden = true;
  previewSection.hidden = false;
}

function formatFileSize(size) {
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

function formatPace(speedKmh) {
  const speed = Number(speedKmh);
  if (!Number.isFinite(speed) || speed <= 0) return '-';
  const totalSeconds = Math.round(3600 / speed);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = String(totalSeconds % 60).padStart(2, '0');
  return `${minutes}′${seconds}″/km`;
}

function renderFileList(type) {
  const container = document.querySelector(`[data-file-list="${type}"]`);
  container.replaceChildren();
  selectedFiles[type].forEach((file, index) => {
    const item = document.createElement('div');
    item.className = 'selected-file';
    const details = document.createElement('div');
    details.className = 'selected-file-details';
    const name = document.createElement('span');
    name.className = 'selected-file-name';
    name.textContent = file.name;
    name.title = file.name;
    const size = document.createElement('span');
    size.className = 'selected-file-size';
    size.textContent = formatFileSize(file.size);
    details.append(name, size);
    const remove = document.createElement('button');
    remove.className = 'remove-file';
    remove.type = 'button';
    remove.setAttribute('aria-label', `移除文件 ${file.name}`);
    remove.textContent = '移除';
    remove.addEventListener('click', () => {
      selectedFiles[type].splice(index, 1);
      renderFileList(type);
      analyzeButton.disabled = selectedFiles.log.length === 0;
      clearAnalysisResults();
      uploadMessage.textContent = '文件已从本次选择中移除。';
    });
    item.append(details, remove);
    container.append(item);
  });
}

function addFiles(type, files) {
  let added = 0;
  for (const file of files) {
    const dot = file.name.lastIndexOf('.');
    const extension = dot >= 0 ? file.name.slice(dot).toLowerCase() : '';
    if (!acceptedExtensions[type].includes(extension)) {
      uploadMessage.textContent = type === 'log'
        ? '日志格式不支持，请选择 .log 或 .txt 文件。'
        : 'FIT 格式不支持，请选择 .fit 文件。';
      continue;
    }
    if (selectedFiles[type].some((item) => item.name === file.name && item.size === file.size)) continue;
    selectedFiles[type].push(file);
    added += 1;
  }
  renderFileList(type);
  analyzeButton.disabled = selectedFiles.log.length === 0;
  if (added) {
    clearAnalysisResults();
    uploadMessage.textContent = `已选择 ${selectedFiles.log.length} 份日志和 ${selectedFiles.fit.length} 份 FIT 文件。`;
  }
}

const jiraAttachmentForm = document.querySelector('#jira-attachment-form');
const jiraIssueInput = document.querySelector('#jira-issue-key');
const jiraAttachmentButton = document.querySelector('#jira-attachment-button');
const jiraLoginForm = document.querySelector('#jira-login-form');
const jiraLoginButton = document.querySelector('#jira-login-button');
const jiraLoginOpenButton = document.querySelector('#jira-login-open-button');
const jiraLoginDialog = document.querySelector('#jira-login-dialog');
const jiraLoginCloseButton = document.querySelector('#jira-login-close-button');
const jiraLoginStatus = document.querySelector('#jira-login-status');
const jiraServerUrlInput = document.querySelector('#jira-server-url');
const jiraUsernameInput = document.querySelector('#jira-username');
const jiraPasswordInput = document.querySelector('#jira-password');
const jiraRememberLoginInput = document.querySelector('#jira-remember-login');
const jiraAttachmentStatus = document.querySelector('#jira-attachment-status');
const jiraSessionStorageKey = 'lthrJiraSessionToken';
const jiraLoginInfoStorageKey = 'lthrJiraLoginInfo';
let jiraSessionToken = sessionStorage.getItem(jiraSessionStorageKey) || '';

try {
  const savedLoginInfo = JSON.parse(localStorage.getItem(jiraLoginInfoStorageKey) || '{}');
  if (savedLoginInfo.baseUrl) jiraServerUrlInput.value = savedLoginInfo.baseUrl;
  if (savedLoginInfo.username) jiraUsernameInput.value = savedLoginInfo.username;
  jiraRememberLoginInput.checked = Boolean(savedLoginInfo.baseUrl || savedLoginInfo.username);
} catch {
  localStorage.removeItem(jiraLoginInfoStorageKey);
}

function updateJiraLoginButton() {
  jiraLoginOpenButton.textContent = jiraSessionToken ? '退出 Jira' : '登录 Jira';
  jiraServerUrlInput.required = !jiraSessionToken;
  jiraUsernameInput.required = !jiraSessionToken;
  jiraPasswordInput.required = !jiraSessionToken;
}

jiraLoginOpenButton.addEventListener('click', async () => {
  if (!jiraSessionToken) {
    jiraLoginStatus.textContent = '';
    jiraLoginDialog.showModal();
    jiraServerUrlInput.focus();
    return;
  }
  try {
    await fetch(`${jiraServiceBase}/auth/logout`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${jiraSessionToken}` },
    });
    jiraAttachmentStatus.textContent = '已退出 Jira。';
  } catch {
    jiraAttachmentStatus.textContent = '服务端未确认退出；本页已清除 Jira 登录会话。';
  } finally {
    jiraSessionToken = '';
    sessionStorage.removeItem(jiraSessionStorageKey);
    updateJiraLoginButton();
  }
});

jiraLoginCloseButton.addEventListener('click', () => jiraLoginDialog.close());

async function validateJiraSession() {
  if (!jiraSessionToken) return;
  try {
    const response = await fetch(`${jiraServiceBase}/auth/status`, {
      headers: { Authorization: `Bearer ${jiraSessionToken}` },
    });
    const status = await response.json();
    if (!response.ok || !status.authenticated) {
      jiraSessionToken = '';
      sessionStorage.removeItem(jiraSessionStorageKey);
    }
  } catch {
    jiraAttachmentStatus.textContent = '无法确认 Jira 登录状态；检查服务端连接后可重新登录。';
  }
  updateJiraLoginButton();
}

jiraLoginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  jiraLoginButton.disabled = true;
  jiraLoginStatus.textContent = '正在登录 Jira…';
  try {
    const response = await fetch(`${jiraServiceBase}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        baseUrl: jiraServerUrlInput.value.trim(),
        username: jiraUsernameInput.value.trim(),
        password: jiraPasswordInput.value,
      }),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.message || `登录失败（${response.status}）`);

    jiraSessionToken = result.sessionToken;
    sessionStorage.setItem(jiraSessionStorageKey, jiraSessionToken);
    if (jiraRememberLoginInput.checked) {
      localStorage.setItem(jiraLoginInfoStorageKey, JSON.stringify({
        baseUrl: jiraServerUrlInput.value.trim(),
        username: jiraUsernameInput.value.trim(),
      }));
    } else {
      localStorage.removeItem(jiraLoginInfoStorageKey);
    }
    jiraPasswordInput.value = '';
    updateJiraLoginButton();
    jiraLoginDialog.close();
    jiraAttachmentStatus.textContent = 'Jira 登录成功，可以输入 Jira 单号查找附件。';
  } catch (error) {
    jiraLoginStatus.textContent = error instanceof TypeError
      ? `无法连接 Jira 附件服务（${jiraServiceBase}）。`
      : error.message;
  } finally {
    jiraPasswordInput.value = '';
    jiraLoginButton.disabled = false;
  }
});

updateJiraLoginButton();
if (!jiraHealthRedirected) validateJiraSession();

jiraAttachmentForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const issueKey = jiraIssueInput.value.trim().toUpperCase();
  if (!/^[A-Z][A-Z0-9_]*-\d+$/.test(issueKey)) {
    jiraAttachmentStatus.textContent = '请输入正确的 Jira 单号。';
    return;
  }
  if (!jiraSessionToken) {
    jiraAttachmentStatus.textContent = '请先登录 Jira，再查找问题附件。';
    return;
  }

  jiraAttachmentButton.disabled = true;
  jiraAttachmentStatus.textContent = `正在查找 ${issueKey} 的附件…`;
  try {
    const response = await fetch(`${jiraServiceBase}/issues/${encodeURIComponent(issueKey)}/attachments`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${jiraSessionToken}` },
    });
    const manifest = await response.json();
    if (!response.ok) throw new Error(manifest.message || `请求失败（${response.status}）`);

    const files = await Promise.all((manifest.files || []).map(async (entry) => {
      const fileResponse = await fetch(`${jiraServiceBase}/sessions/${encodeURIComponent(manifest.sessionId)}/files/${encodeURIComponent(entry.fileId)}`, {
        headers: { Authorization: `Bearer ${jiraSessionToken}` },
      });
      if (!fileResponse.ok) throw new Error(`读取附件失败：${entry.name}`);
      const blob = await fileResponse.blob();
      return { type: entry.type, file: new File([blob], entry.name, { type: blob.type || 'application/octet-stream' }) };
    }));

    const logs = files.filter((entry) => entry.type === 'log').map((entry) => entry.file);
    const fits = files.filter((entry) => entry.type === 'fit').map((entry) => entry.file);
    const previousLogCount = selectedFiles.log.length;
    const previousFitCount = selectedFiles.fit.length;
    addFiles('log', logs);
    addFiles('fit', fits);
    const summary = `从 ${issueKey} 找到 ${logs.length} 份日志和 ${fits.length} 份 FIT 文件，新增 ${selectedFiles.log.length - previousLogCount} 份日志和 ${selectedFiles.fit.length - previousFitCount} 份 FIT。`;
    jiraAttachmentStatus.textContent = summary;
    uploadMessage.textContent = summary;
  } catch (error) {
    const message = error instanceof TypeError
      ? `无法连接 Jira 附件服务（${jiraServiceBase}），请确认服务已启动并允许当前网页跨域访问。`
      : error.message;
    jiraAttachmentStatus.textContent = message;
  } finally {
    jiraAttachmentButton.disabled = false;
  }
});

document.querySelectorAll('[data-upload]').forEach((area) => {
  const type = area.dataset.upload;
  const input = area.querySelector('input[type="file"]');
  area.addEventListener('click', () => input.click());
  input.addEventListener('click', (event) => event.stopPropagation());
  area.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      input.click();
    }
  });
  input.addEventListener('change', () => {
    addFiles(type, input.files);
    input.value = '';
  });
  area.addEventListener('dragover', (event) => {
    event.preventDefault();
    area.classList.add('dragging');
  });
  area.addEventListener('dragleave', () => area.classList.remove('dragging'));
  area.addEventListener('drop', (event) => {
    event.preventDefault();
    area.classList.remove('dragging');
    addFiles(type, event.dataTransfer.files);
  });
});

function renderResults(data) {
  resultsSection.replaceChildren();
  resultsSection.hidden = false;
  previewSection.hidden = true;

  const heading = document.createElement('div');
  heading.className = 'results-heading';
  const title = document.createElement('h2');
  title.textContent = '分析结果';
  heading.append(title);
  resultsSection.append(heading);

  data.results.forEach((result, index) => {
    const card = document.createElement('article');
    card.className = 'result-card';
    const cardHeader = document.createElement('div');
    cardHeader.className = 'result-card-header';
    const sampleTitle = document.createElement('h3');
    sampleTitle.textContent = `样本 ${index + 1}`;
    const filename = document.createElement('div');
    filename.className = 'result-filename';
    filename.textContent = result.filename || '';
    cardHeader.append(sampleTitle);

    if (!result.userInfo) {
      const missing = document.createElement('p');
      missing.className = 'analysis-warning';
      missing.textContent = '未检索到用户信息数据，无法分析';
      card.append(cardHeader, missing);
    } else {
      const values = result.userInfo.split(',').map(Number);
      const fields = [
        ['年龄', values[0] > 0 ? `${values[0]} 岁` : ''],
        ['性别', values[1] === 1 ? '男' : values[1] === 0 ? '女' : ''],
        ['身高', values[2] > 0 ? `${values[2]} cm` : ''],
        ['体重', values[3] > 0 ? `${values[3]} kg` : ''],
        ['静息心率', values[5] > 0 ? `${values[5]} bpm` : ''],
      ].filter(([, value]) => value);
      const userInfo = document.createElement('div');
      userInfo.className = 'sample-user-info';
      fields.forEach(([label, value]) => {
        const item = document.createElement('span');
        item.textContent = `${label}：${value}`;
        userInfo.append(item);
      });
      const download = document.createElement('a');
      download.className = 'download-button';
      download.textContent = '下载 CSV ↓';
      download.download = result.filename;
      download.href = URL.createObjectURL(new Blob([`\uFEFF${result.csv}`], { type: 'text/csv;charset=utf-8' }));
      const fileRow = document.createElement('div');
      fileRow.className = 'result-file-row';
      filename.textContent = result.filename;
      fileRow.append(filename, download);
      const summary = document.createElement('div');
      summary.className = 'gomore-summary';
      const summaryTitle = document.createElement('h4');
      summaryTitle.className = 'gomore-summary-title';
      summaryTitle.textContent = `GoMore 出值${result.sdkVersion ? ` · SDK ${result.sdkVersion}` : ''}`;
      summary.append(summaryTitle);
      [
        ['LT HR', result.alg?.lh],
        ['LT PACE', formatPace(result.alg?.ltSpeed)],
        ['MAX HR', result.alg?.mh],
        ['guideResult', result.alg?.guideResult],
      ].forEach(([label, value]) => {
        const item = document.createElement('div');
        item.className = 'gomore-summary-item';
        const name = document.createElement('span');
        name.textContent = label;
        const output = document.createElement('strong');
        output.textContent = value === null || value === undefined ? '-' : String(value);
        item.append(name, output);
        summary.append(item);
      });
      if ((result.startFitLtPace !== null && result.startFitLtPace !== undefined) || (result.startFitLtHr !== null && result.startFitLtHr !== undefined)) {
        const history = document.createElement('div');
        history.className = 'gomore-history';
        const historyTitle = document.createElement('h4');
        historyTitle.className = 'gomore-summary-title';
        historyTitle.textContent = '历史乳酸阈';
        const historyValues = document.createElement('div');
        historyValues.className = 'gomore-history-values';
        if (result.startFitLtHr !== null && result.startFitLtHr !== undefined) {
          const ltHr = document.createElement('div');
          ltHr.className = 'gomore-summary-item';
          ltHr.innerHTML = '<span>LT HR</span>';
          const hrValue = document.createElement('strong');
          hrValue.textContent = `${result.startFitLtHr} bpm`;
          ltHr.append(hrValue);
          historyValues.append(ltHr);
        }
        if (result.startFitLtPace !== null && result.startFitLtPace !== undefined) {
          const ltPace = document.createElement('div');
          ltPace.className = 'gomore-summary-item';
          ltPace.innerHTML = '<span>LT PACE</span>';
          const paceValue = document.createElement('strong');
          paceValue.textContent = formatPace(result.startFitLtPace);
          ltPace.append(paceValue);
          historyValues.append(ltPace);
        }
        history.append(historyTitle, historyValues);
        summary.append(history);
      }
      if (result.outputEvidence && Object.values(result.outputEvidence).some((value) => value !== null && value !== undefined)) {
        const evidence = document.createElement('div');
        evidence.className = 'gomore-history';
        const evidenceTitle = document.createElement('h4');
        evidenceTitle.className = 'gomore-summary-title';
        evidenceTitle.textContent = '输出依据';
        const evidenceValues = document.createElement('div');
        evidenceValues.className = 'gomore-history-values';
        [
          ['有效点数', result.outputEvidence.validPointCount],
          ['输出 Vo2Max', result.outputEvidence.outputVo2Max],
          ['最终 Vo2Max', result.outputEvidence.finalVo2Max],
        ].forEach(([label, value]) => {
          if (value === null || value === undefined) return;
          const item = document.createElement('div');
          item.className = 'gomore-summary-item';
          const name = document.createElement('span');
          name.textContent = label;
          const output = document.createElement('strong');
          output.textContent = String(value);
          item.append(name, output);
          evidenceValues.append(item);
        });
        evidence.append(evidenceTitle, evidenceValues);
        if (result.outputEvidence.vo2MaxHistory?.length) {
          const history = document.createElement('div');
          history.className = 'gomore-history vo2max-history';
          const historyTitle = document.createElement('h4');
          historyTitle.className = 'gomore-summary-title';
          historyTitle.textContent = '28天 Vo2Max';
          const historyValues = document.createElement('div');
          historyValues.className = 'vo2max-history-values';
          result.outputEvidence.vo2MaxHistory.forEach((value, index) => {
            const item = document.createElement('div');
            item.className = 'vo2max-history-item';
            const day = document.createElement('span');
            day.textContent = `D${String(index + 1).padStart(2, '0')}`;
            const number = document.createElement('strong');
            number.textContent = value < 0 ? '—' : String(value);
            item.append(day, number);
            historyValues.append(item);
          });
          history.append(historyTitle, historyValues);
          evidence.append(history);
        }
        summary.append(evidence);
      }
      card.append(cardHeader, userInfo, fileRow, summary);
      const estimate = document.createElement('section');
      estimate.className = 'estimate-card';
      const estimateTitle = document.createElement('h4');
      estimateTitle.textContent = '分析估值';
      const meetsCondition = result.estimate?.meetsTimeCondition;
      const estimateStatus = document.createElement('p');
      estimateStatus.className = `estimate-status ${meetsCondition === true ? 'meets' : meetsCondition === false ? 'does-not-meet' : 'undetermined'}`;
      estimateStatus.textContent = meetsCondition === true
        ? '符合出值条件'
        : meetsCondition === false ? '不符合出值条件' : '无法判断出值条件';
      const estimateValues = document.createElement('div');
      estimateValues.className = 'estimate-values';
      [
        ['UseMax', result.estimate?.useMax, 'bpm', '用于计算 Hr Line 的最大心率。存在两个或以上有效历史值时取倒数第二个；有效历史值少于两个时按 220 − 年龄估算。'],
        ['Hr Line', result.estimate?.hrLine, 'bpm', '公式：Hr Line = (UseMax − 静息心率) × 70% + 静息心率。'],
        ['估值点数', result.estimate?.estimatePoints, '点', '统计同时满足 HR > Hr Line 且速度 > 6 km/h 的数据点数。超过 720 点表示满足基础时间条件；该估值不等同于设备实际有效点数。'],
      ].forEach(([label, value, unit, explanation]) => {
        const item = document.createElement('div');
        item.className = 'estimate-item';
        const labelRow = document.createElement('div');
        labelRow.className = 'estimate-label-row';
        const name = document.createElement('span');
        name.textContent = label;
        const helpButton = document.createElement('button');
        helpButton.className = 'estimate-help-button';
        helpButton.type = 'button';
        helpButton.textContent = '?';
        helpButton.setAttribute('aria-label', `${label}的计算说明`);
        helpButton.setAttribute('aria-expanded', 'false');
        const helpText = document.createElement('p');
        helpText.className = 'estimate-help-text';
        helpText.textContent = explanation;
        helpText.hidden = true;
        helpButton.addEventListener('click', () => {
          helpText.hidden = !helpText.hidden;
          helpButton.setAttribute('aria-expanded', String(!helpText.hidden));
        });
        labelRow.append(name, helpButton);
        const output = document.createElement('strong');
        const displayedValue = label === '估值点数' ? String(value) : Number(value).toFixed(1);
        output.textContent = value === null || value === undefined ? '无法计算' : `${displayedValue} ${unit}`;
        item.append(labelRow, output, helpText);
        estimateValues.append(item);
      });
      const estimateNote = document.createElement('p');
      estimateNote.className = 'estimate-note';
      const timeCondition = result.estimate?.meetsTimeCondition;
      estimateNote.textContent = `按 HR > Hr Line、SPEED > 6 kph 统计；${timeCondition === null || timeCondition === undefined ? '无法判断是否满足' : timeCondition ? '估值点数超过' : '估值点数未超过'} 720 秒。该值为外部辅助参考，不是 SDK 实际有效点数。`;
      estimate.append(estimateTitle, estimateStatus, estimateValues, estimateNote);
      if (meetsCondition === false) {
        const unmet = document.createElement('ul');
        unmet.className = 'estimate-unmet';
        const hrDetail = document.createElement('li');
        hrDetail.textContent = `HR > Hr Line：${result.estimate.hrPassCount} 个点达标`;
        unmet.append(hrDetail);
        const speedDetail = document.createElement('li');
        speedDetail.textContent = `SPEED > 6 kph：${result.estimate.speedPassCount} 个点达标`;
        unmet.append(speedDetail);
        const simultaneous = document.createElement('li');
        simultaneous.textContent = `HR 与 SPEED 同时达标 ${result.estimate.estimatePoints} 秒，未超过 720 秒`;
        unmet.append(simultaneous);
        estimate.append(unmet);
      }
      card.append(estimate);
    }
    resultsSection.append(card);
  });
  uploadMessage.textContent = data.results.length ? '分析完成。' : '没有可展示的分析结果。';
  resultsSection.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function finishAnalysis(data) {
  analyzeButton.disabled = selectedFiles.log.length === 0;
  analyzeButton.innerHTML = '开始分析 <span>→</span>';
  if (data.error) {
    uploadMessage.textContent = data.error;
    return;
  }
  renderResults(data);
}

function failAnalysis() {
  analyzeButton.disabled = selectedFiles.log.length === 0;
  analyzeButton.innerHTML = '开始分析 <span>→</span>';
  uploadMessage.textContent = '分析失败，请检查文件格式后重试。';
}

analyzeButton.addEventListener('click', async () => {
  if (!selectedFiles.log.length) return;
  analyzeButton.disabled = true;
  analyzeButton.textContent = '正在分析…';
  uploadMessage.textContent = '正在浏览器内解析日志和 FIT 文件，请稍候。';
  resultsSection.hidden = true;
  previewSection.hidden = false;
  try {
    const logs = await Promise.all(selectedFiles.log.map(async (file) => ({ name: file.name, buffer: await file.arrayBuffer() })));
    const fits = await Promise.all(selectedFiles.fit.map(async (file) => ({ name: file.name, buffer: await file.arrayBuffer() })));
    if (location.protocol === 'file:') {
      setTimeout(() => {
        try {
          finishAnalysis(window.LTHRAnalyzer.analyze({ logs, fits }));
        } catch (error) {
          analyzeButton.disabled = selectedFiles.log.length === 0;
          analyzeButton.innerHTML = '开始分析 <span>→</span>';
          uploadMessage.textContent = `分析失败：${error.message}`;
        }
      }, 0);
      return;
    }
    const worker = new Worker('./lthr-jira-analyzer/web/parser-worker.js');
    worker.onmessage = (event) => {
      worker.terminate();
      finishAnalysis(event.data);
    };
    worker.onerror = () => {
      worker.terminate();
      try {
        finishAnalysis(window.LTHRAnalyzer.analyze({ logs, fits }));
      } catch (error) {
        failAnalysis();
      }
    };
    const transfer = [...logs, ...fits].map((file) => file.buffer);
    worker.postMessage({ logs, fits }, transfer);
  } catch (error) {
    analyzeButton.disabled = selectedFiles.log.length === 0;
    analyzeButton.innerHTML = '开始分析 <span>→</span>';
    uploadMessage.textContent = `读取文件失败：${error.message}`;
  }
});
