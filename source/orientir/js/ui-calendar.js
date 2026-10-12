(function (root) {
  'use strict';

  const el = () => root.Dom.el;
  const WEEKDAYS = ['пн', 'вт', 'ср', 'чт', 'пт', 'сб', 'вс'];

  function render(ctx) {
    const e = el();
    const grid = document.getElementById('cal-grid');

    document.getElementById('cal-title').textContent = root.Stats.monthTitle(ctx.month);

    root.Dom.clear(grid);

    for (const wd of WEEKDAYS) grid.appendChild(e('div', { class: 'wd', text: wd }));

    const bars = root.Stats.monthBars(ctx.data, ctx.month);
    const today = root.Model.today();

    for (const week of root.Stats.monthGrid(ctx.month)) {
      for (const date of week) {
        if (!date) { grid.appendChild(e('div', { class: 'day blank' })); continue; }

        /* период — мягкая заливка через дни, срок — точка под числом */
        const ranges = [];
        const dots = e('div', { class: 'dots' });

        for (const mark of (bars[date] || [])) {
          const color = root.Model.sphere(mark.sphereId).color;
          if (mark.span) {
            ranges.push(e('div', {
              class: 'range' + (mark.head ? ' head' : '') + (mark.tail ? ' tail' : ''),
              style: 'background:' + color
            }));
          } else {
            dots.appendChild(e('i', { style: 'background:' + color }));
          }
        }

        const заметка = root.Model.note(ctx.data, date);
        const состояние = заметка ? root.Model.mood(заметка.mood) : null;

        const cell = e('button', {
          class: 'day' + (date === today ? ' today' : ''),
          type: 'button',
          'aria-pressed': date === ctx.day ? 'true' : 'false',
          onclick: () => ctx.pick(date === ctx.day ? null : date)
        }, [
          e('span', { text: String(Number(date.slice(8, 10))) }),
          заметка ? e('u', {
            class: 'noted',
            style: 'background:' + (состояние ? состояние.color : 'var(--line-strong)')
          }) : null,
          dots
        ]);

        for (const range of ranges) cell.insertBefore(range, cell.firstChild);

        grid.appendChild(cell);
      }
    }

    /* подробности дня живут в боковой панели, здесь только сетка */
  }

  root.Calendar = { render };
})(typeof globalThis !== 'undefined' ? globalThis : this);
