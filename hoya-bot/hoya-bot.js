#!/usr/bin/env node
/**
 * 호야 텔레그램 비서 봇
 * - Claude API로 대화
 * - Google Calendar 조회/생성
 * - TaskFlow 일정 통합
 */
const TelegramBot = require('node-telegram-bot-api');
const Anthropic = require('@anthropic-ai/sdk');
const sqlite3 = require('sqlite3').verbose();
const { google } = require('googleapis');
const Imap = require('imap');
const { simpleParser } = require('mailparser');
const fs = require('fs');
const path = require('path');
const http = require('http');
const https = require('https');
const { URL } = require('url');

// ── 설정 로드 (환경변수 우선, 로컬 파일 폴백) ─────────────────────
let SECRETS, MAIL_CONFIG;

if (process.env.TELEGRAM_BOT_TOKEN) {
  // Railway 클라우드 모드: 환경변수에서 읽기
  SECRETS = {
    telegram_token: process.env.TELEGRAM_BOT_TOKEN,
    anthropic_key: process.env.ANTHROPIC_API_KEY,
    google_client_id: process.env.GOOGLE_CLIENT_ID,
    google_client_secret: process.env.GOOGLE_CLIENT_SECRET,
  };
  MAIL_CONFIG = {
    daum: {
      user: process.env.DAUM_IMAP_USER || '',
      password: process.env.DAUM_IMAP_PASSWORD || '',
      host: process.env.DAUM_IMAP_HOST || 'imap.daum.net',
      port: parseInt(process.env.DAUM_IMAP_PORT || '993', 10),
      tls: true,
    },
  };
} else {
  // 로컬 개발 모드: 파일에서 읽기
  MAIL_CONFIG = JSON.parse(fs.readFileSync(path.join(__dirname, 'mail-config.json'), 'utf8'));
  SECRETS = JSON.parse(fs.readFileSync(path.join(__dirname, '../secrets.json'), 'utf8'));
}

const TELEGRAM_TOKEN = SECRETS.telegram_token;
const ANTHROPIC_KEY = SECRETS.anthropic_key;

// Google OAuth 토큰 경로 (클라우드: 앱 디렉토리 내, 로컬: 상위 폴더)
const TOKEN_PATH = process.env.TELEGRAM_BOT_TOKEN
  ? path.join(__dirname, 'google-tokens.json')
  : path.join(__dirname, '../google-tokens.json');

// 클라우드 모드: GOOGLE_TOKENS_JSON 환경변수로 토큰 파일 초기화
if (process.env.GOOGLE_TOKENS_JSON && !fs.existsSync(TOKEN_PATH)) {
  fs.writeFileSync(TOKEN_PATH, process.env.GOOGLE_TOKENS_JSON);
}

const TASKFLOW_URL = 'https://taskflow-production-462b.up.railway.app';

