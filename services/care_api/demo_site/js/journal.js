/* ==========================================================================
   BeKavod — журнал событий. Работает против настоящего сервера.

   Адрес сервера задаётся в <head>:
       <meta name="api-base" content="https://api.engineeragainstdementia.net">
   Для проверки против другого сервера адрес можно подменить прямо в
   ссылке:  /journal/?api=<адрес>

   ЧТО ЗДЕСЬ ВАЖНО ПОНИМАТЬ.

   1) Гость и владелец разделены НА СЕРВЕРЕ, а не в разметке. Гостю в
      ответе просто нет полей media, evidence, vote — доставать из браузера
      нечего. Размытие кадра осталось оформлением пустой рамки.

   2) Фильтры и поиск ушли на сервер. В базе могут быть тысячи эпизодов,
      тянуть их целиком ради поиска по слову — плохой план, а на телефоне
      через туннель ещё и медленный.

   3) Листание курсорное, а не по номеру страницы. Пока человек читает,
      сверху добавляются новые записи; со смещением он на второй странице
      увидел бы то же, что на первой.

   4) Удаление и голос сначала подтверждает сервер и только потом экран.
      Обратный порядок выглядит быстрее, но врёт.

   Разбор поломок — в js/api.js, показ — в js/fault.js.
   ========================================================================== */
