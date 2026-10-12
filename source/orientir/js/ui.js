(function (root) {
  'use strict';

  const el = () => root.Dom.el;

  const MONTHS_OF = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
    'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

  const state = {
    data: null,
    sphereId: null,
    report: false,
    nextOpen: false,
    dayOpen: false,
    month: null,
    day: null,
    focus: null,
    storage: null
  };

  /* ── даты человеческим языком ───────────────────────────────── */

  function toUTC(date) {
    return Date.UTC(Number(date.slice(0, 4)), Number(date.slice(5, 7)) - 1, Number(date.slice(8, 10)));
  }

  function daysBetween(from, to) {
    return Math.round((toUTC(to) - toUTC(from)) / 86400000);
  }

  function shortDate(date) {
    return Number(date.slice(8, 10)) + ' ' + MONTHS_OF[Number(date.slice(5, 7)) - 1].slice(0, 3) + '.';
  }

  function longDate(date) {
    return Number(date.slice(8, 10)) + ' ' + MONTHS_OF[Number(date.slice(5, 7)) - 1];
  }

  function humanDue(due, today) {
    const diff = daysBetween(today, due);
    if (diff === 0) return { text: 'сегодня', late: false, soon: true };
    if (diff === 1) return { text: 'завтра', late: false, soon: true };
    if (diff === -1) return { text: 'вчера', late: true, soon: false };
    if (diff < 0) {
      const n = Math.abs(diff);
      if (n < 8) return { text: n + ' ' + root.Stats.plural(n, ['день', 'дня', 'дней']) + ' назад', late: true, soon: false };
      return { text: longDate(due), late: true, soon: false };
    }
    if (diff < 8) return { text: 'через ' + diff + ' ' + root.Stats.plural(diff, ['день', 'дня', 'дней']), late: false, soon: true };
    return { text: longDate(due), late: false, soon: false };
  }

  function shortRange(from, to) {
    const месяцОдин = from.slice(0, 7) === to.slice(0, 7);
    const начало = месяцОдин
      ? String(Number(from.slice(8, 10)))
      : shortDate(from);
    return начало + '–' + shortDate(to);
  }

  /* срок одной датой или период двумя */
  function humanSpan(task, today) {
    if (!task.due) return { text: 'поставить срок', late: false, soon: false };
    if (!task.start) return humanDue(task.due, today);
    return {
      text: shortRange(task.start, task.due),
      late: task.due < today,
      soon: task.start <= today && today <= task.due
    };
  }

  /* ── состояние ──────────────────────────────────────────────── */

  function update(next, focus, quiet) {
    state.data = next;
    state.focus = focus || null;
    root.Store.save(state.storage, state.data);
    render(quiet);
  }

  function open(sphereId) {
    state.sphereId = sphereId;
    state.report = false;
    state.dayOpen = false;
    state.focus = null;
    render();
  }

  function openReport() {
    state.sphereId = null;
    state.dayOpen = false;
    state.report = true;
    render();
  }

  function openDay(date) {
    state.sphereId = null;
    state.report = false;
    state.day = date;
    state.dayOpen = !!date;
    render();
  }

  function close() {
    state.sphereId = null;
    state.report = false;
    state.dayOpen = false;
    render();
  }

  /* ── отрисовка ──────────────────────────────────────────────── */

  function render(skipPanel) {
    renderNext();
    renderDocks();
    if (!skipPanel) renderPanel();
    root.Calendar.render({
      data: state.data,
      month: state.month,
      day: state.day,
      pick: date => openDay(date)
    });
    if (root.City) root.City.update(state.data, state.sphereId, state.report || state.dayOpen);
    state.focus = null;
  }

  function groupOf(task, today) {
    if (task.due < today) return 'Просрочено';
    if (task.due === today) return 'Сегодня';
    if (task.due === root.Stats.plus(today)) return 'Завтра';
    return 'Дальше';
  }

  function renderNext() {
    const e = el();
    const host = document.getElementById('soon');
    root.Dom.clear(host);

    const today = root.Model.today();
    const list = root.Stats.upcoming(state.data, today, 7);
    if (list.length === 0) {
      state.nextOpen = false;
      return;
    }

    const first = list[0];
    const said = humanSpan(first, today);
    const sphere = root.Model.sphere(first.sphereId);

    const line = e('button', {
      class: 'next' + (said.late ? ' late' : ''),
      type: 'button',
      style: '--sphere:' + sphere.color,
      'aria-expanded': state.nextOpen ? 'true' : 'false',
      onclick: ev => {
        ev.stopPropagation();
        state.nextOpen = !state.nextOpen;
        render(true);
      }
    }, [
      e('i', {}),
      e('b', { text: first.title }),
      e('em', { text: said.text }),
      list.length > 1 ? e('span', { class: 'more', text: '+' + (list.length - 1) }) : null
    ]);

    host.appendChild(line);

    if (!state.nextOpen || list.length < 2) return;

    const pop = e('div', { class: 'next-pop', onclick: ev => ev.stopPropagation() });
    let group = null;

    for (const task of list) {
      const name = groupOf(task, today);
      if (name !== group) {
        group = name;
        pop.appendChild(e('p', { class: 'next-group', text: name }));
      }
      const item = humanSpan(task, today);
      const color = root.Model.sphere(task.sphereId).color;
      pop.appendChild(e('button', {
        class: 'next-item' + (item.late ? ' late' : ''),
        type: 'button',
        style: '--sphere:' + color,
        onclick: () => { state.nextOpen = false; open(task.sphereId); }
      }, [
        e('i', {}),
        e('b', { text: task.title }),
        e('em', { text: item.text })
      ]));
    }

    host.appendChild(pop);
  }

  function renderDocks() {
    const e = el();
    const host = document.getElementById('docks');
    root.Dom.clear(host);

    for (const sphere of root.Model.SPHERES) {
      host.appendChild(e('button', {
        class: 'dock',
        type: 'button',
        style: '--sphere:' + sphere.color,
        'aria-current': state.sphereId === sphere.id ? 'true' : 'false',
        onclick: () => (state.sphereId === sphere.id ? close() : open(sphere.id))
      }, [
        e('i', {}),
        e('b', { text: sphere.name }),
        e('span', { text: root.Stats.label(state.data, sphere.id) })
      ]));
    }
  }

  const NOTES = {
    work: 'Задачи и сроки. Башня показывает долю сделанного.',
    gym: 'Каждая тренировка — новый этаж. Этажи не пропадают.',
    social: 'Быстрые заметки для Threads и TikTok.',
    study: 'Домашка со сроками — она же попадает в календарь.',
    money: 'Траты, приходы и накопления.'
  };

  function renderPanel() {
    const panel = document.getElementById('panel');
    const body = document.getElementById('panel-body');

    if (state.dayOpen && state.day) {
      const today = root.Model.today();
      panel.hidden = false;
      panel.style.setProperty('--sphere', '#7c8cff');
      document.getElementById('panel-title').textContent = longDate(state.day);
      document.getElementById('panel-note').textContent =
        state.day === today ? 'Сегодня' : (state.day > today ? 'Впереди' : 'Прошедший день');
      root.Dom.clear(body);
      root.PanelDay.render(body, {
        data: state.data,
        date: state.day,
        future: state.day > today,
        update: update
      });
      return;
    }

    if (state.report) {
      panel.hidden = false;
      panel.style.setProperty('--sphere', '#7c8cff');
      document.getElementById('panel-title').textContent = 'Итоги';
      document.getElementById('panel-note').textContent = 'Что сделано за сегодня и за неделю.';
      root.Dom.clear(body);
      root.PanelReport.render(body, { data: state.data, update: update });
      return;
    }

    if (!state.sphereId) {
      panel.hidden = true;
      root.Dom.clear(body);
      return;
    }

    const sphere = root.Model.sphere(state.sphereId);
    panel.hidden = false;
    panel.style.setProperty('--sphere', sphere.color);
    document.getElementById('panel-title').textContent = sphere.name;
    document.getElementById('panel-note').textContent = NOTES[sphere.id] || '';

    root.Dom.clear(body);

    const ctx = {
      data: state.data,
      sphereId: sphere.id,
      sphere: sphere,
      focus: state.focus,
      update: update
    };

    if (sphere.kind === 'gym') root.PanelGym.render(body, ctx);
    else if (sphere.kind === 'money') root.PanelMoney.render(body, ctx);
    else root.PanelTasks.render(body, ctx);
  }

  /* ── сообщения ──────────────────────────────────────────────── */

  function notice(text, action) {
    const e = el();
    const host = document.getElementById('notice');
    root.Dom.clear(host);
    host.hidden = false;
    host.appendChild(e('span', { text: text }));
    if (action) host.appendChild(e('button', { class: 'link', type: 'button', text: action.text, onclick: action.run }));
    host.appendChild(e('button', {
      class: 'link', type: 'button', text: 'Скрыть',
      onclick: () => { host.hidden = true; }
    }));
  }

  /* ── тема ───────────────────────────────────────────────────── */

  const THEME_KEY = 'orientir-theme';

  function theme() {
    return document.documentElement.dataset.theme === 'dark' ? 'dark' : 'light';
  }

  function applyTheme(name) {
    document.documentElement.dataset.theme = name;
    document.getElementById('btn-theme').textContent = name === 'dark' ? 'День' : 'Ночь';
    try { state.storage.setItem(THEME_KEY, name); } catch (err) { /* переживём */ }
    if (root.City) root.City.setTheme();
  }

  /* ── файлы ──────────────────────────────────────────────────── */

  function download(text, name) {
    const blob = new Blob([text], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = name;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function wireTools(archive) {
    document.getElementById('btn-save').addEventListener('click', () => {
      download(root.Model.serialize(state.data), root.Store.exportName());
    });

    const file = document.getElementById('file');
    document.getElementById('btn-load').addEventListener('click', () => file.click());
    file.addEventListener('change', () => {
      const chosen = file.files && file.files[0];
      if (!chosen) return;
      const reader = new FileReader();
      reader.onload = () => {
        const res = root.Model.parse(String(reader.result));
        if (!res.ok) { notice('Файл не подошёл: ' + res.error); return; }
        update(res.data);
        notice('Данные загружены из файла.');
      };
      reader.readAsText(chosen);
      file.value = '';
    });

    document.getElementById('btn-theme').addEventListener('click', () => {
      applyTheme(theme() === 'dark' ? 'light' : 'dark');
    });

    document.getElementById('btn-report').addEventListener('click', () => {
      if (state.report) close(); else openReport();
    });

    document.getElementById('btn-calendar').addEventListener('click', () => {
      document.getElementById('calendar').classList.toggle('shown');
    });

    document.getElementById('panel-close').addEventListener('click', close);

    document.getElementById('cal-prev').addEventListener('click', () => {
      state.month = root.Stats.shiftMonth(state.month, -1);
      render();
    });
    document.getElementById('cal-next').addEventListener('click', () => {
      state.month = root.Stats.shiftMonth(state.month, 1);
      render();
    });

    document.addEventListener('click', () => {
      if (!state.nextOpen) return;
      state.nextOpen = false;
      render(true);
    });

    document.addEventListener('keydown', ev => {
      if (ev.key === 'Escape' && state.nextOpen) { state.nextOpen = false; render(true); return; }
      if (ev.key === 'Escape' && (state.sphereId || state.report || state.dayOpen)) close();
    });

    if (archive) {
      notice('Прежняя карта желаний сохранена в архив.', {
        text: 'Скачать её файлом',
        run: () => download(archive, 'karta-zhelaniy.json')
      });
    }
  }

  /* ── запуск ─────────────────────────────────────────────────── */

  function memoryStorage() {
    /* запасное хранилище: страница работает даже там, где браузер
       запретил localStorage — правки просто не переживут перезагрузку */
    const map = new Map();
    return {
      getItem: k => (map.has(k) ? map.get(k) : null),
      setItem: (k, v) => { map.set(k, String(v)); },
      removeItem: k => { map.delete(k); }
    };
  }

  function init() {
    let storage;
    try {
      storage = root.Store.available(window.localStorage) ? window.localStorage : memoryStorage();
    } catch (err) {
      storage = memoryStorage();
    }
    state.storage = storage;

    const loaded = root.Store.load(storage);
    state.data = loaded.data;
    state.month = root.Model.today().slice(0, 7);

    wireTools(loaded.archive);
    applyTheme(theme());
    render();

    if (loaded.warning) notice(loaded.warning);
  }

  root.UI = { init, open, openReport, openDay, close, update, humanDue, humanSpan, shortRange, shortDate, longDate, daysBetween,
    data: () => state.data, selected: () => state.sphereId };

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})(typeof globalThis !== 'undefined' ? globalThis : this);