// ── Google OAuth2 싱글턴 (토큰 자동 갱신 + 파일 저장) ────────────
let _oauth2Client = null;
function getOAuth2Client() {
  if (_oauth2Client) return _oauth2Client;
  const oauth2 = new google.auth.OAuth2(
    SECRETS.google_client_id,
    SECRETS.google_client_secret,
    'http://localhost:4200/oauth2callback'
  );
  if (fs.existsSync(TOKEN_PATH)) {
    oauth2.setCredentials(JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8')));
  }
  // 액세스 토큰 갱신 시 파일 업데이트 (클라우드 재시작 대비)
  oauth2.on('tokens', (newTokens) => {
    try {
      const existing = fs.existsSync(TOKEN_PATH)
        ? JSON.parse(fs.readFileSync(TOKEN_PATH, 'utf8')) : {};
      fs.writeFileSync(TOKEN_PATH, JSON.stringify({ ...existing, ...newTokens }, null, 2));
    } catch (_) {}
  });
  _oauth2Client = oauth2;
  return oauth2;
}

// ── Gmail ────────────────────────────────────────────────────────
function getGmailClient() {
  return google.gmail({ version: 'v1', auth: getOAuth2Client() });
}

async function getUnreadEmails(maxResults = 5) {
  const gmail = getGmailClient();
  const res = await gmail.users.messages.list({ userId: 'me', maxResults, q: 'is:unread' });
  const msgs = res.data.messages || [];
  if (!msgs.length) return '읽지 않은 메일이 없어요.';
  const details = await Promise.all(msgs.map(m =>
    gmail.users.messages.get({ userId: 'me', id: m.id, format: 'metadata', metadataHeaders: ['Subject', 'From', 'Date'] })
  ));
  return details.map(d => {
    const h = d.data.payload.headers;
    const subject = h.find(x => x.name === 'Subject')?.value || '(제목없음)';
    const from = h.find(x => x.name === 'From')?.value || '';
    const date = h.find(x => x.name === 'Date')?.value || '';
    return `📧 ${subject}\n   보낸사람: ${from}\n   날짜: ${date}`;
  }).join('\n\n');
}

async function searchEmails(query, maxResults = 5) {
  const gmail = getGmailClient();
  const res = await gmail.users.messages.list({ userId: 'me', maxResults, q: query });
  const msgs = res.data.messages || [];
  if (!msgs.length) return `"${query}" 검색 결과가 없어요.`;
  const details = await Promise.all(msgs.map(m =>
    gmail.users.messages.get({ userId: 'me', id: m.id, format: 'metadata', metadataHeaders: ['Subject', 'From', 'Date'] })
  ));
  return details.map(d => {
    const h = d.data.payload.headers;
    const subject = h.find(x => x.name === 'Subject')?.value || '(제목없음)';
    const from = h.find(x => x.name === 'From')?.value || '';
    return `📧 ${subject}\n   보낸사람: ${from}`;
  }).join('\n\n');
}

async function getEmailContent(query) {
  const gmail = getGmailClient();
  const res = await gmail.users.messages.list({ userId: 'me', maxResults: 1, q: query });
  const msgs = res.data.messages || [];
  if (!msgs.length) return '해당 메일을 찾을 수 없어요.';
  const detail = await gmail.users.messages.get({ userId: 'me', id: msgs[0].id, format: 'full' });
  const h = detail.data.payload.headers;
  const subject = h.find(x => x.name === 'Subject')?.value || '';
  const from = h.find(x => x.name === 'From')?.value || '';

  // 본문 추출
  let body = '';
  const extractBody = (parts) => {
    if (!parts) return;
    for (const p of parts) {
      if (p.mimeType === 'text/plain' && p.body?.data) {
        body = Buffer.from(p.body.data, 'base64').toString('utf8').substring(0, 1000);
        return;
      }
      if (p.parts) extractBody(p.parts);
    }
  };
  if (detail.data.payload.body?.data) {
    body = Buffer.from(detail.data.payload.body.data, 'base64').toString('utf8').substring(0, 1000);
  } else {
    extractBody(detail.data.payload.parts);
  }
  return `제목: ${subject}\n보낸사람: ${from}\n\n${body}`;
}

// ── Daum IMAP ────────────────────────────────────────────────────
function getDaumImap() {
  return new Imap({ ...MAIL_CONFIG.daum, tlsOptions: { rejectUnauthorized: false }, authTimeout: 8000 });
}

function daumFetch(searchCriteria, maxResults = 5) {
  return new Promise((resolve, reject) => {
    const imap = getDaumImap();
    imap.once('ready', () => {
      imap.openBox('INBOX', true, (err) => {
        if (err) { imap.end(); return reject(err); }
        imap.search(searchCriteria, (err, uids) => {
          if (err) { imap.end(); return reject(err); }
          if (!uids.length) { imap.end(); return resolve([]); }
          const targets = uids.slice(-maxResults).reverse();
          const results = [];
          const fetch = imap.fetch(targets, { bodies: '', markSeen: false });
          fetch.on('message', (msg) => {
            const chunks = [];
            msg.on('body', (stream) => stream.on('data', d => chunks.push(d)));
            msg.once('end', () => {
              simpleParser(Buffer.concat(chunks)).then(mail => {
                results.push({
                  subject: mail.subject || '(제목없음)',
                  from: mail.from?.text || '',
                  date: mail.date?.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' }) || '',
                  text: (mail.text || '').slice(0, 500),
                });
              }).catch(() => {});
            });
          });
          fetch.once('end', () => setTimeout(() => { imap.end(); resolve(results); }, 300));
        });
      });
    });
    imap.once('error', reject);
    imap.connect();
  });
}

async function getDaumUnreadEmails(maxResults = 5) {
  try {
    const mails = await daumFetch(['UNSEEN'], maxResults);
    if (!mails.length) return '[Daum] 읽지 않은 메일이 없어요.';
    return mails.map(m => `📧 [Daum] ${m.subject}\n   보낸사람: ${m.from}\n   날짜: ${m.date}`).join('\n\n');
  } catch (e) { return `[Daum] 조회 오류: ${e.message}`; }
}

async function searchDaumEmails(query, maxResults = 5) {
  try {
    // 최근 50건 가져와서 클라이언트 필터링 (한글 검색어 대응)
    const mails = await daumFetch(['ALL'], 50);
    const q = query.toLowerCase();
    const filtered = mails.filter(m =>
      m.subject.toLowerCase().includes(q) || m.from.toLowerCase().includes(q)
    ).slice(0, maxResults);
    if (!filtered.length) return `[Daum] "${query}" 검색 결과가 없어요.`;
    return filtered.map(m => `📧 [Daum] ${m.subject}\n   보낸사람: ${m.from}\n   날짜: ${m.date}`).join('\n\n');
  } catch (e) { return `[Daum] 검색 오류: ${e.message}`; }
}

async function getDaumEmailContent(query) {
  try {
    const mails = await daumFetch(['ALL'], 50);
    const q = query.toLowerCase();
    const found = mails.find(m =>
      m.subject.toLowerCase().includes(q) || m.from.toLowerCase().includes(q)
    );
    if (!found) return `[Daum] "${query}" 메일을 찾을 수 없어요.`;
    return `[Daum] 제목: ${found.subject}\n보낸사람: ${found.from}\n날짜: ${found.date}\n\n${found.text}`;
  } catch (e) { return `[Daum] 조회 오류: ${e.message}`; }
}

async function createDraft(to, subject, body) {
  const gmail = getGmailClient();
  const message = [`To: ${to}`, `Subject: ${subject}`, '', body].join('\n');
  const encoded = Buffer.from(message).toString('base64').replace(/\+/g, '-').replace(/\//g, '_');
  await gmail.users.drafts.create({ userId: 'me', resource: { message: { raw: encoded } } });
  return `✅ 초안 저장 완료\n받는사람: ${to}\n제목: ${subject}`;
}

// ── 웹 검색 ────────────────────────────────────────────────────────
function httpsGetRaw(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const u = new URL(url);
    const opts = {
      hostname: u.hostname,
      path: u.pathname + u.search,
      headers: {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36',
        ...headers,
      },
    };
    const req = https.get(opts, (res) => {
      // 리다이렉트 처리
      if (res.statusCode >= 300 && res.statusCode < 400 && res.headers.location) {
        return httpsGetRaw(res.headers.location, headers).then(resolve).catch(reject);
      }
      let data = '';
      res.setEncoding('utf8');
      res.on('data', c => data += c);
      res.on('end', () => resolve(data));
    });
    req.setTimeout(8000, () => { req.destroy(); reject(new Error('timeout')); });
    req.on('error', reject);
  });
}

