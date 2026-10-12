(function (root) {
  'use strict';

  const el = () => root.Dom.el;

  function render(host, ctx) {
    const e = el();
    const день = root.Stats.dayReport(ctx.data, ctx.date);
    const заметка = день.note || { text: '', mood: null };

    const блокЗаметки = note(заметка, ctx);
    host.appendChild(moods(заметка, ctx, блокЗаметки.field));
    host.appendChild(блокЗаметки.box);

    if (день.done.length > 0) {
      host.appendChild(e('p', { class: 'sub', text: 'Сделал' }));
      host.appendChild(list(день.done, ctx, true));
    }

    if (день.open.length > 0) {
      host.appendChild(e('p', { class: 'sub', text: ctx.future ? 'В плане' : 'Не сделал' }));
      host.appendChild(list(день.open, ctx, false));
    }

    if (день.workouts.length > 0) {
      host.appendChild(e('p', { class: 'sub', text: 'Зал' }));
      for (const workout of день.workouts) {
        host.appendChild(e('div', { class: 'log-line' }, [
          e('b', { text: workout.exercise }),
          e('span', { text: workout.sets.map(s => s.weight + '×' + s.reps).join(', ') })
        ]));
      }
    }

    if (день.spent > 0 || день.saved > 0 || день.income > 0) {
      host.appendChild(e('p', { class: 'sub', text: 'Деньги' }));
      const части = [];
      if (день.spent > 0) части.push('потрачено ' + root.Money.format(день.spent));
      if (день.income > 0) части.push('пришло ' + root.Money.format(день.income));
      if (день.saved > 0) части.push('отложено ' + root.Money.format(день.saved));
      host.appendChild(e('div', { class: 'log-line' }, [e('span', { text: части.join(', ') })]));
    }

    if (день.done.length === 0 && день.open.length === 0 && день.workouts.length === 0) {
      host.appendChild(e('p', { class: 'empty', text: 'В этот день ничего не запланировано и не отмечено.' }));
    }
  }

  function moods(заметка, ctx, field) {
    const e = el();
    const выбран = root.Model.mood(заметка.mood);

    const кружки = root.Model.MOODS.map(m => e('button', {
      class: 'mood' + (заметка.mood === m.value ? ' on' : ''),
      type: 'button',
      title: m.name,
      'aria-label': 'Состояние: ' + m.name,
      'aria-pressed': заметка.mood === m.value ? 'true' : 'false',
      style: '--mood:' + m.color,
      /* забираем недописанный текст из поля, иначе перерисовка его потеряет */
      onclick: () => ctx.update(root.Model.setNote(ctx.data, ctx.date, {
        mood: заметка.mood === m.value ? null : m.value,
        text: field.value
      }))
    }));

    return e('div', { class: 'moods' }, [
      e('div', { class: 'mood-row' }, кружки),
      e('span', { class: 'mood-name', text: выбран ? выбран.name : 'как прошёл день?' })
    ]);
  }

  /* заметка пишется без кнопок: текст уезжает в хранилище через
     полсекунды после последней буквы, поле растёт вместе с текстом */
  function note(заметка, ctx) {
    const e = el();

    const field = e('textarea', {
      class: 'note',
      rows: '2',
      placeholder: 'Как прошёл день, что мешало, что получилось'
    });
    field.value = заметка.text;

    const mark = e('span', { class: 'saved', text: '' });

    function grow() {
      field.style.height = 'auto';
      field.style.height = Math.max(72, field.scrollHeight) + 'px';
    }

    let timer = null;
    let saved = заметка.text;

    function store() {
      if (field.value.trim() === saved) return;
      saved = field.value.trim();
      /* тихо: панель не перерисовываем, чтобы не выбить курсор из поля */
      ctx.update(root.Model.setNote(ctx.data, ctx.date, { text: field.value }), null, true);
      mark.textContent = 'сохранено';
      setTimeout(() => { mark.textContent = ''; }, 1600);
    }

    field.addEventListener('input', () => {
      grow();
      clearTimeout(timer);
      timer = setTimeout(store, 500);
    });

    field.addEventListener('blur', () => { clearTimeout(timer); store(); });

    setTimeout(grow, 0);

    return { box: e('div', { class: 'note-box' }, [field, mark]), field: field };
  }

  function list(tasks, ctx, done) {
    const e = el();
    const rows = e('ul', { class: 'rows' });

    for (const task of tasks) {
      const sphere = root.Model.sphere(task.sphereId);
      rows.appendChild(e('li', {
        class: 'row' + (done ? ' done' : ''),
        style: '--sphere:' + sphere.color
      }, [
        e('button', {
          class: 'tick', type: 'button',
          'aria-label': done ? 'Вернуть в работу' : 'Отметить сделанным',
          onclick: () => ctx.update(root.Model.toggleTask(ctx.data, task.id))
        }),
        e('div', { class: 'row-main' }, [
          e('div', { class: 'row-title', text: task.title }),
          e('div', { class: 'row-due', text: sphere.name })
        ])
      ]));
    }

    return rows;
  }

  root.PanelDay = { render };
})(typeof globalThis !== 'undefined' ? globalThis : this);
