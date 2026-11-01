/* ==========================================================================
   BeKavod — разговор с сервером и, главное, разбор поломок.

   ЗАЧЕМ ЭТОТ ФАЙЛ. «Не работает» — бесполезное сообщение. Человеку, который
   в три часа ночи открыл журнал и увидел пустоту, нужно знать одно: это у
   него пропал интернет, или дома выключилась коробка, или сервер жив, но
   отвечает ошибкой. Это три разных действия, и страница обязана их
   различать сама, а не предлагать «попробуйте позже».

   ЧТО БРАУЗЕР ПОКАЗЫВАЕТ, А ЧТО ПРЯЧЕТ.
   fetch() при любой сетевой беде бросает один и тот же TypeError: «Failed
   to fetch». Он намеренно не говорит, что именно случилось — иначе через
   этот канал можно было бы сканировать чужую локальную сеть. Поэтому
   причину приходится вычислять пробами:

     1) navigator.onLine === false   — устройство само знает, что офлайн.
     2) запрос к СВОЕМУ origin       — жив ли вообще интернет прямо сейчас.
        Страница могла открыться двадцать минут назад, это ничего не значит.
     3) картинка с адреса API        — вот главный трюк. Загрузка <img> не
        подчиняется CORS. Если наш 1×1 PNG по адресу /api/ping.png
        загрузился, значит приложение на том конце ЖИВО и достижимо, а
        ответ не дошёл только из-за правил браузера (нет заголовков CORS
        или смешанный http/https). Если картинка не загрузилась — на том
        конце не отвечает ничего: коробка, туннель, DNS.
     4) время до отказа              — отказ за 60 мс это «дверь закрыта»
        (порт не слушает, имя не разрешилось). Отказ через восемь секунд —
        «пакеты уходят в никуда» (туннель висит, файрвол молча глотает).

   Чего мы честно НЕ различаем: сбитую DNS-запись от закрытого порта.
   Из браузера это неотличимо, и врать точностью хуже, чем назвать обе
   причины и дать команду для проверки каждой.

   ОТДЕЛЬНО ПРО CLOUDFLARE. Когда туннель лежит, ошибку рисует не наш
   сервер, а край Cloudflare (521/522/523/524, 530 для Argo 1033). На этих
   страницах нет заголовков CORS, поэтому при запросе с другого origin
   браузер превратит их в тот же TypeError, и код статуса мы не увидим.
   Увидим только если API отдаётся с того же origin, что и сайт. Поэтому
   проба картинкой важнее кода статуса: она работает в обоих случаях.

   ПРОВЕРКА АВАРИЙ. Поведение при поломке надо проверять заранее, а не
   «на глаз» в день аварии, когда чинить надо, а не смотреть. Любой
   сценарий включается адресом: ?fail=offline, ?fail=cors, ?fail=530 и так
   далее. Список открывается на /status/?dev=1.
   Записи при этом НИКОГДА не подделываются: подделывается только отказ.
   Если сценарий успешный, запрос уходит настоящему серверу.
   ========================================================================== */
