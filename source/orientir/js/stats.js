(function (root) {
  'use strict';

  const isNode = typeof module !== 'undefined' && module.exports;
  const Model = isNode ? require('./model.js') : root.Model;
  const Money = isNode ? require('./money.js') : root.Money;
  const Gym = isNode ? require('./gym.js') : root.Gym;

  const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

  const MONEY_FLOORS = 20;
  const MONEY_STEP = 10000;

  function plural(n, forms) {
    const десятки = Math.abs(n) % 100;
    const единицы = Math.abs(n) % 10;
    if (десятки > 10 && десятки < 20) return forms[2];
    if (единицы > 1 && единицы < 5) return forms[1];
    if (единицы === 1) return forms[0];
    return forms[2];
  }

  function fill(data, sphereId) {
    const s = Model.sphere(sphereId);
    if (!s) return { filled: 0, total: 0 };

    if (s.kind === 'tasks') {
      const мои = data.tasks.filter(t => t.sphereId === sphereId);
      return { filled: мои.filter(t => t.done).length, total: мои.length };
    }

    if (s.kind === 'gym') {
      const n = data.workouts.length;
      return { filled: n, total: n };
    }

    const прогресс = Money.progress(data);
    if (прогресс.goal > 0) {
      const этажи = Math.min(MONEY_FLOORS, Math.round(прогресс.percent / 100 * MONEY_FLOORS));
      return { filled: этажи, total: MONEY_FLOORS };
    }
    const n = Math.floor(прогресс.saved / MONEY_STEP);
    return { filled: n, total: n };
  }

  function label(data, sphereId) {
    const s = Model.sphere(sphereId);
    if (!s) return '';

    if (s.kind === 'gym') {
      const n = data.workouts.length;
      if (n === 0) return 'пусто';
      return n + ' ' + plural(n, ['тренировка', 'тренировки', 'тренировок']);
    }

    if (s.kind === 'money') {
      const прогресс = Money.progress(data);
      if (прогресс.goal > 0) return прогресс.percent + '\u00A0%\u00A0к цели';
      if (прогресс.saved > 0) return Money.format(прогресс.saved);
      return 'пусто';
    }

    const счёт = fill(data, sphereId);
    if (счёт.total === 0) return 'пусто';
    return счёт.filled + ' из ' + счёт.total;
  }

  function tasksOn(data, date) {
    return data.tasks.filter(t => Model.coversDay(t, date));
  }

  function overdue(data, today) {
    return data.tasks.filter(t => !t.done && t.due && t.due < today);
  }

  function upcoming(data, today, limit) {
    const list = data.tasks
      .filter(t => !t.done && t.due)
      .slice()
      .sort((a, b) => (a.due < b.due ? -1 : (a.due > b.due ? 1 : 0)));
    return limit ? list.slice(0, limit) : list;
  }

  /* метки календаря: точка на срок, полоса на каждый день периода */
  function monthBars(data, month) {
    const bars = {};

    for (const task of data.tasks) {
      if (!task.due) continue;
      const from = task.start || task.due;
      const to = task.due;
      if (to.slice(0, 7) < month || from.slice(0, 7) > month) continue;

      let date = from;
      while (date <= to) {
        if (date.slice(0, 7) === month) {
          if (!bars[date]) bars[date] = [];
          const already = bars[date].find(m => m.sphereId === task.sphereId && m.span === !!task.start);
          const mark = {
            sphereId: task.sphereId,
            span: !!task.start,
            head: date === from,
            tail: date === to
          };
          if (already) {
            already.head = already.head || mark.head;
            already.tail = already.tail || mark.tail;
          } else {
            bars[date].push(mark);
          }
        }
        date = shiftDate(date, 1);
      }
    }

    return bars;
  }

  function pad(n) { return n < 10 ? '0' + n : String(n); }

  function monthGrid(month) {
    const year = Number(month.slice(0, 4));
    const index = Number(month.slice(5, 7)) - 1;
    const первое = new Date(year, index, 1);
    /* getDay(): воскресенье это 0, а неделя у нас начинается с понедельника */
    const сдвиг = (первое.getDay() + 6) % 7;
    const дней = new Date(year, index + 1, 0).getDate();

    const клетки = [];
    for (let i = 0; i < сдвиг; i += 1) клетки.push(null);
    for (let d = 1; d <= дней; d += 1) клетки.push(year + '-' + pad(index + 1) + '-' + pad(d));
    while (клетки.length % 7 !== 0) клетки.push(null);

    const недели = [];
    for (let i = 0; i < клетки.length; i += 7) недели.push(клетки.slice(i, i + 7));
    return недели;
  }

  function monthTitle(month) {
    return MONTHS[Number(month.slice(5, 7)) - 1] + ' ' + month.slice(0, 4);
  }

  function shiftMonth(month, delta) {
    const year = Number(month.slice(0, 4));
    const index = Number(month.slice(5, 7)) - 1 + delta;
    const d = new Date(year, index, 1);
    return d.getFullYear() + '-' + pad(d.getMonth() + 1);
  }


  /* ── итоги дня и недели ─────────────────────────────────────── */

  function plus(date) { return shiftDate(date, 1); }

  function shiftDate(date, delta) {
    const d = new Date(Date.UTC(
      Number(date.slice(0, 4)),
      Number(date.slice(5, 7)) - 1,
      Number(date.slice(8, 10)) + delta
    ));
    return d.getUTCFullYear() + '-' + pad(d.getUTCMonth() + 1) + '-' + pad(d.getUTCDate());
  }

  function moneyOn(data, kind, from, to) {
    return data.money.entries
      .filter(e => e.kind === kind && e.date >= from && e.date <= to)
      .reduce((total, e) => total + e.amount, 0);
  }

  function dayReport(data, date) {
    return {
      date: date,
      done: data.tasks.filter(t => t.done && t.doneAt === date),
      open: data.tasks.filter(t => !t.done && Model.coversDay(t, date)),
      note: Model.note(data, date),
      workouts: data.workouts.filter(w => w.date === date),
      spent: moneyOn(data, 'spend', date, date),
      saved: moneyOn(data, 'save', date, date),
      income: moneyOn(data, 'income', date, date)
    };
  }

  function weekReport(data, today) {
    const from = shiftDate(today, -6);
    const to = today;

    const done = data.tasks.filter(t => t.done && t.doneAt && t.doneAt >= from && t.doneAt <= to);
    const workouts = data.workouts.filter(w => w.date >= from && w.date <= to);

    const counts = {};
    for (const task of done) counts[task.sphereId] = (counts[task.sphereId] || 0) + 1;
    const bySphere = Model.SPHERES
      .filter(s => counts[s.id])
      .map(s => ({ id: s.id, name: s.name, color: s.color, count: counts[s.id] }))
      .sort((a, b) => b.count - a.count);

    const byDay = [];
    for (let i = 6; i >= 0; i -= 1) {
      const date = shiftDate(today, -i);
      byDay.push({
        date: date,
        done: done.filter(t => t.doneAt === date).length,
        trained: workouts.some(w => w.date === date)
      });
    }

    /* рекорд считаем новым, если недельный максимум дотянул до общего */
    const records = [];
    const seen = [];
    for (const workout of workouts) {
      if (seen.indexOf(workout.exercise) !== -1) continue;
      seen.push(workout.exercise);
      const weekTop = workouts
        .filter(w => w.exercise === workout.exercise)
        .reduce((top, w) => Math.max(top, Gym.maxWeight(w)), 0);
      const allTop = data.workouts
        .filter(w => w.exercise === workout.exercise)
        .reduce((top, w) => Math.max(top, Gym.maxWeight(w)), 0);
      if (weekTop >= allTop) records.push({ exercise: workout.exercise, weight: weekTop });
    }

    const moods = [];
    const diary = [];
    for (const day of byDay) {
      const note = Model.note(data, day.date);
      if (!note) continue;
      if (note.mood) moods.push(note.mood);
      if (note.text) diary.push({ date: day.date, text: note.text, mood: note.mood });
    }
    diary.reverse();   /* свежие записи сверху */

    return {
      from: from,
      to: to,
      mood: moods.length ? Math.round(moods.reduce((a, b) => a + b, 0) / moods.length) : null,
      diary: diary,
      moodDays: moods.length,
      done: done.length,
      bySphere: bySphere,
      workouts: workouts.length,
      tonnage: workouts.reduce((total, w) => total + Gym.volume(w), 0),
      records: records,
      spent: moneyOn(data, 'spend', from, to),
      saved: moneyOn(data, 'save', from, to),
      income: moneyOn(data, 'income', from, to),
      byDay: byDay,
      overdue: overdue(data, today).length
    };
  }

  function summary(data, today) {
    const week = weekReport(data, today);
    const lines = [];

    if (week.done > 0) {
      lines.push('Закрыто ' + week.done + ' ' + plural(week.done, ['задача', 'задачи', 'задач']) + '.');
    }

    if (week.workouts > 0) {
      let line = week.workouts + ' ' + plural(week.workouts, ['тренировка', 'тренировки', 'тренировок']);
      if (week.tonnage > 0) line += ', поднято ' + Math.round(week.tonnage) + ' кг';
      lines.push(line + '.');
    }

    if (week.records.length > 0) {
      lines.push('Новый рекорд: ' + week.records.map(r => r.exercise + ' ' + r.weight + ' кг').join(', ') + '.');
    }

    if (week.spent > 0 || week.saved > 0) {
      const parts = [];
      if (week.spent > 0) parts.push('потрачено ' + Money.format(week.spent));
      if (week.saved > 0) parts.push('отложено ' + Money.format(week.saved));
      lines.push(parts.join(', ').replace(/^./, c => c.toUpperCase()) + '.');
    }

    if (week.bySphere.length > 0) {
      const top = week.bySphere[0];
      lines.push('Больше всего — ' + top.name + ': ' + top.count + '.');
    }

    if (week.mood) {
      const слово = Model.mood(week.mood).name;
      lines.push('Состояние за неделю — ' + слово + '.');
    }

    if (week.overdue > 0) {
      lines.push('Просрочено ' + week.overdue + ' ' + plural(week.overdue, ['задача', 'задачи', 'задач']) + '.');
    }

    if (lines.length === 0) return ['За неделю пусто. Закройте одну задачу — тут появится итог.'];
    return lines;
  }

  const Stats = {
    plural, fill, label, tasksOn, overdue, upcoming,
    monthBars, monthGrid, monthTitle, shiftMonth, MONTHS,
    shiftDate, plus, dayReport, weekReport, summary
  };

  if (isNode) module.exports = Stats;
  else root.Stats = Stats;
})(typeof globalThis !== 'undefined' ? globalThis : this);