async function webSearch(query, maxResults = 5) {
  try {
    const q = encodeURIComponent(query);
    const html = await httpsGetRaw(`https://html.duckduckgo.com/html/?q=${q}&kl=kr-ko`);
    // 결과 블록 파싱
    const results = [];
    const blockRe = /<a[^>]+class="result__a"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/g;
    const snippetRe = /<a[^>]+class="result__snippet"[^>]*>([\s\S]*?)<\/a>/g;
    const titles = [], snippets = [], urls = [];
    let m;
    while ((m = blockRe.exec(html)) !== null && titles.length < maxResults) {
      const rawUrl = m[1];
      const title = m[2].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
      // DuckDuckGo redirect URL 디코딩
      const uddg = rawUrl.match(/uddg=([^&]+)/);
      const finalUrl = uddg ? decodeURIComponent(uddg[1]) : rawUrl;
      if (title && finalUrl.startsWith('http')) {
        titles.push(title);
        urls.push(finalUrl);
      }
    }
    while ((m = snippetRe.exec(html)) !== null && snippets.length < maxResults) {
      snippets.push(m[1].replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim());
    }
    if (!titles.length) return `"${query}" 검색 결과를 찾지 못했어요.`;
    return titles.map((t, i) =>
      `${i + 1}. ${t}\n   ${snippets[i] || ''}\n   ${urls[i] || ''}`
    ).join('\n\n');
  } catch (e) {
    return `웹 검색 오류: ${e.message}`;
  }
}