(function () {
  'use strict';

  var PAGE = 20;

  /* Запасной список типов — на случай, если /api/kinds не ответит.
     Совпадает с KINDS на сервере. */
  var KINDS = [
    { id: 'exit',     label: 'Left the house', level: 'crit' },
    { id: 'fall',     label: 'Fall',      level: 'crit' },
    { id: 'unsteady', label: 'Unsteady gait', level: 'warn' },
    { id: 'night',    label: 'Night activity', level: 'warn' },
    { id: 'long',     label: 'Long inactivity', level: 'warn' },
    { id: 'toilet',   label: 'Toilet',       level: 'ok' },
    { id: 'sleep',    label: 'Sleep',          level: 'ok' },
    { id: 'meal',     label: 'Eating or drinking',   level: 'ok' },
    { id: 'meds',     label: 'Medication',    level: 'ok' },
    { id: 'quiet',    label: 'Quiet',     level: 'ok' }
  ];
  var LVL = {
    crit: { t: 'Alert',  c: 'crit' },
    warn: { t: 'Attention', c: 'warn' },
    ok:   { t: 'Normal',    c: 'ok' }
  };
  var kindById = {};
  function indexKinds() {
    kindById = {};
    KINDS.forEach(function (k) { kindById[k.id] = k; });
  }
  indexKinds();

  /* Улики приходят парами {k, v}, где k — уже готовая русская подпись
     («маршрут», «камера», «опознан»). Раньше здесь стоял словарь перевода
     с английских ключей — он не совпадал с сервером, и подписи пропадали,
     а список рисовался голыми значениями. */

  /* ------------------------------------------------------------- состояние */
  var state = {
    authed: false,
    user: null,
    q: '', from: '', to: '',
    kinds: {},                 // id -> true
    cursor: null,              // курсор следующей порции
    total: 0,
    items: [],
    loading: false,
    /* Карточки, которые сейчас пересматриваются. Это и есть то самое
       временное хранилище: id кладётся сюда при нажатии и убирается, когда
       сервер сказал «готово». Долгого хранения не нужно — очередь живёт на
       сервере, в таблице recheck_queue, и переживает перезагрузку страницы:
       при следующей загрузке ленты сервер сам пришлёт recheck у карточки. */
    rechecking: {}
  };

  /* ----------------------------------------------------------- обращения */
  var $ = function (s) { return document.querySelector(s); };
  var feed = $('#feed'), counter = $('#counter'), empty = $('#empty');
  var moreBtn = $('#more'), moreN = $('#moreN'), endMsg = $('#end');
  var dlg = $('#auth'), faultBox = $('#fault');

  var fmtDay = new Intl.DateTimeFormat('en-GB', { day: 'numeric', month: 'long', year: 'numeric' });
  var fmtTime = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit' });

  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;')
      .replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }
  function ms(iso) { return iso ? Date.parse(iso) : null; }

  /* Страница состояния живёт на основном сайте, в демо её нет. */
  var STATUS_HREF = 'https://engineeragainstdementia.net/status/';

  /* Сервер демо отдаёт ссылки на кадры уже с префиксом /demo
     (/demo/api/events/...). Склеивать его с BASE второй раз нельзя —
     вышло бы /demo/demo/api/... */
  function mediaUrl(p) {
    if (!p) return '';
    if (/^https?:/i.test(p)) return p;
    // Путь от корня (/demo/api/...) считаем от адреса сервера API, а не от
    // BASE целиком: и когда страница лежит на том же сервере (BASE=/demo),
    // и когда на сайте, а API на другом домене (BASE=https://api…/demo).
    try { return new URL(p, new URL(API.base || '/', location.href)).href; }
    catch (e) { return p; }
  }

  /* В демо «сколько минут назад» не имеет смысла: события старые.
     Показываем дату и время последнего события. */
  function seenText(iso) {
    var t = ms(iso);
    if (!t) return '';
    return dayLabel(t) + ', ' + fmtTime.format(new Date(t));
  }

  function sameDay(a, b) {
    var x = new Date(a), y = new Date(b);
    return x.getFullYear() === y.getFullYear() && x.getMonth() === y.getMonth()
        && x.getDate() === y.getDate();
  }
  function human(min) {
    if (min < 1) return 'under a minute';
    if (min < 60) return min + ' min';
    var h = Math.floor(min / 60), m = min % 60;
    return h + ' h' + (m ? ' ' + m + ' min' : '');
  }
  function dayLabel(t) {
    var now = new Date(), d = new Date(t);
    if (sameDay(now, d)) return 'Today';
    if (sameDay(now - 864e5, d)) return 'Yesterday';
    return fmtDay.format(d);
  }

  /* Время записи. Момент — одна отметка. Отрезок — от и до. Незакончившееся
     событие показываем как «с 20:41 · идёт», а не подставляем текущее время
     в конец: оно ещё не наступило. */
  function whenHTML(e) {
    var a = ms(e.startedAt), b = ms(e.endedAt);
    var from = fmtTime.format(new Date(a));
    var mins = Math.round((e.durationSec || 0) / 60);

    if (e.ongoing) {
      var going = Math.max(1, Math.round((Date.now() - a) / 60000));
      return '<time class="entry__time" datetime="' + esc(e.startedAt) + '">from ' + from +
        '</time><span class="dur dur--live"><i aria-hidden="true"></i>ongoing ' +
        human(going) + '</span>';
    }
    if (!b || mins < 1) {
      return '<time class="entry__time" datetime="' + esc(e.startedAt) + '">' +
             from + '</time>';
    }
    return '<time class="entry__time" datetime="' + esc(e.startedAt) + '">' + from +
      '</time><span class="entry__dash" aria-hidden="true">–</span>' +
      '<time class="entry__time entry__time--to" datetime="' + esc(e.endedAt) + '">' +
      fmtTime.format(new Date(b)) + '</time>' +
      '<span class="dur">' + human(mins) + '</span>' +
      /* Сервер закрывает зависшие эпизоды по таймауту. Молчать об этом
         нельзя: «6 ч 00 мин» ровно — это не измерение, а предел. */
      (e.closedBy === 'timeout'
        ? '<span class="tag-cap" title="The event did not end on its own; ' +
          'the server closed it at the maximum duration">timed out</span>' : '');
  }

  /* ---------------------------------------------------------------- отрисовка */
  function mediaHTML(e) {
    // Гостю сервер поля media не присылает вовсе — доставать нечего.
    if (!state.authed || !e.media) {
      return '<div class="shot shot--locked" aria-hidden="true">' +
             '<span class="shot__grain"></span></div>' +
             '<span class="shot__lock" title="Available after signing in">' +
             '<svg viewBox="0 0 24 24" aria-hidden="true">' +
             '<path d="M7 10V7a5 5 0 0 1 10 0v3" fill="none" stroke="currentColor" ' +
             'stroke-width="1.8" stroke-linecap="round"/>' +
             '<rect x="4.5" y="10" width="15" height="10.5" rx="2.6" fill="currentColor"/>' +
             '</svg></span>';
    }
    var m = e.media;
    var cam = evValue(e, 'camera') || '';
    var inner = '<span class="shot__grain"></span>' +
      (cam ? '<span class="shot__cam">' + esc(cam) + '</span>' : '');

    if (m.poster) {
      /* Кадр отдаёт сам API по сессии. Кука SameSite=Lax уходит и на
         поддомен: api.<домен> и сам домен — один сайт, поэтому картинка
         грузится обычным <img> без ухищрений.
         onerror нужен на случай истёкшей сессии: вместо битой иконки
         показываем ту же рамку, что видит гость. */
      inner += '<img class="shot__img" src="' + esc(mediaUrl(m.poster)) +
        '" alt="Event frame" loading="lazy" decoding="async" ' +
        'onerror="this.remove()">';
    }
    if (m.type === 'video' && (m.preview || m.clip)) {
      /* Кнопка, а не ссылка в новую вкладку. Вкладка отдавала браузеру
         голый файл с камеры: с уличной камеры это HEVC, которого
         десктопный Chrome не умеет вовсе, да ещё до ста мегабайт. */
      inner += '<button class="shot__play" type="button" data-play ' +
        'title="Watch here">▶</button>' +
        '<span class="shot__dur">video</span>';
    } else if (m.type === 'photo') {
      inner += '<span class="shot__dur">frame</span>';
    } else {
      inner += '<span class="shot__dur">no frame</span>';
    }
    return '<div class="shot">' + inner + '</div>';
  }

  function mb(bytes) {
    if (!bytes) return '';
    return bytes >= 1e9 ? (bytes / 1e9).toFixed(1) + ' GB'
                        : bytes < 1e7 ? (bytes / 1e6).toFixed(1) + ' MB'
                        : Math.round(bytes / 1e6) + ' MB';
  }

  /* Плеер встаёт на место кадра по нажатию, а не грузится вместе с лентой:
     двадцать карточек с preload='auto' — это двадцать запросов на видео
     сразу при открытии журнала.

     Играем ЛЁГКУЮ копию (m.preview): H.264, узкая, без звука, в 10-15 раз
     меньше оригинала. Оригинал остаётся ссылкой под кадром — он с камеры
     как есть, в полном разрешении.

     Если лёгкой копии нет (событие снято до 2.3.2), деваться некуда — даём
     оригинал и честно пишем, сколько он весит. Он ещё и может не открыться:
     HEVC десктопные браузеры не декодируют. Поэтому у video висит onerror,
     который вместо чёрного прямоугольника скажет, что случилось. */
  function videoHTML(e) {
    var m = e.media || {};
    var src = m.preview || m.clip;
    return '<video class="vid" controls autoplay playsinline preload="auto"' +
      (m.poster ? ' poster="' + esc(mediaUrl(m.poster)) + '"' : '') +
      ' src="' + esc(mediaUrl(src)) + '"' +
      ' onerror="this.closest(\'.entry__media\').classList.add(\'is-broken\')">' +
      '</video>' +
      '<p class="vid__no">Your browser cannot play this recording — it is ' +
      'HEVC straight from the camera. Download the original with the link below the frame.</p>';
  }

  /* Строка под кадром: чем именно вы сейчас смотрите и где взять оригинал. */
  function origHTML(e) {
    var m = e.media || {};
    if (!state.authed || !m.clip) return '';
    var size = mb(m.clipBytes);
    return '<p class="entry__orig">' +
      (m.preview ? '<span class="entry__orig-l">light copy</span> · ' : '') +
      '<a href="' + esc(mediaUrl(m.clip)) + '" target="_blank" rel="noopener">' +
      'original' + (size ? ' ' + size : '') + '</a></p>';
  }

  /* ------- чистка записи, пришедшей с сервера -------

     Служебная сводка разбора («диван | сидит | сидит, опираясь на
     подлокотник…») собрана кодом из маршрута, позы и отметок шагов. В
     ленте её нет вовсе; она нужна при разметке и живёт под раскрытием,
     рядом с ответами шагов.

     Сервер до 2.3.1 отдельного поля summary не знал и клал сводку в text.
     Сайт обновляется раньше сервера и не должен от него зависеть, поэтому
     разбираем это здесь: строка с палками — это сводка, где бы она ни
     пришла. */
  function tidy(e) {
    if (e && !e.summary && e.text && e.text.indexOf(' | ') > 0) {
      e.summary = e.text;
    }
    return e;
  }

  function evValue(e, key) {
    var list = e.evidence || [];
    for (var i = 0; i < list.length; i++) if (list[i].k === key) return list[i].v;
    return null;
  }

  function evidenceHTML(e) {
    if (!e.evidence || !e.evidence.length) return '';
    return '<ul class="ev">' + e.evidence.map(function (x) {
      return '<li><b>' + esc(x.k) + ':</b> ' + esc(x.v) + '</li>';
    }).join('') + '</ul>';
  }

  /* ====================================================== ответы шагов

     Разбор идёт цепочкой отдельных запросов к модели, и каждый вопрос
     задаётся только когда для него есть повод. Поэтому пустое поле здесь
     значит «такой вопрос не задавали», и это НЕ то же самое, что «нет».
     Разница видна глазом: спросили и получили «нет» — тег приглушённый;
     не спрашивали — тега нет вовсе. Без этого нельзя понять, модель
     ошиблась или её просто не спросили. */

  function fact(cls, label, value) {
    return '<span class="fact fact--' + cls + '">' +
           '<b>' + esc(label) + '</b>' + esc(value) + '</span>';
  }

  function factsHTML(e) {
    var f = e.facts;
    if (!f) return '';
    var out = [];

    /* Положений за событие может быть несколько: за восемнадцать минут
       человек успевает пройти, посидеть и снова встать. Показываем их
       цепочкой, «на чём» приписываем к последнему — оно про то, где он
       остался. */
    var poses = f.postures && f.postures.length ? f.postures
              : (f.posture ? [f.posture] : []);
    if (poses.length) {
      /* Без «на чём». Шаг позы про мебель больше не спрашивают: он был
         обязан что-то ответить и выдумывал — человек полулежал на кровати,
         а в карточке стояло «сидит на стуле». На чём он, говорит плашка
         падения, и только когда это важно: пол или кровать. */
      var line = poses.join(' → ');
      /* Тревожным делает плашку только то, в чём он ОСТАЛСЯ. «Лежит» в
         середине — это он прилёг на диван и встал, и подсвечивать это как
         тревогу значит кричать на каждом отдыхе. */
      out.push(fact(f.posture === 'lying' ? 'warn' : 'on',
                    poses.length > 1 ? 'postures' : 'posture', line));
    }
    if (f.gait) out.push(fact('on', 'gait', f.gait));
    if (f.gaitSupport && !/^(none|nobody|nothing|no)$/i.test(f.gaitSupport)) {
      out.push(fact('on', 'support', f.gaitSupport));
    }
    if (f.gaitRisk && f.gaitRisk !== 'normal') out.push(fact('warn', 'risk', f.gaitRisk));

    /* Спит сидя — не то же, что спит в постели: так засыпают от усталости
       или когда нехорошо. Молча писать «спит» здесь нельзя.

       И «лежит, не спит» — тоже отдельное состояние, а не отсутствие сна.
       У сидящего «не спит» ожидаемо и висит приглушённым; у лежащего,
       который шевелит руками и поворачивается, это как раз то, что стоит
       заметить, и плашка показывается в полную силу. */
    if (f.sleeping === true) {
      out.push(fact('on', 'sleep', f.posture === 'sitting' ? 'asleep sitting up' : 'asleep'));
    } else if (f.sleeping === false) {
      var awake = f.posture === 'lying' && f.sleepMoves === 'moving';
      out.push(fact(awake ? 'on' : 'off', 'sleep',
                    awake ? 'lying, awake' : 'awake'));
    }

    /* Падение — один вопрос с двумя ответами. Ни поверхности, ни своей
       оценки опасности модель больше не даёт: на чём он оказался, она
       решает внутри себя, а наружу выдаёт только «упал» или «не упал». */
    if (f.fallen === true) out.push(fact('bad', 'fall', 'fell'));
    else if (f.fallen === false) out.push(fact('off', 'fall', 'no fall'));

    if (f.toilet) out.push(fact('on', 'toilet', f.toilet));

    /* Еда, питьё и лекарства — три ответа одного шага. Порознь они дали бы
       три приглушённых тега «не ест», «не пьёт», «без лекарств» почти на
       каждой карточке. Поэтому положительные показываем по отдельности, а
       сплошное «нет» сворачиваем в один тег. */
    var meal = [];
    if (f.eating === true) meal.push('eating');
    if (f.drinking === true) meal.push('drinking');
    if (f.meds === true) meal.push('medication');
    if (meal.length) out.push(fact('on', 'food', meal.join(', ')));
    else if (f.eating === false || f.drinking === false) {
      out.push(fact('off', 'food', 'not eating or drinking'));
    }

    return out.length ? '<p class="facts">' + out.join('') + '</p>' : '';
  }

  /* Почему выставлен именно такой уровень. Это вывод кода по ответам шагов,
     а не мнение модели, — поэтому строка отдельная, а не среди улик.

     Класс называется entry__why, а не why: журнал грузит и style.css главной,
     где .why — экран во всю высоту окна. Короткое имя совпадало, и строка
     раздувалась в пустой блок на пол-экрана. */
  function reasonHTML(e) {
    if (!e.dangerReason) return '';
    var warn = e.level === 'crit' || e.level === 'warn';
    return '<p class="entry__why entry__why--' + (warn ? 'warn' : 'ok') + '">' +
      '<b>' + (warn ? 'Why the alert:' : 'Assessment:') + '</b> ' +
      esc(e.dangerReason) + '</p>';
  }

  /* ---- ответ одного шага человеческой строкой ----
     Показывать сырой JSON нельзя: размечать по нему невозможно. Каждый шаг
     знает, как рассказать о себе; незнакомый шаг (появится в будущем)
     раскладывается парами ключ-значение, а не пропадает молча. */
  function yn(v, yes, no) { return v === true ? yes : v === false ? no : null; }

  function answerText(step) {
    var a = step.answer || {}, p = [];
    switch (step.step) {
      case 'validate':
        p.push(yn(a.subject_found, 'it is him', 'not him'));
        p.push(a.match);
        if (a.people_in_frame != null) p.push('people in frame: ' + a.people_in_frame);
        p.push(a.quality);
        break;
      case 'gait':
        p.push(yn(a.walks, 'walking', 'standing still'));
        p.push(a.gait);
        if (a.support && !/^(none|nobody|nothing|no)$/i.test(a.support)) p.push('support: ' + a.support);
        if (a.risk && a.risk !== 'normal') p.push('risk: ' + a.risk);
        break;
      case 'posture':
        var seen = (a.postures && a.postures.length) ? a.postures
                 : (a.posture ? [a.posture] : []);
        if (seen.length) p.push(seen.join(' → '));
        p.push(a.sure);
        break;
      case 'sleep':
        p.push(yn(a.sleeping, 'asleep', 'awake'));
        p.push(a.sure);
        if (a.moves) p.push(a.moves);
        if (a.eyes) p.push('eyes ' + a.eyes);
        if (a.head && a.head !== 'not visible') p.push('head ' + a.head);
        if (a.covered) p.push(a.covered);
        break;
      case 'fall':
        p.push(yn(a.fallen, 'fell', 'no fall'));
        break;
      case 'meal':
        var m = [];
        if (a.eating) m.push('eating');
        if (a.drinking) m.push('drinking');
        if (a.meds) m.push('medication');
        p.push(m.length ? m.join(', ') : 'not eating or drinking');
        if (a.items && a.items.length) p.push('sees: ' + a.items.join(', '));
        break;
      case 'toilet_in':
        p.push(yn(a.entered, 'went into the toilet', 'did not go in'));
        p.push(a.sure);
        break;
      case 'toilet_out':
        p.push(yn(a.exited, 'came out of the toilet', 'did not come out'));
        if (a.steady) p.push('steadiness: ' + a.steady);
        p.push(a.sure);
        break;
      default:
        Object.keys(a).forEach(function (k) {
          var v = a[k];
          if (v != null && v !== '' && typeof v !== 'object') p.push(k + ': ' + v);
        });
    }
    return p.filter(Boolean).join(' · ');
  }

  /* Полный разбор под кнопкой. <details> взят намеренно: браузер сам делает
     раскрытие, клавиатуру и доступность — руками это пишется хуже.

     Здесь же служебная сводка (маршрут | поза | отметки шагов | действие).
     Наверху карточки ей не место: там человек читает, что произошло, а не
     разбирает машинную строку с палками. Раскрыл разбор — увидел её целиком. */
  function chainHTML(e) {
    if (!state.authed) return '';
    var chain = e.chain || [];
    if (!chain.length && !e.summary) return '';
    var sum = e.summary
      ? '<p class="qa__sum">' + esc(e.summary) + '</p>' : '';
    var rows = chain.map(function (s) {
      if (s.failed) {
        return '<li class="qa__row qa__row--fail"><b>' + esc(s.title) + '</b>' +
               '<span class="qa__ans">no answer</span></li>';
      }
      var note = (s.answer && s.answer.note) ? s.answer.note : '';
      return '<li class="qa__row"><b>' + esc(s.title) + '</b>' +
        '<span class="qa__cost">' + (s.seconds != null ? s.seconds + ' s' : '') +
        (s.frames ? ' · ' + s.frames + (s.frames === 1 ? ' frame' : ' frames') : '') + '</span>' +
        '<span class="qa__ans">' + esc(answerText(s)) + '</span>' +
        (note ? '<span class="qa__note">' + esc(note) + '</span>' : '') +
        '</li>';
    }).join('');
    var n = e.stepsAsked || chain.length;
    return '<details class="qa"><summary>What the model was asked' +
      ' <span class="qa__n">' + n + '</span></summary>' + sum +
      (rows ? '<ol class="qa__list">' + rows + '</ol>' : '') +
      '</details>';
  }

  /* Кнопка «Переоценить». Это не оценка, а запрос на повторный разбор
     записи моделью, поэтому она стоит отдельно от пальцев и не снимается
     повторным нажатием: задача уже ушла в очередь. */
  function redoHTML(e) {
    var busy = e.recheck && e.recheck.state === 'queued';
    return '<button class="redo" data-recheck' + (busy ? ' disabled' : '') + '>' +
      '<svg viewBox="0 0 24 24" aria-hidden="true">' +
      '<path d="M20 12a8 8 0 1 1-2.3-5.6" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round"/>' +
      '<path d="M20 3.5V9h-5.5" fill="none" stroke="currentColor" ' +
      'stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      '<span>' + (busy ? 'Re-checking' : 'Re-check') + '</span></button>';
  }

  /* Демо только для чтения: сервер на любую запись отвечает 403, поэтому
     оценку, переоценку и удаление не рисуем вовсе. */
  var READ_ONLY = true;

  function voteHTML(e) {
    if (!state.authed || READ_ONLY) return '';
    return '<div class="acts">' +
      '<div class="vote" role="group" aria-label="Rate this entry">' +
      '<button class="vote__b" data-vote="1" aria-pressed="' + (e.vote === 1) + '" ' +
        'title="Correct detection">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 22V10l5-8 1.4.7c.7.4 1 1.2.8 2L13 10h5.7c1.3 0 2.3 1.2 2 2.5l-1.6 7c-.2 1.4-1.4 2.5-2.9 2.5H7Z"/><rect x="2" y="10" width="3.4" height="12" rx="1.2"/></svg>' +
        '<span>Correct</span></button>' +
      '<button class="vote__b" data-vote="-1" aria-pressed="' + (e.vote === -1) + '" ' +
        'title="False alarm">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M17 2v12l-5 8-1.4-.7c-.7-.4-1-1.2-.8-2L11 14H5.3C4 14 3 12.8 3.3 11.5l1.6-7C5.1 3.1 6.3 2 7.8 2H17Z"/><rect x="18.6" y="2" width="3.4" height="12" rx="1.2"/></svg>' +
        '<span>False</span></button>' +
      '</div>' + redoHTML(e) +
      '<button class="del" data-del title="Delete entry">' +
        '<svg viewBox="0 0 24 24" aria-hidden="true">' +
        '<path d="M4 7h16M10 4h4M9 7v12M15 7v12M6 7l1 13.2A2 2 0 0 0 9 22h6' +
        'a2 2 0 0 0 2-1.8L18 7" fill="none" stroke="currentColor" ' +
        'stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
        '<span>Delete</span></button>' +
      '</div>' +
      /* Подтверждение прямо в карточке, а не отдельным окном: список
         длинный, и модалка отрывает от места, где ты был. */
      '<div class="confirm" hidden>' +
        '<p class="confirm__t">Delete this entry? The frame and video go with it.</p>' +
        '<span class="confirm__go">' +
          '<button class="btn btn--danger btn--sm" data-del-yes>Delete</button>' +
          '<button class="btn btn--ghost btn--sm" data-del-no>Cancel</button>' +
        '</span>' +
      '</div>';
  }

  /* ------- карточка на время переоценки -------

     Пока модель пересматривает запись, показывать старый разбор нельзя: он
     как раз тот, который человек назвал неверным. Но и убирать карточку
     из ленты тоже — она мигнёт и вернётся на другом месте.

     Поэтому карточка сворачивается: время и вид записи остаются, всё
     остальное уступает место полосе с крутилкой. Видно, что дело идёт и
     где именно оно идёт. */
  function busyHTML(e, withDay) {
    var kinds = (e.kinds && e.kinds.length ? e.kinds : (e.kind ? [e.kind] : []))
      .map(function (id) { return (kindById[id] || { label: id }).label; });
    var running = e.recheck && e.recheck.state === 'running';
    return '<li class="entry is-rechecking" data-id="' + esc(e.id) + '">' +
      (withDay ? '<p class="entry__day">' + dayLabel(ms(e.startedAt)) + '</p>' : '') +
      '<article class="entry__in entry__in--busy">' +
        '<span class="rechk__spin" aria-hidden="true"></span>' +
        '<div class="rechk">' +
          '<p class="rechk__top">' + whenHTML(e) +
            kinds.map(function (l) {
              return '<span class="kind">' + esc(l) + '</span>';
            }).join('') +
          '</p>' +
          '<p class="rechk__t">' +
            (running ? '<b>The model is re-checking this entry.</b> It takes about a minute.'
                     : '<b>Entry queued for re-check.</b> Starting in a few seconds.') +
          '</p>' +
        '</div>' +
      '</article>' +
    '</li>';
  }

  function entryHTML(e, withDay) {
    if (state.rechecking[e.id]) return busyHTML(e, withDay);
    /* Видов записи может быть несколько: дед вышел из туалета и тут же сел
       есть — это и «туалет», и «приём пищи». Показываем все, в порядке
       важности, как их прислал сервер. Старый сервер шлёт одно поле kind —
       тогда список из него и получается. */
    var kinds = (e.kinds && e.kinds.length ? e.kinds : (e.kind ? [e.kind] : []))
      .map(function (id) { return (kindById[id] || { label: id }).label; });
    var l = LVL[e.level] || LVL.ok;
    return '<li class="entry' + (e.ongoing ? ' entry--live' : '') +
      '" data-id="' + esc(e.id) + '">' +
      (withDay ? '<p class="entry__day">' + dayLabel(ms(e.startedAt)) + '</p>' : '') +
      '<article class="entry__in">' +
        '<div class="entry__left">' +
          '<div class="entry__media">' + mediaHTML(e) + '</div>' +
          origHTML(e) +
        '</div>' +
        '<div class="entry__body">' +
          '<p class="entry__top">' +
            whenHTML(e) +
            '<span class="lvl lvl--' + l.c + '">' + l.t + '</span>' +
            kinds.map(function (label) {
              return '<span class="kind">' + esc(label) + '</span>';
            }).join('') +
            (e.recheck && e.recheck.state === 'queued'
              ? '<span class="tag-redo">re-checking</span>' : '') +
          '</p>' +
          factsHTML(e) +
          reasonHTML(e) +
          evidenceHTML(e) +
          chainHTML(e) +
          voteHTML(e) +
        '</div>' +
      '</article>' +
    '</li>';
  }

  function render() {
    var html = '', prevDay = null;
    state.items.forEach(function (e) {
      var d = dayLabel(ms(e.startedAt));
      html += entryHTML(e, d !== prevDay);
      prevDay = d;
    });
    feed.innerHTML = html;

    /* «из 12» показываем только когда фильтров нет: сервер считает total
       по всей таблице, без учёта условий, и рядом с отфильтрованным
       списком это число означало бы не то, что читается. */
    counter.textContent = state.items.length
      ? 'Showing ' + state.items.length +
        (state.total && !filtersOn() ? ' of ' + state.total : '')
      : '';
    empty.hidden = state.items.length !== 0 || state.loading;

    moreBtn.hidden = !state.cursor;
    moreN.textContent = '';
    endMsg.hidden = !(state.items.length > PAGE && !state.cursor);

    paintChrome();
  }

  /* Обвязка страницы — кто вошёл, что ему доступно. Отдельно от render(),
     потому что нужна и когда ленты нет вовсе: при сбое шапка не должна
     врать, будто человек вышел из системы. */
  function paintChrome() {
    document.documentElement.classList.toggle('authed', state.authed);
    if (nowBox && !state.authed) { nowBox.hidden = true; nowData = null; }
    $('#lockbar').hidden = state.authed;
    $('#signin').hidden = state.authed;
    $('#signout').hidden = !state.authed || READ_ONLY;
    var who = document.querySelector('.who');
    who.hidden = !state.authed;
    if (state.authed) {
      who.querySelector('.who__name').textContent =
        (state.user && state.user.name) || 'owner';
    }
    // Поля фильтров гостю не просто прикрыты накладкой, но и выключены:
    // иначе до них можно добраться табом.
    document.querySelectorAll('#filters input, #filters button').forEach(function (el) {
      el.disabled = !state.authed;
    });
  }

  /* ================================================ где он сейчас

     Журнал отвечает на вопрос «что было». Открывают его обычно ради
     другого — «где он сейчас». Поэтому сверху отдельная панель: последнее
     ПОДТВЕРЖДЁННОЕ появление человека в кадре.

     Данные берутся из памяти системы (точка /api/last_seen). Она живёт
     отдельно от журнала: одна строка, всегда перезаписываемая, обновляется
     только когда модель подтвердила, что на кадрах именно он. Событие, где
     его не нашли, память не трогает — иначе «где он» врало бы каждый раз,
     когда мимо камеры прошёл кто-то другой.

     Панель не опрашивает сервер часто: строка меняется не чаще, чем
     человек переходит из комнаты в комнату. Раз в минуту пересчитываем
     «сколько минут назад» на месте, а к серверу ходим раз в пять минут и
     при появлении новых записей. */

  var nowBox = $('#now');
  var nowData = null;

  function agoText(min) {
    if (min == null) return '';
    if (min < 1) return 'just now';
    if (min < 60) return min + ' min ago';
    var h = Math.floor(min / 60);
    if (h < 24) return h + ' h ago';
    var days = Math.floor(h / 24);
    return days + (days === 1 ? ' day ago' : ' days ago');
  }

  function paintNow() {
    if (!nowBox) return;
    // Гостю панель не показываем вовсе: сервер ему данных и не отдаёт.
    if (!state.authed || !nowData || !nowData.known) { nowBox.hidden = true; return; }
    var d = nowData;
    var l = LVL[d.level] || LVL.ok;
    var shot = d.media && d.media.poster
      ? '<img class="now__img" src="' + esc(mediaUrl(d.media.poster)) +
        '" alt="" loading="lazy" decoding="async" onerror="this.remove()">'
      : '';
    var line = [d.where, d.posture].filter(Boolean).join(', ');
    var extra = [];
    if (d.sleeping) extra.push('asleep');
    if (d.toilet) extra.push('toilet: ' + d.toilet);

    nowBox.innerHTML =
      '<div class="now__shot">' + shot + '</div>' +
      '<div class="now__body">' +
        '<p class="now__eyebrow">' + (d.demo ? 'Last seen' : 'Where he is now') +
          '<span class="lvl lvl--' + l.c + '">' + l.t + '</span></p>' +
        '<p class="now__line">' + esc(line || 'outside the zones') +
          '<span class="now__ago">' + esc(d.agoMinutes != null ? agoText(d.agoMinutes) : seenText(d.seenAt)) + '</span></p>' +
        (extra.length ? '<p class="now__extra">' + esc(extra.join(' · ')) + '</p>' : '') +
      '</div>';
    nowBox.className = 'now now--' + l.c;
    nowBox.hidden = false;
  }

  function loadNow() {
    if (!state.authed) { if (nowBox) nowBox.hidden = true; return Promise.resolve(); }
    // tries:1 — панель второстепенна. Не ответила, значит её просто нет;
    // поднимать из-за неё карточку сбоя поверх живой ленты незачем.
    return API.get('/api/last_seen', { tries: 1 }).then(function (r) {
      nowData = r.data; paintNow();
    }, function () { /* молчим: лента важнее */ });
  }

  /* ============================================================== загрузка */

  function filtersOn() {
    return !!(state.q || state.from || state.to ||
      Object.keys(state.kinds).some(function (k) { return state.kinds[k]; }));
  }

  function query(cursor) {
    var p = [];
    if (state.q) p.push('q=' + encodeURIComponent(state.q));
    if (state.from) p.push('from=' + state.from);
    if (state.to) p.push('to=' + state.to);
    var on = Object.keys(state.kinds).filter(function (k) { return state.kinds[k]; });
    if (on.length) p.push('kind=' + on.join(','));
    if (cursor) p.push('cursor=' + encodeURIComponent(cursor));
    p.push('limit=' + PAGE);
    return '/api/events?' + p.join('&');
  }

  var waitEl = null, slowTimer = null;
  function waitOn(text) {
    waitOff();
    waitEl = document.createElement('p');
    waitEl.className = 'jrn__wait';
    waitEl.textContent = text;
    feed.parentNode.insertBefore(waitEl, feed);
    // Пустой экран на десять секунд — сам по себе плохая новость.
    slowTimer = setTimeout(function () {
      if (!waitEl) return;
      waitEl.textContent = 'The house is taking longer than usual to answer. Still waiting…';
      waitEl.classList.add('is-long');
    }, 4000);
  }
  function waitOff() {
    clearTimeout(slowTimer);
    if (waitEl && waitEl.parentNode) waitEl.parentNode.removeChild(waitEl);
    waitEl = null;
  }

  function load(more) {
    if (state.loading) return;
    state.loading = true;
    faultBox.hidden = true;
    faultBox.innerHTML = '';
    $('#filters').hidden = false;
    var note = document.querySelector('.wire-note');
    if (note) note.parentNode.removeChild(note);

    if (!more) { waitOn('Loading the journal…'); }
    else { moreBtn.disabled = true; moreBtn.textContent = 'Loading…'; }

    /* ДОБОР СТРАНИЦЫ.
       Сервер отбирает записи по типу уже ПОСЛЕ того, как взял из базы
       свои LIMIT строк. Поэтому при включённом фильтре страница может
       вернуться почти пустой, хотя подходящие записи есть — просто они
       ниже по времени. Тянем дальше по курсору, пока не наберём экран.
       Ограничение на число доборов обязательно: если под фильтр не
       попадает ничего, без него мы прочитали бы всю базу подряд.
       Правильное лечение — фильтровать в SQL, это на стороне сервера. */
    var collected = [], hops = 0, MAX_HOPS = 4;
    var startCursor = more ? state.cursor : null;
    var slowest = 0;

    function pull(cursor) {
      return API.getShaped(query(cursor), ['items']).then(function (r) {
        slowest = Math.max(slowest, r.ms);
        collected = collected.concat(r.data.items || []);
        state.cursor = r.data.nextCursor || null;
        state.total = r.data.total || 0;
        if (collected.length < PAGE && state.cursor && hops < MAX_HOPS) {
          hops++;
          return pull(state.cursor);
        }
        return r;
      });
    }

    return pull(startCursor)
      .then(function (r) {
        state.loading = false;
        waitOff();
        moreBtn.disabled = false;
        moreBtn.innerHTML = 'Show more <span id="moreN"></span>';
        moreN = $('#moreN');

        collected = collected.map(tidy);
        state.items = more ? state.items.concat(collected) : collected;
        render();
        resumeRechecks();
        if (!more) loadNow();
        if (slowest > 2500) slowNote(slowest);
      })
      .catch(function (err) {
        state.loading = false;
        waitOff();
        moreBtn.disabled = false;
        return showFault(err);
      });
  }

  /* Ответ дошёл, но еле-еле. Это не поломка, а предупреждение: так
     выглядит связь с домом за минуту до того, как она отвалится. */
  function slowNote(t) {
    var n = document.createElement('div');
    n.className = 'wire-note';
    n.innerHTML = '<b>The link to the house is slow.</b> The answer took ' +
      (t / 1000).toFixed(1) + ' s. The entries are here, but frames may ' +
      'take a while to load. <a href="' + STATUS_HREF + '">Check the chain</a>';
    feed.parentNode.insertBefore(n, feed);
  }

  function showFault(err) {
    return API.diagnose(err).then(function (d) {
      feed.innerHTML = '';
      counter.textContent = '';
      empty.hidden = true;
      moreBtn.hidden = true;
      endMsg.hidden = true;
      $('#filters').hidden = true;      // фильтровать нечего
      paintChrome();
      Fault.mount(faultBox, d, {
        statusHref: STATUS_HREF,
        onRetry: function () { load(false); },
        onSignin: openAuth
      });
    });
  }

  /* ------------------------------------------------------------- фильтры */
  var chipbox = $('#kinds');
  function paintChips() {
    chipbox.innerHTML = KINDS.map(function (k) {
      return '<button class="chip" type="button" data-kind="' + k.id + '" ' +
             'aria-pressed="' + (state.kinds[k.id] ? 'true' : 'false') + '">' +
             esc(k.label) + '</button>';
    }).join('');
    paintChrome();
  }
  paintChips();

  chipbox.addEventListener('click', function (e) {
    var b = e.target.closest('.chip');
    if (!b || b.disabled) return;
    var id = b.dataset.kind;
    state.kinds[id] = !state.kinds[id];
    b.setAttribute('aria-pressed', String(!!state.kinds[id]));
    load(false);
  });

  var typing = null;
  $('#q').addEventListener('input', function (e) {
    clearTimeout(typing);
    var v = e.target.value;
    // Каждое нажатие — запрос к дому через туннель. Ждём паузу в наборе.
    typing = setTimeout(function () { state.q = v.trim(); load(false); }, 350);
  });
  $('#from').addEventListener('change', function (e) {
    state.from = e.target.value; load(false);
  });
  $('#to').addEventListener('change', function (e) {
    state.to = e.target.value; load(false);
  });
  $('#reset').addEventListener('click', function () {
    state.q = ''; state.from = ''; state.to = ''; state.kinds = {};
    $('#q').value = ''; $('#from').value = ''; $('#to').value = '';
    chipbox.querySelectorAll('.chip').forEach(function (c) {
      c.setAttribute('aria-pressed', 'false');
    });
    load(false);
  });

  moreBtn.addEventListener('click', function () { load(true); });

  /* ================================================== действия над записью

     Цена ошибки здесь другая, чем при загрузке. Не пришла лента — экран
     пустой, это видно. А если не прошло удаление, но запись исчезла с
     экрана, человек уверен, что дело сделано, и узнает правду через сутки.
     Поэтому оценка ставится сразу и откатывается при отказе, а удаление
     сначала подтверждает сервер. */

  function byId(id) {
    for (var i = 0; i < state.items.length; i++) {
      if (state.items[i].id === id) return state.items[i];
    }
    return null;
  }

  function entryErr(li, err, what) {
    API.diagnose(err).then(function (d) {
      var old = li.querySelector('.enerr');
      if (old) old.parentNode.removeChild(old);
      var p = document.createElement('p');
      p.className = 'enerr';
      p.setAttribute('role', 'alert');
      p.innerHTML = '<b>' + what + '.</b> ' + esc(d.title) +
        '. <a href="' + STATUS_HREF + '">What happened</a>';
      li.querySelector('.entry__body').appendChild(p);
      // Сессия кончилась — предлагаем вход прямо отсюда, не гоняя на статус.
      if (d.signin) {
        state.authed = false;
        paintChrome();
      }
    });
  }

  /* ---------------------------------------------------------- оценка записи */
  feed.addEventListener('click', function (e) {
    var b = e.target.closest('[data-vote]');
    if (!b) return;
    var li = b.closest('.entry');
    var ev = byId(li.dataset.id);
    if (!ev) return;
    var was = ev.vote;
    var v = Number(b.dataset.vote);
    ev.vote = ev.vote === v ? 0 : v;      // повторное нажатие снимает оценку

    var box = b.closest('.vote');
    function paint() {
      box.querySelectorAll('.vote__b').forEach(function (x) {
        x.setAttribute('aria-pressed', String(Number(x.dataset.vote) === ev.vote));
      });
    }
    paint();

    API.post('/api/events/' + ev.id + '/vote', { vote: ev.vote })
      .catch(function (err) {
        ev.vote = was;                    // сервер не принял — возвращаем как было
        paint();
        entryErr(li, err, 'The rating was not saved');
      });
  });

  /* ------------------------------------------------- запрос на переоценку
     Сервер принимает задачу и отвечает 202. Сам воркер переоценки пока
     стаб — значит запись пометится, но модель по ней ещё не прогонится.
     Врать кнопкой об этом не будем, когда воркер появится — ничего в
     разметке менять не придётся. */
  /* Нажали ▶ — кадр уступает место плееру. Обратно не возвращаем: человек
     нажал смотреть, а не «мигни видео». */
  feed.addEventListener('click', function (e) {
    var b = e.target.closest('[data-play]');
    if (!b) return;
    var li = b.closest('.entry');
    var ev = byId(li.dataset.id);
    if (!ev || !ev.media) return;
    var box = li.querySelector('.entry__media');
    // Класс на всей карточке, а не только на рамке: пока идёт запись,
    // колонка с кадром раздвигается — в 260 пикселей смотреть нечего.
    li.classList.add('is-playing');
    box.classList.add('is-playing');
    box.innerHTML = videoHTML(ev);
    var v = box.querySelector('video');
    // Автозапуск браузер может и не разрешить — тогда просто останется
    // плеер с кнопкой, это нормально и ломать ничего не должно.
    if (v && v.play) { var go = v.play(); if (go && go.catch) go.catch(function () {}); }
  });

  feed.addEventListener('click', function (e) {
    var b = e.target.closest('[data-recheck]');
    if (!b || b.disabled) return;
    var li = b.closest('.entry');
    var ev = byId(li.dataset.id);
    if (!ev) return;

    b.disabled = true;
    b.querySelector('span').textContent = 'Sending…';

    API.post('/api/events/' + ev.id + '/recheck').then(function (r) {
      ev.recheck = { state: (r.data && r.data.state) || 'queued' };
      startRecheck(ev.id);
    }, function (err) {
      // Задача не принята — кнопку возвращаем, иначе человек уверен,
      // что запись стоит в очереди, а её там нет.
      b.disabled = false;
      b.querySelector('span').textContent = 'Re-check';
      entryErr(li, err, 'The re-check request did not go through');
    });
  });

  /* ------- ход переоценки -------

     Спрашиваем сервер про КАЖДУЮ переоцениваемую запись отдельно, раз в три
     секунды. Не про ленту целиком: лента может быть длинной, а ждут обычно
     одну карточку, и человек смотрит именно на неё.

     Когда сервер отвечает «done», он присылает и свежую карточку — тем же
     ответом. Иначе после каждого «готово» пришлось бы ходить ещё раз за
     списком, и карточка лишнюю секунду висела бы свёрнутой уже без причины. */
  var RECHECK_POLL = 3000;

  function startRecheck(id) {
    if (state.rechecking[id]) return;
    state.rechecking[id] = { since: Date.now() };
    render();
    pollRecheck(id);
  }

  function stopRecheck(id) {
    delete state.rechecking[id];
  }

  function pollRecheck(id) {
    if (!state.rechecking[id]) return;
    API.get('/api/events/' + id + '/recheck', { tries: 1 }).then(function (r) {
      var d = r.data || {};
      var ev = byId(id);
      if (ev) ev.recheck = { state: d.state };

      if (d.state === 'done') {
        stopRecheck(id);
        if (d.event) {
          var at = state.items.findIndex(function (x) { return x.id === id; });
          if (at >= 0) state.items[at] = tidy(d.event);
        }
        render();
        return;
      }
      if (d.state === 'none') {        // очередь потеряна — не висим вечно
        stopRecheck(id);
        render();
        return;
      }
      render();
      setTimeout(function () { pollRecheck(id); }, RECHECK_POLL);
    }, function () {
      // Связь моргнула — не бросаем ожидание, пробуем ещё раз.
      setTimeout(function () { pollRecheck(id); }, RECHECK_POLL * 2);
    });
  }

  /* Переоценка могла начаться в прошлый заход и пережить перезагрузку
     страницы: очередь-то на сервере. Поднимаем ожидание по тому, что
     сервер сам сказал о карточке. */
  function resumeRechecks() {
    state.items.forEach(function (e) {
      var st = e.recheck && e.recheck.state;
      if (st === 'queued' || st === 'running') startRecheck(e.id);
    });
  }

  /* ------------------------------------------------------------- удаление
     Два шага намеренно: запись уносит с собой кадр и видео, а промах
     пальцем по списку — обычное дело. Сервер удаляет мягко и даёт десять
     секунд на возврат — ровно столько живёт полоса отмены. */
  var trash = null, trashTimer = null;
  var toast = $('#toast');

  function closeConfirm(li) {
    li.classList.remove('is-confirming');
    var c = li.querySelector('.confirm');
    if (c) c.hidden = true;
  }

  feed.addEventListener('click', function (e) {
    var li = e.target.closest && e.target.closest('.entry');
    if (!li) return;

    if (e.target.closest('[data-del]')) {
      li.classList.add('is-confirming');
      li.querySelector('.confirm').hidden = false;
      li.querySelector('[data-del-yes]').focus();
      return;
    }
    if (e.target.closest('[data-del-no]')) { closeConfirm(li); return; }

    var yes = e.target.closest('[data-del-yes]');
    if (!yes) return;

    var id = li.dataset.id;
    var idx = -1;
    for (var i = 0; i < state.items.length; i++) {
      if (state.items[i].id === id) { idx = i; break; }
    }
    if (idx < 0) return;

    yes.disabled = true;
    yes.textContent = 'Deleting…';

    API.del('/api/events/' + id).then(function (r) {
      trash = { at: idx, item: state.items[idx] };
      state.items.splice(idx, 1);
      if (state.total) state.total--;
      render();
      // Сколько именно даётся на возврат, решает сервер.
      var until = r.data && r.data.restorableUntil
        ? Math.max(3000, Date.parse(r.data.restorableUntil) - Date.now())
        : 10000;
      showToast(until);
    }, function (err) {
      yes.disabled = false;
      yes.textContent = 'Delete';
      entryErr(li, err, 'Could not delete');
    });
  });

  function showToast(life) {
    toast.querySelector('.toast__t').textContent = 'Entry deleted';
    var u = $('#undo');
    u.disabled = false;
    u.textContent = 'Undo';
    toast.hidden = false;
    toast.classList.remove('is-out');
    void toast.offsetWidth;              // перезапустить полоску времени
    toast.style.setProperty('--life', Math.round(life) + 'ms');
    toast.classList.add('is-out');
    clearTimeout(trashTimer);
    trashTimer = setTimeout(hideToast, life);
  }
  function hideToast() {
    toast.hidden = true;
    toast.classList.remove('is-out');
    trash = null;                        // вернуть уже нельзя
  }
  $('#undo').addEventListener('click', function () {
    if (!trash) return;
    var back = trash, btn = $('#undo');
    btn.disabled = true;
    btn.textContent = 'Restoring…';

    API.post('/api/events/' + back.item.id + '/restore').then(function (r) {
      state.items.splice(back.at, 0,
        tidy((r.data && r.data.id) ? r.data : back.item));
      if (state.total) state.total++;
      clearTimeout(trashTimer);
      hideToast();
      btn.disabled = false;
      btn.textContent = 'Undo';
      render();
    }, function () {
      /* Отсрочка на сервере могла истечь, или связь оборвалась. Молчать
         нельзя: человек нажал «вернуть» и уверен, что вернул. */
      btn.disabled = false;
      btn.textContent = 'Try again';
      toast.querySelector('.toast__t').textContent = 'Could not restore — no connection';
      clearTimeout(trashTimer);
      trashTimer = setTimeout(hideToast, 10000);
    });
  });

  // Esc закрывает подтверждение, если оно открыто
  document.addEventListener('keydown', function (e) {
    if (e.key !== 'Escape') return;
    var open = feed.querySelector('.entry.is-confirming');
    if (open) { closeConfirm(open); e.stopPropagation(); }
  });

  /* ================================================================== вход
     Сессия живёт в куке HttpOnly: страница её не видит и видеть не должна.
     Поэтому «вошёл ли я» — это не флаг в localStorage, а вопрос к серверу. */
  function openAuth() {
    $('#authErr').hidden = true;
    dlg.showModal();
    setTimeout(function () { $('#login').focus(); }, 30);
  }
  $('#signin').addEventListener('click', openAuth);
  $('#signin2').addEventListener('click', openAuth);
  $('#authCancel').addEventListener('click', function () { dlg.close('cancel'); });

  $('#authForm').addEventListener('submit', function (e) {
    e.preventDefault();                  // окно закроем сами, после ответа
    var err = $('#authErr');
    var login = $('#login').value.trim(), pass = $('#pass').value;
    if (!login || !pass) {
      err.textContent = 'Please fill in both fields.';
      err.hidden = false;
      return;
    }
    var go = $('#authForm button[value="ok"]');
    go.disabled = true;
    go.textContent = 'Checking…';

    API.post('/api/session', { login: login, password: pass }).then(function (r) {
      go.disabled = false; go.textContent = 'Sign in';
      var who = r.data || {};
      state.authed = true;
      // Вход отвечает тем же телом, что и проверка сессии: имя в name.
      state.user = who.user || (who.name ? { name: who.name } : null);
      $('#pass').value = '';
      dlg.close('ok');
      load(false);
    }, function (e2) {
      go.disabled = false; go.textContent = 'Sign in';
      if (e2.kind === 'http' && e2.status === 401) {
        // Сервер намеренно не говорит, что именно неверно.
        err.textContent = 'Wrong login or password.';
      } else if (e2.kind === 'http' && e2.status === 429) {
        err.textContent = 'Too many attempts. Please wait a quarter of an hour.';
      } else {
        err.textContent = 'The server is not answering. Check the connection to the house.';
      }
      err.hidden = false;
    });
  });

  $('#signout').addEventListener('click', function () {
    API.del('/api/session').then(function () {
      state.authed = false;
      state.user = null;
      load(false);
    }, function () {
      // Даже если запрос не дошёл — на экране больше ничего не показываем.
      state.authed = false;
      state.user = null;
      render();
    });
  });

  /* =================================================================== старт */
  function boot() {
    /* Кто мы. Ответ 401 здесь — не ошибка, а «гость»: показывать из-за
       него карточку сбоя было бы враньём.

       Смотрим на ПОЛЕ ответа, а не на код. Сервер отвечает 200 и гостю
       тоже — телом {"authed": false}. Раньше здесь стояло безусловное
       state.authed = true на любой успешный ответ, и сайт считал владельцем
       каждого, кто открыл страницу: рисовал «Выйти», включал фильтры и
       вешал на карточки кнопки голоса и удаления. Данных гость всё равно не
       получал — их не отдаёт сервер, — но кнопки были, и они молча не
       работали. */
    API.get('/api/session', { tries: 1 }).then(function (r) {
      var who = r.data || {};
      state.authed = !!who.authed;
      state.user = who.user || (who.name ? { name: who.name } : null);
    }, function () {
      state.authed = false;
    }).then(function () {
      paintChrome();
      // Справочник типов берём с сервера: там он один на всех.
      return API.get('/api/kinds', { tries: 1 }).then(function (r) {
        if (Array.isArray(r.data) && r.data.length) { KINDS = r.data; indexKinds(); }
      }, function () {});
    }).then(function () {
      paintChips();
      return load(false);
    });
  }
  boot();

  /* Связь вернулась сама — не заставляем нажимать кнопку. */
  window.addEventListener('online', function () {
    if (!faultBox.hidden) setTimeout(function () { load(false); }, 500);
  });

  /* У незакончившегося события счётчик должен идти сам, а не только при
     перерисовке. Обновляем раз в минуту и только его — трогать всю ленту
     ради одной строки незачем. */
  setInterval(function () {
    document.querySelectorAll('.entry--live .dur--live').forEach(function (el) {
      var li = el.closest('.entry');
      var ev = byId(li.dataset.id);
      if (!ev || !ev.ongoing) return;
      var m = Math.max(1, Math.round((Date.now() - ms(ev.startedAt)) / 60000));
      el.lastChild.textContent = 'ongoing ' + human(m);
    });
    // «12 минут назад» стареет само по себе — пересчитываем на месте,
    // не тревожа сервер.
    if (nowData && nowData.known && nowData.agoMinutes != null) {
      nowData.agoMinutes++;
      paintNow();
    }
  }, 60000);

  /* ------------------------------------------------- автообновление ленты

     Раз в минуту тихо переспрашиваем первую страницу и подменяем то, что
     изменилось. Тихо — значит без полосы «Загружаем…» и без прыжка
     прокрутки: человек читает ленту, а не ждёт её.

     ПОЧЕМУ НЕ ПРОСТО load(false). Перерисовка ленты рвёт всё, что человек
     в ней делает: играющее видео обрывается на середине, раскрытый разбор
     захлопывается, наполовину подтверждённое удаление отменяется. Поэтому
     обновление пропускается, пока на экране что-то происходит, и делается
     только тогда, когда действительно есть что менять.

     Живые события и так приходят потоком (EventSource) — это подстраховка
     на случай, когда поток отвалился и человек об этом не знает.

     ВЫКЛЮЧЕНО. Оно ломало просмотр записи, и защита ниже от этого НЕ
     спасала: busyOnScreen() проверяется ПЕРЕД сетевым запросом, а render()
     происходит ПОСЛЕ ответа. Между ними секунда-другая, и если нажать на
     видео в эту щель — ответ приходит и стирает плеер вместе с просмотром.
     Проверять надо второй раз, прямо перед перерисовкой, но пока решено
     просто не обновлять ленту само́й: живые события и так приходят потоком,
     а кнопка обновления страницы никуда не делась.

     Чтобы включить обратно — поставить сюда 60000. Код ниже рабочий и
     нетронутый, включая исправление, которого не хватало: повторную
     проверку busyOnScreen() перед render(). */
  var REFRESH = 0;      // 0 — не обновлять автоматически

  function busyOnScreen() {
    if (!state.authed || state.loading) return true;
    if (document.hidden) return true;                 // вкладка не смотрит
    if (feed.querySelector('video')) return true;     // идёт просмотр записи
    if (feed.querySelector('details.qa[open]')) return true;   // раскрыт разбор
    if (feed.querySelector('.entry.is-confirming')) return true;
    if (Object.keys(state.rechecking).length) return true;
    if (trash) return true;                           // висит полоса отмены
    return false;
  }

  /* Что считается изменением: другой набор записей, другой уровень, другой
     голос, другое состояние переоценки. Сравниваем отпечаток, а не объекты
     целиком: сервер может прислать те же данные другим порядком ключей. */
  function stamp(list) {
    return list.map(function (e) {
      return [e.id, e.level, (e.kinds || []).join(','), e.vote,
              e.recheck && e.recheck.state].join(':');
    }).join('|');
  }

  function quietRefresh() {
    if (busyOnScreen()) return;
    API.getShaped(query(null), ['items'], { tries: 1 }).then(function (r) {
      var fresh = (r.data.items || []).map(tidy);
      if (!fresh.length) return;
      // Хвост, догруженный кнопкой «показать ещё», сохраняем: обновляем
      // только голову ленты, ровно ту, что сервер сейчас и прислал.
      var known = {};
      fresh.forEach(function (e) { known[e.id] = true; });
      var tail = state.items.filter(function (e) { return !known[e.id]; });
      var merged = fresh.concat(tail);
      if (stamp(merged) === stamp(state.items)) return;   // ничего не поменялось
      // Вторая проверка, и она обязательна: пока шёл запрос, человек мог
      // начать смотреть запись или раскрыть разбор. Перерисовка в этот
      // момент обрывает и то, и другое.
      if (busyOnScreen()) return;
      state.items = merged;
      state.total = r.data.total || state.total;
      render();
      resumeRechecks();
    }, function () { /* не достучались — молчим, через минуту попробуем снова */ });
  }

  if (REFRESH) setInterval(quietRefresh, REFRESH);

  /* Свериться с сервером раз в пять минут: реже, чем человек ходит по дому,
     чаще нет смысла — строка меняется только с новым подтверждённым
     появлением, а о нём и так придёт событие в поток. */
  setInterval(loadNow, 300000);

  /* ------------------------------------------------------- живые события
     Сервер держит поток SSE и присылает новые записи по мере появления.
     Полностью перезагружать ленту не надо: показываем полосу «есть новое»,
     а вставку делает сам человек. Иначе список дёргается под пальцем, а
     если открыто подтверждение удаления — оно исчезает вместе с записью. */
  var fresh = 0, bar = null;
  function liveBar() {
    if (!bar) {
      bar = document.createElement('button');
      bar.type = 'button';
      bar.className = 'newbar';
      bar.addEventListener('click', function () {
        fresh = 0; bar.remove(); bar = null; load(false);
      });
      feed.parentNode.insertBefore(bar, feed);
    }
    bar.textContent = fresh === 1 ? '1 new entry — show'
                                  : fresh + ' new entries — show';
  }
  function listen() {
    if (!window.EventSource || API.simulated) return;
    var es;
    try { es = new EventSource(API.base + '/api/events/stream',
                              { withCredentials: true }); }
    catch (e) { return; }
    es.addEventListener('created', function () { fresh++; liveBar(); loadNow(); });
    // Обрыв EventSource переподключает сам; молчим, чтобы не пугать зря.
    es.onerror = function () {};
  }
  listen();
})();
