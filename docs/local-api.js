'use strict';
// GitHub Pages edition: all documents stay in this browser.
const LocalRules = (() => {
  const key = 'arizona-rules-documents-v1';
  function read() {
    try { return JSON.parse(localStorage.getItem(key) || '[]'); }
    catch { throw new Error('Не удалось прочитать базу браузера. Проверь разрешение на хранение данных.'); }
  }
  function save(docs) {
    try { localStorage.setItem(key, JSON.stringify(docs)); }
    catch { throw new Error('Недостаточно места в браузере. Экспортируй базу и удали ненужные документы.'); }
  }
  const chunks = text => {
    const result = [];
    for (let i = 0; i < text.length; i += 1000) result.push(text.slice(i, i + 1200));
    return result;
  };
  function insert(data) {
    const text = String(data.text || '').trim();
    const title = String(data.title || '').trim().slice(0, 220);
    if (!title || text.length < 30) throw new Error('Нужны название и минимум 30 символов текста.');
    if (text.length > 800000) throw new Error('Документ превышает 800 000 символов.');
    const source = String(data.source_url || '').trim();
    if (source && !/^https?:\/\//i.test(source)) throw new Error('Источник должен быть ссылкой HTTP или HTTPS.');
    const docs = read();
    const duplicate = docs.find(d => d.text === text);
    if (duplicate) return {...duplicate, duplicate:true, chunks:duplicate.chunk_count};
    let category = data.category;
    if (!category || category === 'Автоматически') {
      const heading = title.toLowerCase();
      category = /фбр|fbi/.test(heading) ? 'ФБР' : /закон|кодекс|конституц/.test(heading) ? 'Законодательство' : /гос|полици|армия|мэрия/.test(heading) ? 'Государственные структуры' : /правила/.test(heading) ? 'Правила проекта' : 'Другое';
    }
    const doc = {id:crypto.randomUUID(), title, text, source_url:source, category, chunk_count:chunks(text).length, created_at:new Date().toISOString()};
    save([...docs,doc]);
    return {...doc,chunks:doc.chunk_count,duplicate:false};
  }
  async function api(url, options = {}) {
    const method = options.method || 'GET';
    const data = typeof options.body === 'string' ? JSON.parse(options.body) : {};
    if (url === '/api/session') return {authenticated:true,password_required:false};
    if (url === '/api/stats') { const docs = read(); return {documents:docs.length,chunks:docs.reduce((n,d)=>n+d.chunk_count,0),categories:[...new Set(docs.map(d=>d.category))],gemini_enabled:false}; }
    if (url === '/api/documents') return {documents:read()};
    if (url.startsWith('/api/documents/') && method === 'DELETE') { save(read().filter(d=>d.id !== url.split('/').pop())); return {ok:true}; }
    if (url === '/api/ingest/text') return {items:[insert(data)],errors:[]};
    if (url === '/api/ingest/files') {
      const items = [], errors = [];
      for (const file of options.body.getAll('files')) {
        try {
          if (file.size > 8*1024*1024) throw new Error('Файл больше 8 МБ.');
          if (!/\.(txt|md|html?|json)$/i.test(file.name)) throw new Error('Поддерживаются TXT, MD, HTML и резервные копии JSON.');
          let text = await file.text();
          if (/\.json$/i.test(file.name)) {
            const backup = JSON.parse(text);
            if (backup.format !== 'arizona-rules-v1' || !Array.isArray(backup.documents)) throw new Error('Это не резервная копия Arizona Rules.');
            for (const doc of backup.documents) items.push(insert(doc));
          } else {
            if (/\.html?$/i.test(file.name)) { const parsed = new DOMParser().parseFromString(text,'text/html'); parsed.querySelectorAll('script,style').forEach(n=>n.remove()); text = parsed.body.textContent || ''; }
            items.push(insert({title:file.name,text,category:options.body.get('category')}));
          }
        } catch (error) { errors.push({file:file.name,error:error.message}); }
      }
      return {items,errors};
    }
    if (url === '/api/ingest/urls') throw new Error('Скопируй текст с форума во вкладку «Вставить текст».');
    if (url === '/api/chat') {
      const stop = new Set('что такое как какие какая какой когда где для про это при его мне есть все или также еще уже ли же не на по от за со во их правила правило'.split(' '));
      const terms = [...new Set((data.question.toLowerCase().match(/[а-яёa-z0-9]+/g)||[]).filter(w=>w.length>1&&!stop.has(w)).map(w=>w.length>5?w.slice(0,-2):w))];
      const matches = read().flatMap(d=>chunks(d.text).map(content=>({doc:d,content,score:terms.reduce((n,t)=>n+(content.toLowerCase().includes(t)?1:0)+(d.title.toLowerCase().includes(t)?0.5:0),0)}))).filter(m=>m.score>0).sort((a,b)=>b.score-a.score).slice(0,5);
      return {mode:'search',answer:matches.length?'Найдены фрагменты по словам вопроса. Это поиск, а не ответ ИИ. Проверь контекст в оригинале.\n\n'+matches.map((m,i)=>`[${i+1}] ${m.doc.title}\n${m.content}`).join('\n\n'):'Подходящих фрагментов нет. Добавь правила в базу или попробуй другие ключевые слова.',sources:matches.map((m,i)=>({ref:i+1,document_id:m.doc.id,title:m.doc.title,category:m.doc.category,url:m.doc.source_url,excerpt:m.content}))};
    }
    throw new Error('Действие недоступно в версии GitHub Pages.');
  }
  function backup() {
    const blob = new Blob([JSON.stringify({format:'arizona-rules-v1',documents:read()},null,2)],{type:'application/json'});
    const url = URL.createObjectURL(blob), link = document.createElement('a'); link.href=url; link.download='arizona-rules-backup.json'; link.click(); setTimeout(()=>URL.revokeObjectURL(url),1000);
  }
  return {api,backup};
})();