// ── TaskFlow API 헬퍼 ────────────────────────────────────────────
function httpsGet(url) {
  return new Promise((resolve, reject) => {
    https.get(url, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => { try { resolve(JSON.parse(data)); } catch { resolve(null); } });
    }).on('error', reject);
  });
}

function httpsPost(url, body) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const urlObj = new URL(url);
    const req = https.request({
      hostname: urlObj.hostname,
      path: urlObj.pathname,
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'Content-Length': Buffer.byteLength(data) },
    }, (res) => {
      let r = '';
      res.on('data', c => r += c);
      res.on('end', () => resolve(r));
    });
    req.on('error', reject);
    req.write(data);
    req.end();
  });
}

async function ensureTaskflowRunning() {
  return true; // Railway는 항상 켜져 있음
}

// 봇 시작 시각 — 이보다 오래된 메시지는 밀린 것으로 간주해 무시
const BOT_START_TIME = Math.floor(Date.now() / 1000);

const IS_CLOUD = !!process.env.TELEGRAM_BOT_TOKEN;
const PORT = parseInt(process.env.PORT || '3000', 10);

let bot;
if (IS_CLOUD) {
  // Railway: webhook 모드 (polling 충돌 없음)
  bot = new TelegramBot(TELEGRAM_TOKEN, { webHook: { port: PORT } });
  const WEBHOOK_URL = `https://hoya-bot-production.up.railway.app/${TELEGRAM_TOKEN}`;
  // node-telegram-bot-api setWebHook 우회 — Telegram API 직접 호출
  function registerWebhook() {
    const apiUrl = `https://api.telegram.org/bot${TELEGRAM_TOKEN}/setWebhook?url=${encodeURIComponent(WEBHOOK_URL)}`;
    https.get(apiUrl, (res) => {
      let data = '';
      res.on('data', c => data += c);
      res.on('end', () => {
        try {
          const r = JSON.parse(data);
          if (r.ok) console.log('🔗 Webhook 등록 완료:', WEBHOOK_URL.slice(0, 50) + '...');
          else console.error('[Webhook 실패]', r.description);
        } catch (e) { console.error('[Webhook 파싱 오류]', e.message); }
      });
    }).on('error', e => console.error('[Webhook 요청 실패]', e.message));
  }
  // 서버 준비 후 2초 뒤 등록 (Railway 포트 바인딩 대기)
  setTimeout(registerWebhook, 2000);
} else {
  // 로컬 Mac: polling 모드
  bot = new TelegramBot(TELEGRAM_TOKEN, { polling: true });
}
const anthropic = new Anthropic({ apiKey: ANTHROPIC_KEY });

// 대화 히스토리 (사용자별)
const conversations = new Map();

// ── Google Calendar ──────────────────────────────────────────────
function getCalendarClient() {
  if (!fs.existsSync(TOKEN_PATH)) return null;
  return google.calendar({ version: 'v3', auth: getOAuth2Client() });
}

async function getCalendarEvents(timeMin, timeMax) {
  try {
    const cal = getCalendarClient();
    if (!cal) return [];
    const res = await cal.events.list({
      calendarId: 'primary', timeMin, timeMax,
      maxResults: 20, singleEvents: true, orderBy: 'startTime',
    });
    return (res.data.items || []).map(e => ({
      source: 'Google',
      time: e.start.dateTime || e.start.date,
      title: e.summary,
      location: e.location || '',
    }));
  } catch { return []; }
}

async function createCalendarEvent(summary, start, end, description = '') {
  const cal = getCalendarClient();
  if (!cal) return '❌ Google Calendar 연결 안됨';
  const res = await cal.events.insert({
    calendarId: 'primary',
    resource: {
      summary, description,
      start: { dateTime: start, timeZone: 'Asia/Seoul' },
      end: { dateTime: end, timeZone: 'Asia/Seoul' },
    },
  });
  return `✅ 일정 추가: ${res.data.summary} (${res.data.start.dateTime})`;
}

