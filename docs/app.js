'use strict';

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => Array.from(document.querySelectorAll(selector));
const categories = ['Автоматически', 'Правила проекта', 'Государственные структуры', 'ФБР', 'Законодательство', 'Другое'];
let selectedFiles = [];
let chatting = false;
let toastTimer = null;

async function api(url, options = {}) { return LocalRules.api(url, options); }
const jsonOpts = (data, method = 'POST') => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
function showToast(message) {
  const box = $('#toast');
  box.textContent = message;
  box.classList.remove('hidden');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.add('hidden'), 4300);
}

function navigate(target) {
  $$('.page').forEach((p) => p.classList.toggle('visible', p.id === `page-${target}`));
  $$('.nav-item').forEach((b) => b.classList.toggle('active', b.dataset.page === target));
  $('#current-page').textContent = ({chat: 'AI ЧАТ', knowledge: 'БАЗА ЗНАНИЙ', import: 'ДОБАВЛЕНИЕ'})[target];
  if (target === 'knowledge') loadDocuments();
}

async function loadStats() {
  try {
    const stats = await api('/api/stats');
    $('#stat-docs').textContent = stats.documents;
    $('#stat-chunks').textContent = stats.chunks;
    $('#stat-categories').textContent = stats.categories.length;
    $('#nav-doc-count').textContent = stats.documents;
    $('#side-documents').textContent = `${stats.documents} документов`;
    $('#ai-state').textContent = stats.gemini_enabled ? 'Gemini AI подключён' : 'Режим поиска';
  } catch (err) { console.warn(err.message); }
}

async function loadDocuments() {
  const list = $('#documents');
  try {
    const response = await api('/api/documents');
    list.replaceChildren();
    if (!response.documents.length) { const empty = document.createElement('div'); empty.className = 'empty'; empty.textContent = 'Пока нет документов. Добавь свои первые правила.'; list.append(empty); return; }
    for (const doc of response.documents) {
      const row = document.createElement('div'); row.className = 'document-row';
      const main = document.createElement('div'); main.className = 'doc-main';
      const icon = document.createElement('span'); icon.className = 'doc-icon'; icon.textContent = '▤';
      const info = document.createElement('div'); info.className = 'doc-info';
      const title = document.createElement('strong'); title.textContent = doc.title;
      const details = document.createElement('span'); details.textContent = `${doc.category} · ${doc.chunk_count} фрагм. · ${new Date(doc.created_at).toLocaleDateString('ru-RU')}`;
      info.append(title, details); main.append(icon, info);
      const actions = document.createElement('div'); actions.className = 'doc-actions';
      if (doc.source_url && /^https?:\/\//i.test(doc.source_url)) {
        const source = document.createElement('a'); source.href = doc.source_url; source.target = '_blank'; source.rel = 'noopener noreferrer'; source.textContent = 'Источник ↗'; actions.append(source);
      }
      const del = document.createElement('button'); del.type = 'button'; del.textContent = 'Удалить';
      del.addEventListener('click', async () => {
        if (!confirm(`Удалить «${doc.title}» из базы?`)) return;
        del.disabled = true;
        try { await api(`/api/documents/${doc.id}`, { method: 'DELETE' }); await Promise.all([loadDocuments(), loadStats()]); showToast('Документ удалён'); }
        catch (err) { del.disabled = false; showToast(err.message); }
      });
      actions.append(del); row.append(main, actions); list.append(row);
    }
  } catch (err) { list.textContent = err.message; }
}

