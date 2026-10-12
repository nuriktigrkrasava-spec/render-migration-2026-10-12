(function (root) {
  'use strict';

  const el = () => root.Dom.el;
  const WEEKDAY = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'];

  function weekday(date) {
    const d = new Date(Number(date.slice(0, 4)), Number(date.slice(5, 7)) - 1, Number(date.slice(8, 10)));
    return WEEKDAY[d.getDay()];
  }

  function render(host, ctx) {
    const e = el();
    const today = root.Model.today();
    const день = root.Stats.dayReport(ctx.data, today);
    const неделя = root.Stats.weekReport(ctx.data, today);

    /* вывод недели идёт первым: ради него сюда и заходят */
    const вывод = e('div', { class: 'verdict' });
    for (const line of root.Stats.summary(ctx.data, today)) {
      вывод.appendChild(e('p', { text: line }));
    }
    host.appendChild(вывод);

    host.appendChild(e('p', { class: 'sub', text: 'Неделя: ' + root.UI.longDate(неделя.from) + ' — ' + root.UI.longDate(неделя.to) }));
    host.appendChild(chart(неделя));

    host.appendChild(e('div', { class: 'figures' }, [
      figure('Закрыто задач', String(неделя.done)),
      figure('Тренировок', String(неделя.workouts), неделя.tonnage > 0 ? Math.round(неделя.tonnage) + ' кг' : null),
      figure('Потрачено', root.Money.format(неделя.spent)),
      figure('Отложено', root.Money.format(неделя.saved))
    ]));

    if (неделя.bySphere.length > 0) {
      host.appendChild(e('p', { class: 'sub', text: 'По сферам' }));
      const most = неделя.bySphere[0].count;
      for (const item of неделя.bySphere) {
        host.appendChild(e('div', { class: 'share', style: '--sphere:' + item.color }, [
          e('span', { text: item.name }),
          e('div', { class: 'bar' }, [e('i', { style: 'width:' + Math.round(item.count / most * 100) + '%' })]),
          e('b', { text: String(item.count) })
        ]));
      }
    }

    if (неделя.diary.length > 0) {
      host.appendChild(e('p', { class: 'sub', text: 'Дневник' }));
      for (const запись of неделя.diary) {
        const состояние = root.Model.mood(запись.mood);
        host.appendChild(e('button', {
          class: 'diary',
          type: 'button',
          onclick: () => root.UI.openDay(запись.date)
        }, [
          e('div', { class: 'diary-head' }, [
            состояние ? e('i', { style: 'background:' + состояние.color }) : null,
            e('em', { text: root.UI.longDate(запись.date) }),
            состояние ? e('span', { text: состояние.name }) : null
          ]),
          e('p', { text: запись.text })
        ]));
      }
    }

    host.appendChild(e('p', { class: 'sub', text: 'Сегодня' }));
    host.appendChild(todayBlock(день));
  }

  function figure(name, value, note) {
    const e = el();
    return e('div', { class: 'figure' }, [
      e('em', { text: name }),
      e('b', { text: value }),
      note ? e('small', { text: note }) : null
    ]);
  }

  function chart(неделя) {
    const e = el();
    const most = Math.max.apply(null, неделя.byDay.map(d => d.done).concat([1]));
    const today = root.Model.today();

    const columns = неделя.byDay.map(day => {
      const height = day.done === 0 ? 3 : Math.round(day.done / most * 54) + 4;
      return e('div', { class: 'col' + (day.date === today ? ' now' : '') }, [
        e('div', { class: 'col-bar' }, [
          e('i', { style: 'height:' + height + 'px', class: day.done === 0 ? 'none' : null })
        ]),
        e('span', { text: weekday(day.date) }),
        day.trained ? e('u', { title: 'тренировка' }) : e('u', { class: 'off' })
      ]);
    });

    return e('div', { class: 'week' }, columns);
  }

  function todayBlock(день) {
    const e = el();
    const строки = [];

    if (день.done.length > 0) {
      for (const task of день.done) {
        const sphere = root.Model.sphere(task.sphereId);
        строки.push(e('div', { class: 'cal-task' }, [
          e('i', { style: 'background:' + sphere.color }),
          e('b', { text: task.title })
        ]));
      }
    }

    for (const workout of день.workouts) {
      строки.push(e('div', { class: 'cal-task' }, [
        e('i', { style: 'background:' + root.Model.sphere('gym').color }),
        e('b', { text: workout.exercise + ' — ' + workout.sets.map(s => s.weight + '×' + s.reps).join(', ') })
      ]));
    }

    if (день.spent > 0 || день.saved > 0) {
      const части = [];
      if (день.spent > 0) части.push('потрачено ' + root.Money.format(день.spent));
      if (день.saved > 0) части.push('отложено ' + root.Money.format(день.saved));
      строки.push(e('div', { class: 'cal-task' }, [
        e('i', { style: 'background:' + root.Model.sphere('money').color }),
        e('b', { text: части.join(', ') })
      ]));
    }

    if (строки.length === 0) return e('p', { class: 'empty', text: 'Сегодня пока ничего не отмечено.' });
    return e('div', {}, строки);
  }

  root.PanelReport = { render };
})(typeof globalThis !== 'undefined' ? globalThis : this);