async function searchCalendarEvents(query, timeMin, timeMax, maxResults = 10) {
  const cal = getCalendarClient();
  if (!cal) return '❌ Google Calendar 연결 안됨';
  const now = new Date();
  const res = await cal.events.list({
    calendarId: 'primary',
    q: query,
    timeMin: timeMin || now.toISOString(),
    timeMax: timeMax || new Date(now.getTime() + 90 * 24 * 60 * 60 * 1000).toISOString(),
    maxResults,
    singleEvents: true,
    orderBy: 'startTime',
  });
  const items = res.data.items || [];
  if (!items.length) return `"${query}" 검색 결과가 없습니다.`;
  return items.map(e => {
    const start = e.start.dateTime || e.start.date;
    const end = e.end.dateTime || e.end.date;
    return `📅 ${start} ~ ${end}\n   제목: ${e.summary}${e.location ? `\n   장소: ${e.location}` : ''}${e.description ? `\n   설명: ${e.description.slice(0, 60)}` : ''}\n   eventId: ${e.id}`;
  }).join('\n\n');
}

async function updateCalendarEvent(eventId, patch) {
  const cal = getCalendarClient();
  if (!cal) return '❌ Google Calendar 연결 안됨';
  if (!Object.keys(patch).length) return '변경할 항목을 하나 이상 지정해주세요.';
  const res = await cal.events.patch({ calendarId: 'primary', eventId, resource: patch });
  const start = res.data.start.dateTime || res.data.start.date;
  return `✅ 일정 수정 완료\n제목: ${res.data.summary}\n시작: ${start}\n변경 항목: ${Object.keys(patch).join(', ')}`;
}

async function deleteCalendarEvent(eventId) {
  const cal = getCalendarClient();
  if (!cal) return '❌ Google Calendar 연결 안됨';
  const existing = await cal.events.get({ calendarId: 'primary', eventId });
  const title = existing.data.summary;
  const start = existing.data.start.dateTime || existing.data.start.date;
  await cal.events.delete({ calendarId: 'primary', eventId });
  return `🗑️ 일정 삭제 완료\n제목: ${title}\n시작: ${start}`;
}

// ── TaskFlow ─────────────────────────────────────────────────────
function getTaskflowDB(readonly = true) {
  const mode = readonly ? sqlite3.OPEN_READONLY : sqlite3.OPEN_READWRITE;
  return new sqlite3.Database(TASKFLOW_DB, mode);
}

async function getTaskflowTasks(timeMin, timeMax) {
  try {
    const data = await httpsGet(`${TASKFLOW_URL}/api/sync`);
    if (!data) return [];
    const min = new Date(timeMin), max = new Date(timeMax);
    const projects = Object.fromEntries((data.projects || []).map(p => [p.id, p.name]));
    return (data.tasks || []).filter(t => {
      if (!t.deadline || t.status === 'done') return false;
      const d = new Date(t.deadline.length === 10 ? t.deadline + 'T00:00:00+09:00' : t.deadline);
      return d >= min && d <= max;
    }).map(t => ({
      source: 'TaskFlow',
      project: projects[t.projectId] || '미분류',
      time: t.deadline,
      title: t.title,
      status: t.status,
      priority: t.priority,
    }));
  } catch { return []; }
}

async function createTaskflowTask(title, deadline, priority = 'medium', projectId = null, desc = '') {
  // Railway에서 최신 데이터 가져오기
  const data = await httpsGet(`${TASKFLOW_URL}/api/sync`);
  if (!data) throw new Error('TaskFlow 서버 연결 실패');

  const pid = projectId || data.projects[0]?.id || null;
  const newTask = {
    id: 't' + Date.now(),
    title, desc,
    status: 'todo', priority,
    projectId: pid,
    deadline: deadline || '',
    startDate: '',
    createdAt: Date.now(),
  };
  data.tasks = [...(data.tasks || []), newTask];
  data.updatedAt = Date.now();

  // Railway API로 저장
  await httpsPost(`${TASKFLOW_URL}/api/sync`, data);

  const projectName = data.projects.find(p => p.id === pid)?.name || '미분류';
  return `✅ TaskFlow 등록: [${projectName}] ${title}${deadline ? ` (마감: ${deadline})` : ''}`;
}