(function () {
  'use strict';

  /* Адрес API. Пусто — тот же origin, что и страница. Меняется одной
     строкой в <head>:  <meta name="api-base" content="https://api.…"> */
  var meta = document.querySelector('meta[name="api-base"]');
  /* ?api=<адрес> — проверить страницу против другого сервера, не
     трогая разметку. */
  var qsBase = new URLSearchParams(location.search).get('api');
  var BASE = (qsBase || (meta && meta.content) || '').replace(/\/+$/, '');

  /* Ждать дольше семи секунд бессмысленно: человек уже решил, что сломалось.
     Две попытки, а не три — иначе перед сообщением о сбое проходит
     полминуты пустого экрана, и это хуже самого сбоя. */
  var TIMEOUT = { get: 7000, write: 12000 };
  var TRIES = 2;                 // всего попыток для безопасных запросов
  var PING = '/api/ping.png';    // 1×1 PNG без авторизации, см. docs/api-journal.md

  /* Всегда приводим к полному адресу: в отчёте о сбое «/api/health» ничего
     не говорит, а «https://api.…/api/health» сразу показывает, куда ушёл
     запрос — и что именно надо проверять. */
  function full(path) {
    if (/^https?:/i.test(path)) return path;
    try { return new URL(BASE + path, location.href).href; }
    catch (e) { return BASE + path; }
  }
  function apiOrigin() {
    try { return new URL(full('/api/health'), location.href).origin; }
    catch (e) { return location.origin; }
  }
  var t0 = (window.performance && performance.now)
    ? function () { return performance.now(); }
    : function () { return Date.now(); };

  /* ====================================================== имитация аварий */
  var SIM = (new URLSearchParams(location.search).get('fail') || '').toLowerCase();

  /* net: 'reject' — fetch бросит TypeError, 'hang' — не ответит вовсе.
     site/api — что покажут пробы. status — код ответа, если ответ дошёл. */
  var SCENES = {
    offline:  { title: 'Device is offline',        net: 'reject', online: false, site: false, api: false, ms: 20 },
    isp:      { title: 'Wi-Fi is up, but no internet', net: 'reject', site: false, api: false, ms: 2600 },
    refused:  { title: 'API address refuses at once', net: 'reject', site: true,  api: false, ms: 70 },
    hang:     { title: 'API address hangs',        net: 'reject', site: true,  api: false, ms: 5200 },
    timeout:  { title: 'No answer in time',        net: 'hang',   site: true,  api: false },
    cors:     { title: 'Server is alive, the browser blocked the answer', net: 'reject', site: true, api: true, ms: 190 },
    mixed:    { title: 'http API on an https page', net: 'reject', site: true, api: true, ms: 25, mixed: true },
    '401':    { title: 'Session expired',          status: 401 },
    '403':    { title: 'Access denied',            status: 403 },
    '404':    { title: 'Address not found',        status: 404 },
    '429':    { title: 'Too many requests',        status: 429, retryAfter: 34 },
    '500':    { title: 'Internal server error',    status: 500 },
    '502':    { title: 'Gateway could not reach the server', status: 502 },
    '503':    { title: 'Service is starting',      status: 503, retryAfter: 90 },
    '504':    { title: 'Gateway waited too long',  status: 504 },
    '530':    { title: 'Cloudflare tunnel is down', status: 530 },
    html:     { title: 'Something else is answering', status: 200, html: true },
    badjson:  { title: 'Answer cut off midway',    status: 200, badjson: true },
    version:  { title: 'Server is newer than the page', status: 200, shape: true },
    degraded: { title: 'API is alive, home nodes are not', status: 200, degraded: true },
    /* Отдельный и очень частый случай: читать можно, писать нельзя —
       диск кончился, база в режиме только чтения, очередь встала.
       Лента при этом открывается, а оценка и удаление молча не проходят,
       если не проверить ответ. Поэтому сценарий нужен свой. */
    write:    { title: 'Reads work, writes do not', status: 503,
                writeOnly: true, retryAfter: 20 },
    slow:     { title: 'Everything works, but slowly', status: 200, slowMs: 4200 }
  };
  var scene = SCENES[SIM] || null;

  function online() {
    if (scene && scene.online === false) return false;
    return navigator.onLine !== false;
  }

  /* ============================================================== пробы */

  /* Жив ли интернет ПРЯМО СЕЙЧАС: дёргаем свой же файл мимо кэша.
     Свой origin — значит без CORS и без сюрпризов. */
  function probeSite() {
    // В сценарии, где сервер ответил кодом, сеть заведомо цела: не идём
    // в неё, иначе на тестовом стенде шаг покажет отказ без причины.
    if (scene) return Promise.resolve(scene.site !== false);
    /* Страница открыта с диска: у file:// нет origin, тянуть нечего.
       Возвращаем «цело» — лучше не проверить, чем соврать про обрыв. */
    if (location.protocol === 'file:') return Promise.resolve(true);
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, 4000);
    return fetch(location.origin + '/robots.txt?b=' + Date.now(),
                 { cache: 'no-store', signal: ctl.signal })
      .then(function (r) { clearTimeout(timer); return r.ok || r.status === 404; })
      .catch(function () { clearTimeout(timer); return false; });
  }

  /* Отвечает ли что-нибудь по адресу API. Картинкой — потому что <img>
     не проверяется на CORS, и это единственный способ из браузера
     отличить «сервер жив, но заголовки не те» от «там никого нет». */
  function probeApi() {
    if (scene) return Promise.resolve(scene.api !== false);
    return new Promise(function (done) {
      var img = new Image();
      var timer = setTimeout(function () { img.src = ''; done(false); }, 4000);
      img.onload  = function () { clearTimeout(timer); done(true); };
      img.onerror = function () { clearTimeout(timer); done(false); };
      img.src = full(PING) + '?b=' + Date.now();
    });
  }

  /* Смешанное содержимое видно без всяких проб: https-страница не имеет
     права ходить на http-адрес, браузер режет запрос до отправки. */
  function mixedContent() {
    if (scene && scene.mixed) return true;
    return location.protocol === 'https:' && /^http:/i.test(BASE);
  }

  /* ============================================================ запросы */

  function ApiError(kind, extra) {
    var e = new Error(kind);
    e.kind = kind;                       // network | timeout | http | parse | shape
    for (var k in extra) if (extra.hasOwnProperty(k)) e[k] = extra[k];
    return e;
  }

  function retryAfterMs(h) {
    if (!h) return null;
    var n = Number(h);
    if (!isNaN(n)) return n * 1000;
    var d = Date.parse(h);
    return isNaN(d) ? null : Math.max(0, d - Date.now());
  }

  /* Правдоподобное тело ответа для имитации. Здоровье системы отдаём
     покомпонентно — иначе на странице состояния нечего показывать. */
  function fakeBody(path) {
    if (path.indexOf('/api/health') !== -1) {
      var bad = !!(scene && scene.degraded);
      return JSON.stringify({
        ok: !bad,
        version: '1.4.2',
        time: new Date().toISOString(),
        uptimeSec: bad ? 214 : 396120,
        parts: [
          { id: 'db', name: 'Event database', ok: true, ms: 3 },
          { id: 'mqtt', name: 'MQTT · Mosquitto', ok: !bad, ms: bad ? null : 11,
            why: bad ? 'the broker does not accept connections' : null,
            since: bad ? new Date(Date.now() - 26 * 60000).toISOString() : null },
          { id: 'frigate', name: 'Frigate NVR', ok: !bad, ms: bad ? null : 42,
            why: bad ? 'does not answer /api/version' : null,
            since: bad ? new Date(Date.now() - 24 * 60000).toISOString() : null },
          { id: 'cams', name: 'Cameras', ok: true, note: '2 of 2 streaming' },
          { id: 'radar', name: 'Fall radars', ok: true, note: '2 of 2 online' },
          { id: 'media', name: 'Frame storage', ok: true, note: '118 GB free' }
        ]
      });
    }
    /* Записи НЕ подделываются никогда. Если сценарий не про ошибку, а про
       медленный или частично живой сервер — запрос уходит настоящему
       серверу, и в ленте настоящие события. Выдуманная запись в журнале
       ухода за человеком опаснее любого сбоя: по ней можно принять
       решение, которого ничто в жизни не оправдывает. */
    return null;
  }

  /* Подделка ответа для ?fail=… Возвращает Promise как настоящий fetch,
     включая отказы, чтобы дальше по коду ничего не ветвилось. */
  function simulate(method, path, ms) {
    if (!scene) return null;
    var wait = scene.ms || 60;

    // Сценарий «только чтение»: GET идёт настоящему серверу.
    if (scene.writeOnly && method === 'GET') return null;

    if (scene.net === 'hang') {
      /* Соединение приняли и молчат. В жизни такой запрос убивает наш
         же таймер, поэтому и здесь отвечаем ровно тем, чем ответил бы
         AbortController — иначе имитация вела бы себя мягче реальности. */
      return new Promise(function (_, no) {
        setTimeout(function () {
          var e = new Error('The operation was aborted');
          e.name = 'AbortError';
          no(e);
        }, ms);
      });
    }
    if (scene.net === 'reject') {
      return new Promise(function (_, no) {
        setTimeout(function () {
          no(new TypeError('Failed to fetch'));    // ровно то, что даёт браузер
        }, Math.min(wait, ms - 100));
      });
    }
    if (scene.status) {
      var body = scene.html
            ? '<!doctype html><title>502 Bad Gateway</title><h1>502 Bad Gateway</h1>'
        : scene.badjson ? '{"items":[{"id":"e1042","ts":'
        : scene.shape   ? '{"data":{"records":[]},"apiVersion":"3.0"}'
        : scene.status >= 400
            ? JSON.stringify({ error: {
                code: ({ 401: 'session_expired', 403: 'forbidden', 404: 'not_found',
                         429: 'rate_limited', 500: 'internal', 502: 'upstream_down',
                         503: 'starting', 504: 'upstream_timeout',
                         530: 'tunnel_down' })[scene.status] || 'error',
                message: scene.title,
                requestId: 'r_sim_' + Math.random().toString(36).slice(2, 10) } })
            : fakeBody(path);
      /* Успешный сценарий, а подделывать нечего — значит и не надо:
         пропускаем запрос настоящему серверу. */
      if (body === null && scene.status < 400) return null;

      var head = { 'content-type': scene.html ? 'text/html' : 'application/json' };
      if (scene.retryAfter) head['retry-after'] = String(scene.retryAfter);

      var delay = scene.slowMs || wait;
      return new Promise(function (ok) {
        setTimeout(function () {
          ok(new Response(body, { status: scene.status, headers: head }));
        }, delay);
      });
    }
    return null;
  }

  function once(method, path, body, timeout) {
    var started = t0();
    var ctl = new AbortController();
    var timer = setTimeout(function () { ctl.abort(); }, timeout);

    var opts = {
      method: method,
      signal: ctl.signal,
      credentials: 'include',            // сессия живёт в HttpOnly-куке
      cache: 'no-store',
      headers: { 'accept': 'application/json' }
    };
    if (body !== undefined && body !== null) {
      opts.headers['content-type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }

    var call = simulate(method, path, timeout) || fetch(full(path), opts);

    return call.then(function (res) {
      clearTimeout(timer);
      var ms = Math.round(t0() - started);
      var rid = res.headers.get('x-request-id') || null;

      return res.text().then(function (text) {
        var ct = res.headers.get('content-type') || '';
        var data = null, broken = false;
        if (text) {
          try { data = JSON.parse(text); } catch (e) { broken = true; }
        }

        if (!res.ok) {
          throw ApiError('http', {
            status: res.status, statusText: res.statusText, ms: ms,
            url: full(path), method: method, requestId: (data && data.error &&
              data.error.requestId) || rid,
            server: data && data.error ? data.error : null,
            retryAfter: retryAfterMs(res.headers.get('retry-after')),
            html: /text\/html/i.test(ct)
          });
        }
        /* Ответ 200, но не JSON — почти всегда значит, что до нас отвечает
           кто-то другой: заглушка хостинга, страница ошибки, или гостевой
           Wi-Fi, который подсовывает свою страницу входа. */
        if (broken || (text && !/json/i.test(ct))) {
          throw ApiError('parse', {
            status: res.status, ms: ms, url: full(path), method: method,
            requestId: rid, html: /text\/html|<html/i.test(ct + text.slice(0, 80)),
            sample: text.slice(0, 140)
          });
        }
        return { data: data, ms: ms, requestId: rid, status: res.status };
      });
    }, function (err) {
      clearTimeout(timer);
      var ms = Math.round(t0() - started);
      throw ApiError(err && err.name === 'AbortError' ? 'timeout' : 'network', {
        ms: ms, url: full(path), method: method, raw: String(err && err.message || err)
      });
    });
  }

  /* Повторяем только то, что безопасно повторять: GET и явные «зайди
     позже» (429/503). Повторить POST на удаление — значит удалить дважды. */
  function canRetry(err, method) {
    if (method !== 'GET') return false;
    if (err.kind === 'network' || err.kind === 'timeout') return true;
    if (err.kind !== 'http') return false;
    return err.status === 429 || err.status === 502 ||
           err.status === 503 || err.status === 504;
  }

  function send(method, path, body, opt) {
    opt = opt || {};
    var timeout = opt.timeout || (method === 'GET' ? TIMEOUT.get : TIMEOUT.write);
    var left = opt.tries || (method === 'GET' ? TRIES : 1);

    function attempt(n) {
      return once(method, path, body, timeout).catch(function (err) {
        if (n >= left || !canRetry(err, method)) throw err;
        /* Пауза растёт, плюс разброс: если связь оборвалась у всех сразу,
           не надо всем возвращаться в одну и ту же миллисекунду. */
        var wait = err.retryAfter && err.retryAfter < 10000
          ? err.retryAfter
          : (n === 1 ? 400 : 1400) + Math.random() * 300;
        return new Promise(function (r) { setTimeout(r, wait); }).then(function () {
          return attempt(n + 1);
        });
      });
    }
    return attempt(1);
  }

  /* ========================================================== диагноз */

  /* Каждый диагноз отвечает на три вопроса и ни на один лишний:
       что сломалось · почему именно так решили · что делать.
     hint — для любого человека, ops — команды для владельца системы,
     их показываем только ему. */
  function D(o) {
    o.hint = o.hint || [];
    o.ops = o.ops || [];
    return o;
  }

  var HTTP = {
    400: function (e) { return D({
      code: 'app.bad_request', where: 'app', level: 'warn', retry: false,
      title: 'The server did not understand the request',
      why: 'The page sent data in a shape the server does not expect. ' +
           'This usually happens when one side was updated without the other.',
      hint: ['Reload the page bypassing the cache — Ctrl+Shift+R.'] }); },
    401: function (e) { return D({
      code: 'auth.expired', where: 'auth', level: 'warn', retry: false, signin: true,
      title: 'Your sign-in is no longer valid',
      why: 'The server answers and everything is intact — the session simply ' +
           'ended. That is by design: the journal should not stay open forever.',
      hint: ['Sign in again — the entries are still there.'] }); },
    403: function (e) {
      // Демо отвечает 403 {"error": "demo is read-only"} на любую запись.
      var srv = e && e.server;
      var ro = !!srv && /read-only/i.test(typeof srv === 'string' ? srv : (srv.message || ''));
      return D({
      code: 'auth.forbidden', where: 'auth', level: 'warn', retry: false,
      title: ro ? 'This demo is read-only' : 'This journal is not yours',
      why: ro ? 'The demo journal can be viewed, but not changed.'
              : 'You are signed in, but this account has no access to this house.',
      hint: ro ? [] : ['Check which phone number or email you signed in with.'] }); },
    404: function (e) { return D({
      code: 'app.route', where: 'app', level: 'warn', retry: false,
      title: 'The server answers, but it has no such address',
      why: 'We reached the application, but the API section we need is not there. ' +
           'So the page and the server are different versions, or the request went to the wrong address.',
      hint: ['Reload the page.'] }); },
    408: function () { return HTTP[504](); },
    429: function (e) { return D({
      code: 'app.rate', where: 'app', level: 'warn', retry: true,
      title: 'Too many requests in a row',
      why: 'The server is alive and is slowing down on purpose — protection against brute force. ' +
           (e.retryAfter ? 'It asks to wait ' +
              Math.ceil(e.retryAfter / 1000) + ' s.' : ''),
      hint: ['Wait half a minute and try again.'] }); },
    500: function (e) { return D({
      code: 'server.internal', where: 'server', level: 'crit', retry: true,
      title: 'The server hit an internal error',
      why: 'The connection is fine and the request arrived. The handler itself crashed — ' +
           'this is not on your side, it is on the system side.',
      hint: ['Try again. If it keeps happening, report the request ID below.'] }); },
    502: function (e) { return D({
      code: 'link.gateway', where: 'link', level: 'crit', retry: true,
      title: 'The gateway is up, but the server behind it did not answer',
      why: 'We got to the front door, but the room behind it is empty: the application ' +
           'is not running or has crashed, while the tunnel/proxy is still up.',
      hint: ['Check that the box at home is switched on.'] }); },
    503: function (e) { return D({
      code: 'server.starting', where: 'server', level: 'warn', retry: true,
      title: 'The service is still starting',
      why: 'The server says honestly that it is not ready to take requests right now. ' +
           'This happens in the first minute after a restart or during maintenance.' +
           (e.retryAfter ? ' It asks to come back in ' +
              Math.ceil(e.retryAfter / 1000) + ' s.' : ''),
      hint: ['Wait a minute and reload the page.'] }); },
    504: function (e) { return D({
      code: 'link.gwtimeout', where: 'link', level: 'crit', retry: true,
      title: 'The gateway waited for the house and gave up',
      why: 'The request reached Cloudflare, which knocked on the house and got no ' +
           'answer in time. The box is either fully loaded or its connection is ' +
           'barely alive.',
      hint: ['Try again in a minute.'] })}
  };
  function cloudflare(e) {
    var map = {
      520: 'the Cloudflare edge got a garbled answer from the house',
      521: 'the house refused the connection — the process is not listening on the port',
      522: 'the house did not answer the connection in time',
      523: 'the house is unreachable — usually a broken origin DNS record',
      524: 'the house accepted the connection but sent no answer',
      525: 'the TLS handshake with the house failed',
      530: 'tunnel not found (Argo 1033) — cloudflared is not connected'
    };
    return D({
      code: 'link.cloudflare', where: 'link', level: 'crit', retry: true,
      title: 'Cloudflare is up, the house is not',
      why: 'The error was drawn by the Cloudflare edge, not by our server: ' +
           (map[e.status] || 'the link to the origin is broken') + '. So the domain ' +
           'is alive, the internet is alive, but the box in Yavne could not be reached.',
      hint: ['It looks like the house lost power or internet.'] });
  }

  function fromHttp(e) {
    if (e.status >= 520 && e.status <= 530) return cloudflare(e);
    var f = HTTP[e.status];
    if (f) return f(e);
    if (e.status >= 500) return HTTP[500](e);
    return D({
      code: 'app.http', where: 'app', level: 'warn', retry: e.status !== 400,
      title: 'The server answered with error ' + e.status,
      why: (e.server && e.server.message) || 'The server gave no reason.',
      hint: ['Try the request again.'] });
  }

  /* Разбор сетевого отказа — тот самый случай, когда браузер молчит. */
  function fromNetwork(e) {
    if (mixedContent()) {
      return Promise.resolve(D({
        code: 'net.mixed', where: 'app', level: 'crit', retry: false,
        title: 'The page is https, but the API is http',
        why: 'The browser blocks such a request before sending it — and rightly ' +
             'so: a half-encrypted connection protects nothing.',
        hint: ['This is a site configuration error, not yours.'] }));
    }
    if (!online()) {
      return Promise.resolve(D({
        code: 'net.offline', where: 'device', level: 'warn', retry: true,
        title: 'This device lost its internet connection',
        why: 'The device itself reports that there is no network. The house and the server ' +
             'may be perfectly fine.',
        hint: ['Check Wi-Fi or mobile data.',
               'The page will reload by itself as soon as the connection is back.'] }));
    }
    return probeSite().then(function (siteOk) {
      if (!siteOk) {
        return D({
          code: 'net.dead', where: 'device', level: 'warn', retry: true,
          title: 'No connection to anything',
          why: 'The network icon is on, but even the site itself does not open. This ' +
               'is what Wi-Fi without internet looks like: the access point is there, nothing beyond it.',
          hint: ['Switch to mobile data and try again.',
                 'If you are in a cafe or a hotel, you may need to accept their ' +
                 'terms on the sign-in page.'] });
      }
      return probeApi().then(function (apiOk) {
        if (apiOk) {
          /* Картинка с того же адреса загрузилась. Приложение живо и
             достижимо — значит ответ зарубил сам браузер. */
          return D({
            code: 'net.cors', where: 'app', level: 'crit', retry: false,
            title: 'The server answers, but the browser blocked the answer',
            why: 'The test image from the API address loaded — so the ' +
                 'application is running and reachable. But the browser dropped the regular ' +
                 'request: the answer carries no permission for the domain ' +
                 location.hostname + '. This is a server setting, not a breakdown.',
            hint: ['Nothing can be done about it on the user side.'] });
        }
        var fast = e.kind !== 'timeout' && e.ms < 800;
        return D({
          code: fast ? 'link.refused' : 'link.silent',
          where: 'link', level: 'crit', retry: true,
          title: fast ? 'Nobody answers at the API address'
                      : 'The request went nowhere and never came back',
          why: fast
            ? 'There is internet and the site opens, but the address ' + apiOrigin() +
              ' refused at once, in ' + e.ms + ' ms. An instant refusal means ' +
              'one of two things: the name did not resolve to an address, or it did, ' +
              'but nothing is listening on the port.'
            : 'There is internet and the site opens, but ' + apiOrigin() +
              ' gave no answer for ' + Math.round(e.ms / 1000) + ' s. That is ' +
              'not "switched off" but "stuck": the connection is accepted and ' +
              'then silence, or a firewall along the way swallows the packets.',
          hint: ['Most likely the house lost power or internet.',
                 'If the house is fine, restart the box.'] });
      });
    });
  }

  function fromParse(e) {
    if (e.html) {
      return D({
        code: 'app.notours', where: 'link', level: 'crit', retry: true,
        title: 'Something other than our system is answering at this address',
        why: 'Instead of data, an ordinary web page arrived. That is how a ' +
             'hosting placeholder, someone else\'s error page or guest Wi-Fi answers — ' +
             'the kind that intercepts everything and shows its own sign-in.',
        hint: ['If you are in a cafe, hotel or airport, open any website and ' +
               'accept the terms of their network.'] });
    }
    return D({
      code: 'app.truncated', where: 'server', level: 'crit', retry: true,
      title: 'The answer arrived cut off',
      why: 'The data started arriving and broke off midway — the JSON never ' +
           'reached its end. Usually this is the connection dropping mid-transfer ' +
           'or a server killed mid-sentence.',
      hint: ['Try again — it usually works the second time.'] });
  }

  function fromShape(e) {
    return D({
      code: 'app.shape', where: 'app', level: 'warn', retry: false,
      title: 'The server speaks a different language',
      why: 'The answer arrived whole and as valid JSON, but the fields the page ' +
           'expects are not in it. So the server was updated and the page was not ' +
           '(or the other way round).',
      hint: ['Reload the page bypassing the cache — Ctrl+Shift+R.'] });
  }

  /* Единая точка: на входе ошибка, на выходе — обещание диагноза.
     Обещание, потому что часть выводов требует проб по сети. */
  function diagnose(err) {
    var e = err || {};
    var base;
    if (e.kind === 'http')   base = Promise.resolve(fromHttp(e));
    else if (e.kind === 'parse') base = Promise.resolve(fromParse(e));
    else if (e.kind === 'shape') base = Promise.resolve(fromShape(e));
    else base = fromNetwork(e);

    return base.then(function (d) {
      d.tech = {
        url: e.url || null,
        method: e.method || null,
        status: e.status || null,
        ms: e.ms != null ? e.ms : null,
        requestId: e.requestId || null,
        kind: e.kind || 'network',
        raw: e.raw || (e.server && e.server.code) || null,
        when: new Date().toISOString(),
        page: location.href,
        agent: navigator.userAgent
      };
      return d;
    });
  }

  /* ======================================================= внешний вид */
  window.API = {
    base: BASE,
    origin: apiOrigin,
    scenes: SCENES,
    /* Порядок для показа: от «это у вас» к «это у нас», а не как ключи
       легли в объекте — там числовые имена всегда всплывают наверх. */
    sceneOrder: ['offline', 'isp', 'refused', 'hang', 'timeout', 'cors', 'mixed',
                 '502', '504', '530', '503', '500', '429', '401', '403', '404',
                 'html', 'badjson', 'version', 'write', 'degraded', 'slow'],
    sim: SIM,
    simulated: !!scene,
    ping: PING,

    get:  function (p, o) { return send('GET', p, null, o); },
    post: function (p, b, o) { return send('POST', p, b || {}, o); },
    del:  function (p, o) { return send('DELETE', p, null, o); },

    /* Запрос с проверкой формы ответа: пусть лучше страница честно
       скажет «версии разъехались», чем нарисует пустую ленту. */
    getShaped: function (p, need, o) {
      return send('GET', p, null, o).then(function (r) {
        for (var i = 0; i < need.length; i++) {
          if (!(need[i] in (r.data || {}))) {
            throw ApiError('shape', { url: full(p), method: 'GET',
              ms: r.ms, requestId: r.requestId, status: r.status });
          }
        }
        return r;
      });
    },

    probeSite: probeSite,
    probeApi: probeApi,
    mixedContent: mixedContent,
    online: online,
    diagnose: diagnose,
    error: ApiError
  };
})();