function chatMessage(text, role, sources = [], mode = 'gemini') {
  const log = $('#chat-log');
  const welcome = log.querySelector('.welcome-card');
  if (welcome) welcome.remove();
  const item = document.createElement('div'); item.className = `message ${role}`;
  if (role === 'bot') { const label = document.createElement('span'); label.className = 'message-label'; label.textContent = mode === 'gemini' ? '✦ ARIZONA AI' : '⌕ ПОИСК ПО БАЗЕ'; item.append(label); }
  const body = document.createElement('div'); body.textContent = text; item.append(body);
  if (sources.length) {
    const heading = document.createElement('div'); heading.className = 'source-title'; heading.textContent = 'Найденные источники'; item.append(heading);
    const holder = document.createElement('div'); holder.className = 'source-list';
    for (const src of sources) {
      const source = document.createElement('div'); source.className = 'source';
      const name = document.createElement(src.url && /^https?:\/\//i.test(src.url) ? 'a' : 'span');
      name.textContent = `[${src.ref}] ${src.title}`;
      if (name.tagName === 'A') { name.href = src.url; name.target = '_blank'; name.rel = 'noopener noreferrer'; }
      const tag = document.createElement('span'); tag.textContent = src.category;
      const excerpt = document.createElement('p'); excerpt.textContent = src.excerpt.trim().slice(0, 185) + '…';
      source.append(name, tag, excerpt); holder.append(source);
    }
    item.append(holder);
  }
  log.append(item);
  log.scrollTop = log.scrollHeight;
  return item;
}

async function ask(question) {
  if (chatting || question.trim().length < 3) return;
  chatting = true;
  $('#send').disabled = true;
  $('#question').value = '';
  chatMessage(question.trim(), 'user');
  const pending = chatMessage('Ищу подходящие правила…', 'bot', [], 'search');
  try {
    const data = await api('/api/chat', jsonOpts({ question: question.trim() }));
    pending.remove(); chatMessage(data.answer, 'bot', data.sources, data.mode);
  } catch (err) { pending.remove(); chatMessage(`Не удалось получить ответ: ${err.message}`, 'bot', [], 'search'); }
  finally { chatting = false; $('#send').disabled = false; $('#question').focus(); }
}

function setImportTab(tab) {
  $$('[data-import-tab]').forEach((element) => element.classList.toggle('active', element.dataset.importTab === tab));
  ['files', 'urls', 'text'].forEach((type) => $(`#import-${type}`).classList.toggle('hidden', type !== tab));
  $('#import-result').classList.add('hidden');
}

function showImportResult(result) {
  const box = $('#import-result');
  box.classList.remove('hidden');
  box.classList.toggle('error', result.errors.length > 0);
  const imported = result.items.filter((i) => !i.duplicate).length;
  const repeats = result.items.filter((i) => i.duplicate).length;
  let message = `Добавлено: ${imported}. Уже были в базе: ${repeats}.`;
  if (result.items.length) message += '\n' + result.items.map((i) => `• ${i.title} — ${i.category} (${i.chunks} фрагм.)`).join('\n');
  if (result.errors.length) message += '\n\nОшибки:\n' + result.errors.map((e) => `• ${e.file}: ${e.error}`).join('\n');
  box.textContent = message;
  loadStats();
}
function showImportError(message) { const box = $('#import-result'); box.textContent = message; box.className = 'notice error'; }
function busyForm(form, enabled) { const button = form.querySelector('button[type=submit]'); button.disabled = enabled; }

function setup() {
  $('#export').addEventListener('click', () => { try { LocalRules.backup(); } catch (err) { showToast(err.message); } });
  $$('.nav-item').forEach((button) => button.addEventListener('click', () => navigate(button.dataset.page)));
  $$('.go-import').forEach((b) => b.addEventListener('click', () => navigate('import')));
  $('#refresh').addEventListener('click', () => { loadDocuments(); loadStats(); });
  $$('.suggestions button').forEach((button) => button.addEventListener('click', () => ask(button.dataset.question)));
  $('#chat-form').addEventListener('submit', (e) => { e.preventDefault(); ask($('#question').value); });
  $('#question').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); ask($('#question').value); } });
  $$('.tabs button').forEach((button) => button.addEventListener('click', () => setImportTab(button.dataset.importTab)));
  ['file-category', 'url-category', 'text-category'].forEach((id) => { for (const category of categories) { const option = document.createElement('option'); option.value = option.textContent = category; $(`#${id}`).append(option); } });
  const dropzone = $('#dropzone');
  dropzone.addEventListener('click', () => $('#file-input').click());
  dropzone.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('#file-input').click(); } });
  dropzone.addEventListener('dragover', (e) => { e.preventDefault(); dropzone.classList.add('drag-over'); });
  dropzone.addEventListener('dragleave', () => dropzone.classList.remove('drag-over'));
  dropzone.addEventListener('drop', (e) => { e.preventDefault(); dropzone.classList.remove('drag-over'); selectedFiles = Array.from(e.dataTransfer.files); showSelected(); });
  $('#file-input').addEventListener('change', (e) => { selectedFiles = Array.from(e.target.files); showSelected(); });
  $('#file-form').addEventListener('submit', async (event) => {
    event.preventDefault(); if (!selectedFiles.length) return showImportError('Выбери файлы для импорта.');
    const form = event.currentTarget; busyForm(form, true);
    try { const data = new FormData(); selectedFiles.forEach((file) => data.append('files', file)); data.append('category', $('#file-category').value); const result = await api('/api/ingest/files', { method: 'POST', body: data }); showImportResult(result); selectedFiles = []; $('#file-input').value = ''; showSelected(); }
    catch (err) { showImportError(err.message); } finally { busyForm(form, false); }
  });
  $('#urls-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form = event.currentTarget; busyForm(form, true);
    try { const urls = $('#urls').value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean); const result = await api('/api/ingest/urls', jsonOpts({ urls, category: $('#url-category').value })); showImportResult(result); }
    catch (err) { showImportError(err.message); } finally { busyForm(form, false); }
  });
  $('#text-form').addEventListener('submit', async (event) => {
    event.preventDefault(); const form = event.currentTarget; busyForm(form, true);
    try { const data = { title: $('#text-title').value, text: $('#text-content').value, source_url: $('#text-url').value, category: $('#text-category').value }; const result = await api('/api/ingest/text', jsonOpts(data)); showImportResult(result); }
    catch (err) { showImportError(err.message); } finally { busyForm(form, false); }
  });
  $('#login-form').addEventListener('submit', async (event) => {
    event.preventDefault(); $('#login-error').textContent = '';
    try { await api('/api/session', jsonOpts({ password: $('#login-password').value })); $('#login-password').value = ''; $('#login-screen').classList.add('hidden'); loadStats(); }
    catch (err) { $('#login-error').textContent = err.message; }
  });
  $('#logout').addEventListener('click', async () => {
    try { await api('/api/session', { method: 'DELETE' }); window.location.reload(); }
    catch (err) { showToast(err.message); }
  });
}
function showSelected() {
  const holder = $('#selected-files'); holder.replaceChildren();
  selectedFiles.forEach((file) => { const span = document.createElement('div'); span.textContent = `▤ ${file.name} (${(file.size / 1024).toFixed(0)} КБ)`; holder.append(span); });
}
async function initialize() {
  setup();
  try { const session = await api('/api/session'); if (!session.authenticated) $('#login-screen').classList.remove('hidden'); else await loadStats(); }
  catch (err) { showToast(err.message); }
}
initialize();