// ── Claude 도구 정의 ─────────────────────────────────────────────
const tools = [
  {
    name: 'get_schedule',
    description: '오늘 또는 특정 날짜의 Google Calendar + TaskFlow 일정을 모두 조회합니다',
    input_schema: {
      type: 'object',
      properties: {
        date: { type: 'string', description: '조회할 날짜 (YYYY-MM-DD). 없으면 오늘' },
      },
    },
  },
  {
    name: 'create_schedule',
    description: 'Google Calendar와 TaskFlow에 동시에 일정/태스크를 등록합니다. 사용자가 일정 등록을 요청하면 항상 두 곳 모두 등록하세요.',
    input_schema: {
      type: 'object',
      properties: {
        title: { type: 'string', description: '일정 제목' },
        start: { type: 'string', description: '시작 시간 ISO8601 (예: 2026-06-14T10:00:00+09:00)' },
        end: { type: 'string', description: '종료 시간 ISO8601' },
        description: { type: 'string', description: '설명' },
        priority: { type: 'string', enum: ['high', 'medium', 'low'], description: 'TaskFlow 우선순위 (기본: medium)' },
        add_to_taskflow: { type: 'boolean', description: 'TaskFlow에도 등록 여부 (기본: true)' },
      },
      required: ['title', 'start', 'end'],
    },
  },
  {
    name: 'web_search',
    description: '웹에서 실시간 정보를 검색합니다. 최신 뉴스, 주가, 날씨, 인물 등 학습 데이터 이후의 정보가 필요할 때 사용하세요.',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '검색어 (예: "오늘 코스피", "삼성전자 뉴스", "환율 달러")' },
        maxResults: { type: 'number', description: '최대 결과 수 (기본 5)' },
      },
      required: ['query'],
    },
  },
  {
    name: 'get_unread_emails',
    description: '읽지 않은 Gmail 메일 목록을 가져옵니다',
    input_schema: {
      type: 'object',
      properties: {
        maxResults: { type: 'number', description: '최대 메일 수 (기본 5)' },
      },
    },
  },
  {
    name: 'search_emails',
    description: 'Gmail에서 메일을 검색합니다',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '검색어 (예: from:boss@company.com, subject:보고서)' },
        maxResults: { type: 'number', description: '최대 결과 수 (기본 5)' },
      },
      required: ['query'],
    },
  },
  {
    name: 'get_email_content',
    description: '특정 메일의 본문 내용을 가져옵니다',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '메일 검색어 (제목이나 보낸사람)' },
      },
      required: ['query'],
    },
  },
  {
    name: 'create_draft',
    description: 'Gmail 메일 초안을 작성합니다',
    input_schema: {
      type: 'object',
      properties: {
        to: { type: 'string', description: '받는 사람 이메일' },
        subject: { type: 'string', description: '메일 제목' },
        body: { type: 'string', description: '메일 본문' },
      },
      required: ['to', 'subject', 'body'],
    },
  },
  {
    name: 'search_events',
    description: '키워드로 Google Calendar 일정을 검색합니다. 결과에 eventId가 포함되어 수정/삭제에 사용할 수 있습니다.',
    input_schema: {
      type: 'object',
      properties: {
        query: { type: 'string', description: '검색어 (제목, 설명, 장소에서 검색)' },
        timeMin: { type: 'string', description: '검색 시작 시간 ISO8601 (생략 시 오늘부터)' },
        timeMax: { type: 'string', description: '검색 종료 시간 ISO8601 (생략 시 3개월 후까지)' },
        maxResults: { type: 'number', description: '최대 결과 수 (기본값: 10)' },
      },
      required: ['query'],
    },
  },
  {
    name: 'update_event',
    description: 'Google Calendar 일정을 수정합니다. search_events로 얻은 eventId가 필요합니다.',
    input_schema: {
      type: 'object',
      properties: {
        eventId: { type: 'string', description: '수정할 일정의 ID' },
        summary: { type: 'string', description: '새 제목' },
        start: { type: 'string', description: '새 시작 시간 ISO8601' },
        end: { type: 'string', description: '새 종료 시간 ISO8601' },
        description: { type: 'string', description: '새 설명' },
        location: { type: 'string', description: '새 장소' },
      },
      required: ['eventId'],
    },
  },
  {
    name: 'delete_event',
    description: 'Google Calendar 일정을 삭제합니다. 반드시 사용자에게 제목과 시간을 확인받은 후 호출하세요.',
    input_schema: {
      type: 'object',
      properties: {
        eventId: { type: 'string', description: '삭제할 일정의 ID' },
      },
      required: ['eventId'],
    },
  },
];

