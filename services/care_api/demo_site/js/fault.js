/* ==========================================================================
   BeKavod — как выглядит поломка.

   Одна карточка на весь сайт: и в журнале вместо ленты, и на странице
   состояния как приговор. Правило простое — три строки в одном порядке:
        что случилось · почему мы так решили · что делать.
   Технические подробности спрятаны под кнопку и разворачиваются только
   если их попросили: обычному человеку номер запроса не нужен, а вот
   владельцу без него не разобраться.

   Команд для консоли здесь нет намеренно. Репозиторий сайта публичный,
   и «показать только владельцу» на клиенте не прячет ничего: подсказки
   вида «перезапустите такой-то сервис» видны в исходниках всем, а вместе
   с ними — карта внутренностей дома. Что смотреть при каждом коде сбоя,
   лежит в docs/api-journal.md, который в репозиторий не уходит.
   ========================================================================== */
(function () {
  'use strict';

  var WHERE = {
    device: 'on your device',
    link:   'on the way to the house',
    server: 'on the server',
    app:    'in the system setup',
    auth:   'with access'
  };

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  /* Иконка не декоративная: по ней видно, куда смотреть, ещё до чтения.
     Дом с крестом, облако с разрывом, замок — три разных беды. */
  function icon(where) {
    if (where === 'device') return '<path d="M4 8.5A16 16 0 0 1 20 8.5M7.5 12.5a10.5 10.5 0 0 1 9 0M11 16.6a4.5 4.5 0 0 1 2 0" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><path d="m3 3 18 18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>';
    if (where === 'auth')   return '<path d="M7 10V7a5 5 0 0 1 10 0v3" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/><rect x="4.5" y="10" width="15" height="10.5" rx="2.6" fill="currentColor"/>';
    if (where === 'server') return '<rect x="3" y="4" width="18" height="7" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><rect x="3" y="13" width="18" height="7" rx="2" fill="none" stroke="currentColor" stroke-width="1.8"/><path d="M7 7.5h.01M7 16.5h.01" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/>';
    if (where === 'app')    return '<path d="M12 3v4M12 17v4M3 12h4M17 12h4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M18.4 5.6l-2.8 2.8M8.4 15.6l-2.8 2.8" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.8"/>';
    return '<path d="M6.5 18a4.5 4.5 0 0 1-.4-9A6.5 6.5 0 0 1 18.6 9.4 4.3 4.3 0 0 1 18 18" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="m3 3 18 18" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/>';
  }

  function techRows(t) {
    var rows = [
      ['address', t.url],
      ['method',  t.method],
      ['answer',  t.status ? t.status : 'no answer'],
      ['waited',  t.ms != null ? t.ms + ' ms' : null],
      ['request ID', t.requestId],
      ['failure type', t.kind],
      ['from the browser', t.raw],
      ['time',    t.when]
    ];
    return rows.filter(function (r) { return r[1]; }).map(function (r) {
      return '<div class="fx__row"><dt>' + esc(r[0]) + '</dt><dd>' +
             esc(r[1]) + '</dd></div>';
    }).join('');
  }

  /* Текст для отправки владельцу. Собирается из того же, что показано —
     никаких скрытых полей: неприятно обнаружить, что кнопка «скопировать»
     утащила больше, чем было на экране. */
  function report(d) {
    var t = d.tech || {};
    return ['BeKavod — failure',
      'what: ' + d.title,
      'code: ' + d.code,
      'address: ' + (t.url || '—'),
      'answer: ' + (t.status || 'none'),
      'waited: ' + (t.ms != null ? t.ms + ' ms' : '—'),
      'request ID: ' + (t.requestId || '—'),
      'browser said: ' + (t.raw || '—'),
      'time: ' + (t.when || '—'),
      'page: ' + (t.page || '—'),
      'device: ' + (t.agent || '—')].join('\n');
  }

  function html(d, opt) {
    opt = opt || {};
    var acts = '';
    if (d.retry !== false) {
      acts += '<button class="btn btn--primary btn--sm" data-fx-retry type="button">' +
              'Try again</button>';
    }
    if (d.signin) {
      acts += '<button class="btn btn--primary btn--sm" data-fx-signin type="button">' +
              'Sign in again</button>';
    }
    if (!opt.onStatus) {
      acts += '<a class="btn btn--ghost btn--sm" href="' + (opt.statusHref || '/status/') +
              '">Check the whole chain</a>';
    }

    return '<section class="fx fx--' + esc(d.level || 'crit') + '" role="alert">' +
      '<div class="fx__head">' +
        '<span class="fx__icon" aria-hidden="true"><svg viewBox="0 0 24 24">' +
          icon(d.where) + '</svg></span>' +
        '<div>' +
          '<p class="fx__eyebrow">Not working · ' +
            esc(WHERE[d.where] || 'in the system') + '</p>' +
          '<h2 class="fx__t">' + esc(d.title) + '</h2>' +
        '</div>' +
      '</div>' +

      '<p class="fx__why">' + esc(d.why) + '</p>' +

      (d.hint && d.hint.length
        ? '<div class="fx__do"><p class="fx__do-h">What to do</p><ul>' +
          d.hint.map(function (h) { return '<li>' + esc(h) + '</li>'; }).join('') +
          '</ul></div>'
        : '') +

      (acts ? '<div class="fx__acts">' + acts + '</div>' : '') +

      '<details class="fx__more">' +
        '<summary>Details</summary>' +
        '<dl class="fx__tech">' + techRows(d.tech || {}) + '</dl>' +
        '<button class="btn btn--ghost btn--sm" data-fx-copy type="button">' +
          'Copy report</button>' +
      '</details>' +
    '</section>';
  }

  /* Вставляем карточку и вешаем кнопки. Возвращаем узел — иногда его надо
     потом убрать руками, когда связь вернулась. */
  function mount(box, d, opt) {
    opt = opt || {};
    box.innerHTML = html(d, opt);
    box.hidden = false;

    var retry = box.querySelector('[data-fx-retry]');
    if (retry && opt.onRetry) {
      retry.addEventListener('click', function () {
        retry.disabled = true;
        retry.textContent = 'Trying…';
        opt.onRetry();
      });
    }
    var si = box.querySelector('[data-fx-signin]');
    if (si && opt.onSignin) si.addEventListener('click', opt.onSignin);

    var copy = box.querySelector('[data-fx-copy]');
    if (copy) {
      copy.addEventListener('click', function () {
        var text = report(d);
        var done = function () {
          copy.textContent = 'Copied';
          setTimeout(function () { copy.textContent = 'Copy report'; }, 1800);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, function () { fallback(text, done); });
        } else { fallback(text, done); }
      });
    }
    return box.firstChild;
  }

  /* На http и в старых браузерах clipboard недоступен — тогда просто
     выделяем текст во временном поле, дальше человек нажмёт Ctrl+C. */
  function fallback(text, done) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.cssText = 'position:fixed;inset-block-start:-1000px;opacity:0';
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand('copy'); done(); } catch (e) {}
    document.body.removeChild(ta);
  }

  window.Fault = { html: html, mount: mount, report: report };
})();
