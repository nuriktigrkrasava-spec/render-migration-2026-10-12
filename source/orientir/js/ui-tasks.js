(function (root) {
  'use strict';

  const el = () => root.Dom.el;

  const WORDS = {
    work:   { placeholder: 'Что сделать по работе', empty: 'Пока ничего. Напишите первое дело — башня начнёт расти.' },
    social: { placeholder: 'Идея для Threads или TikTok', empty: 'Пусто. Пишите мысль сразу, как пришла: срок можно поставить потом.' },
    study:  { placeholder: 'Что задали', empty: 'Домашки нет. Добавьте задание и поставьте дату сдачи.' }
  };

  /* поле «с» показывается только когда его позвали */
  let spanOpen = false;

  function render(host, ctx) {
    const e = el();
    const words = WORDS[ctx.sphereId] || { placeholder: 'Новая задача', empty: 'Пока пусто.' };

    const title = e('input', { type: 'text', placeholder: words.placeholder });
    const start = e('input', { type: 'date', 'aria-label': 'Начало' });
    const due = e('input', { type: 'date', 'aria-label': 'Срок' });

    function submit() {
      const res = root.Model.addTask(ctx.data, ctx.sphereId, title.value, due.value, spanOpen ? start.value : '');
      if (!res.id) { title.focus(); return; }
      spanOpen = false;
      ctx.update(res.data, 'new');
    }

    title.addEventListener('keydown', ev => { if (ev.key === 'Enter') submit(); });
    due.addEventListener('keydown', ev => { if (ev.key === 'Enter') submit(); });
    start.addEventListener('keydown', ev => { if (ev.key === 'Enter') due.focus(); });

    const dates = e('div', { class: 'dates' }, spanOpen ? [start, e('span', { text: '–' }), due] : [due]);

    host.appendChild(e('div', { class: 'field' }, [
      title, dates,
      e('button', { class: 'go', type: 'button', title: 'Добавить', text: '+', onclick: submit })
    ]));

    host.appendChild(e('button', {
      class: 'link span-toggle',
      type: 'button',
      text: spanOpen ? 'один день' : 'несколько дней',
      onclick: () => { spanOpen = !spanOpen; ctx.update(ctx.data, 'new'); }
    }));

    if (ctx.focus === 'new') title.focus();

    const list = root.Model.tasksOf(ctx.data, ctx.sphereId);
    if (list.length === 0) {
      host.appendChild(e('p', { class: 'empty', text: words.empty }));
      return;
    }

    const rows = e('ul', { class: 'rows' });
    for (const task of list) rows.appendChild(row(task, ctx));
    host.appendChild(rows);
  }

  function row(task, ctx) {
    const e = el();

    const tick = e('button', {
      class: 'tick', type: 'button',
      'aria-label': task.done ? 'Вернуть в работу' : 'Сделано',
      onclick: () => ctx.update(root.Model.toggleTask(ctx.data, task.id))
    });

    const name = e('input', {
      class: 'row-title', type: 'text', value: task.title,
      onchange: ev => ctx.update(root.Model.editTaskTitle(ctx.data, task.id, ev.target.value))
    });
    name.addEventListener('keydown', ev => { if (ev.key === 'Enter') ev.target.blur(); });

    const kill = e('button', {
      class: 'kill', type: 'button', text: '✕', 'aria-label': 'Удалить',
      onclick: () => ctx.update(root.Model.removeTask(ctx.data, task.id))
    });

    return e('li', { class: 'row' + (task.done ? ' done' : '') }, [
      tick,
      e('div', { class: 'row-main' }, [name, dates(task, ctx)]),
      kill
    ]);
  }

  function dates(task, ctx) {
    const e = el();

    const chip = e('button', {
      class: 'row-due',
      type: 'button',
      text: task.due ? root.UI.humanSpan(task, root.Model.today()).text : 'поставить срок'
    });

    if (task.due) {
      const said = root.UI.humanSpan(task, root.Model.today());
      if (said.late) chip.classList.add('late');
      else if (said.soon) chip.classList.add('soon');
    }

    chip.addEventListener('click', () => chip.replaceWith(editor(task, ctx)));
    return chip;
  }

  function editor(task, ctx) {
    const e = el();

    const start = e('input', {
      type: 'date', class: 'row-date', value: task.start || '', 'aria-label': 'Начало',
      onchange: ev => ctx.update(root.Model.editTaskStart(ctx.data, task.id, ev.target.value))
    });

    const due = e('input', {
      type: 'date', class: 'row-date', value: task.due || '', 'aria-label': 'Срок',
      onchange: ev => ctx.update(root.Model.editTaskDue(ctx.data, task.id, ev.target.value))
    });

    const box = e('div', { class: 'row-dates' }, [start, e('span', { text: '–' }), due]);

    setTimeout(() => {
      const focus = task.due ? due : start;
      focus.focus();
      if (focus.showPicker) { try { focus.showPicker(); } catch (err) { /* браузер не даёт */ } }
    }, 0);

    return box;
  }

  root.PanelTasks = { render };
})(typeof globalThis !== 'undefined' ? globalThis : this);