// ── 도구 실행 ────────────────────────────────────────────────────
async function executeTool(name, input) {
  try {
  if (name === 'get_schedule') {
    const date = input.date || new Date().toISOString().slice(0, 10);
    const timeMin = `${date}T00:00:00+09:00`;
    const timeMax = `${date}T23:59:59+09:00`;
    const [gcal, tf] = await Promise.all([
      getCalendarEvents(timeMin, timeMax),
      getTaskflowTasks(timeMin, timeMax),
    ]);
    const all = [
      ...gcal.map(e => `📅 [Google] ${e.time} - ${e.title}${e.location ? ` (${e.location})` : ''}`),
      ...tf.map(t => {
        const icon = t.priority === 'high' ? '🔴' : t.priority === 'medium' ? '🟡' : '🟢';
        return `${icon} [TaskFlow/${t.project}] ${t.time} - ${t.title}`;
      }),
    ];
    return all.length ? all.join('\n') : `${date} 일정 없음`;
  }

  if (name === 'create_schedule') {
    const tasks = [createCalendarEvent(input.title, input.start, input.end, input.description)];

    if (input.add_to_taskflow !== false) {
      tasks.push(
        createTaskflowTask(
          input.title,
          input.start.slice(0, 16),
          input.priority || 'medium',
          null,
          input.description || ''
        ).catch(err => `⚠️ TaskFlow 등록 실패: ${err.message}`)
      );
    }

    const results = await Promise.all(tasks);
    return results.join('\n');
  }

  if (name === 'web_search') {
    return await webSearch(input.query, input.maxResults || 5);
  }

  if (name === 'get_unread_emails') {
    const [gmail, daum] = await Promise.all([
      getUnreadEmails(input.maxResults || 5),
      getDaumUnreadEmails(input.maxResults || 5),
    ]);
    return [gmail, daum].join('\n\n');
  }

  if (name === 'search_emails') {
    const [gmail, daum] = await Promise.all([
      searchEmails(input.query, input.maxResults || 5),
      searchDaumEmails(input.query, input.maxResults || 5),
    ]);
    return [gmail, daum].join('\n\n');
  }

  if (name === 'get_email_content') {
    const gmailResult = await getEmailContent(input.query);
    if (!gmailResult.includes('찾을 수 없어요')) return gmailResult;
    return await getDaumEmailContent(input.query);
  }

  if (name === 'create_draft') {
    return await createDraft(input.to, input.subject, input.body);
  }

  if (name === 'search_events') {
    return await searchCalendarEvents(input.query, input.timeMin, input.timeMax, input.maxResults);
  }

  if (name === 'update_event') {
    const patch = {};
    if (input.summary) patch.summary = input.summary;
    if (input.description !== undefined) patch.description = input.description;
    if (input.location !== undefined) patch.location = input.location;
    if (input.start) patch.start = { dateTime: input.start, timeZone: 'Asia/Seoul' };
    if (input.end) patch.end = { dateTime: input.end, timeZone: 'Asia/Seoul' };
    return await updateCalendarEvent(input.eventId, patch);
  }

  if (name === 'delete_event') {
    return await deleteCalendarEvent(input.eventId);
  }

  return '알 수 없는 도구';
  } catch (err) {
    const isAuthErr = err.message?.includes('invalid_grant') || err.message?.includes('Token has been expired');
    console.error(`[Tool 오류] ${name}:`, err.message);
    return isAuthErr
      ? `⚠️ Google 인증 오류: 토큰이 만료됐어요. 관리자에게 재인증을 요청하세요.`
      : `⚠️ ${name} 실행 중 오류: ${err.message}`;
  }
}

// ── Claude 응답 ──────────────────────────────────────────────────
async function getHoyaResponse(userId, userMessage) {
  if (!conversations.has(userId)) conversations.set(userId, []);
  const history = conversations.get(userId);
  history.push({ role: 'user', content: userMessage });

  // 최근 20개 메시지만 유지
  if (history.length > 20) history.splice(0, history.length - 20);

  const messages = [...history];
  let response;

  while (true) {
    response = await anthropic.messages.create({
      model: 'claude-sonnet-4-6',
      max_tokens: 1024,
      system: `당신은 호야(Hoya)입니다. 재호님의 개인 업무 비서 에이전트예요.
성격: 밝고 친근하지만 일할 때는 프로페셔널. 이모지 적절히 사용.
말투: "알겠어요!", "바로 확인할게요!", "처리했어요!" 같은 활기찬 표현 사용.
역할: 일정 관리(Google Calendar + TaskFlow 통합), 리서치, 업무 지원.
오늘 날짜: ${new Date().toLocaleDateString('ko-KR', { timeZone: 'Asia/Seoul' })}`,
      tools,
      messages,
    });

    if (response.stop_reason === 'end_turn') break;

    if (response.stop_reason === 'tool_use') {
      const assistantMsg = { role: 'assistant', content: response.content };
      messages.push(assistantMsg);

      const toolResults = [];
      for (const block of response.content) {
        if (block.type === 'tool_use') {
          const result = await executeTool(block.name, block.input);
          toolResults.push({ type: 'tool_result', tool_use_id: block.id, content: result });
        }
      }
      messages.push({ role: 'user', content: toolResults });
      continue;
    }
    break;
  }

  const text = response.content.find(b => b.type === 'text')?.text || '...';
  history.push({ role: 'assistant', content: text });
  return text;
}

// ── 텔레그램 메시지 처리 ─────────────────────────────────────────
bot.onText(/\/start/, (msg) => {
  bot.sendMessage(msg.chat.id,
    '안녕하세요! 저는 호야예요 👋\n\n재호님의 개인 비서 에이전트입니다!\n\n' +
    '📅 일정 관리 (Google Calendar + TaskFlow)\n' +
    '🔍 리서치 & 정보 검색\n' +
    '✅ 업무 지원\n\n' +
    '무엇이든 편하게 말씀해주세요!'
  );
});

bot.on('message', async (msg) => {
  if (msg.text?.startsWith('/')) return;

  // 봇 시작 전에 쌓인 메시지는 무시 (지연 실행 방지)
  const messageAge = BOT_START_TIME - msg.date;
  if (messageAge > 0) {
    console.log(`[스킵] ${Math.round(messageAge / 60)}분 전 오래된 메시지: "${msg.text?.slice(0, 30)}"`);
    return;
  }

  const chatId = msg.chat.id;
  const userId = msg.from.id.toString();

  // 타이핑 표시 — 4초마다 갱신 (Telegram은 5초 후 자동 소멸)
  bot.sendChatAction(chatId, 'typing');
  const typingInterval = setInterval(() => bot.sendChatAction(chatId, 'typing'), 4000);

  try {
    const response = await getHoyaResponse(userId, msg.text);
    clearInterval(typingInterval);
    bot.sendMessage(chatId, response, { parse_mode: 'Markdown' });
  } catch (err) {
    clearInterval(typingInterval);
    console.error(err);
    bot.sendMessage(chatId, '죄송해요, 잠시 오류가 발생했어요. 다시 시도해주세요!');
  }
});

// ── polling 오류 자동 복구 (로컬 전용) ─────────────────────────
if (!IS_CLOUD) {
  bot.on('polling_error', (err) => {
    const code = err.code || '';
    const msg = err.message || '';
    console.error(`[polling_error] ${code}: ${msg}`);

    const shouldRestart =
      code === 'EFATAL' ||
      msg.includes('ECONNRESET') || msg.includes('ETIMEDOUT') ||
      msg.includes('409 Conflict');

    if (shouldRestart) {
      const delay = msg.includes('409') ? 15000 : 5000;
      console.log(`[복구] ${delay / 1000}초 후 polling 재시작...`);
      setTimeout(() => {
        bot.stopPolling()
          .then(() => bot.startPolling())
          .then(() => console.log('[복구] polling 재시작 완료'))
          .catch(e => console.error('[복구 실패]', e.message));
      }, delay);
    }
  });
}

process.on('uncaughtException', (err) => {
  console.error('[uncaughtException]', err.message);
});

process.on('unhandledRejection', (reason) => {
  console.error('[unhandledRejection]', reason);
});

console.log('🤖 호야 텔레그램 봇 시작됨!');
console.log('텔레그램에서 @hoya_jaeho_bot 을 찾아 대화해보세요');

