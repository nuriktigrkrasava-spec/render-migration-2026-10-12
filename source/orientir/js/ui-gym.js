(function (root) {
  'use strict';

  const el = () => root.Dom.el;
  const svg = () => root.Dom.svg;

  /* черновик тренировки живёт между перерисовками панели */
  let draft = { exercise: '', sets: [{ weight: '', reps: '' }], date: '' };

  function resetDraft() {
    draft = { exercise: '', sets: [{ weight: '', reps: '' }], date: '' };
  }

  function render(host, ctx) {
    const e = el();

    host.appendChild(form(ctx));

    const last = root.Gym.last(ctx.data);
    if (!last) {
      host.appendChild(e('p', { class: 'empty', text: 'Тренировок пока нет. Запишите первую — башня зала растёт от каждой.' }));
      return;
    }

    host.appendChild(e('p', { class: 'sub', text: 'Личные рекорды' }));
    for (const name of root.Gym.exercises(ctx.data)) {
      host.appendChild(records(name, ctx));
    }

    host.appendChild(e('p', { class: 'sub', text: 'Журнал' }));
    for (const workout of root.Gym.list(ctx.data, 40)) {
      host.appendChild(logLine(workout, ctx));
    }
  }

  function form(ctx) {
    const e = el();

    const exercise = e('input', {
      type: 'text', placeholder: 'Упражнение', value: draft.exercise,
      oninput: ev => { draft.exercise = ev.target.value; }
    });

    const sets = e('div', { class: 'sets' });

    function drawSets() {
      root.Dom.clear(sets);
      draft.sets.forEach((set, index) => {
        const weight = e('input', {
          type: 'number', inputmode: 'decimal', min: '0', step: '0.5',
          placeholder: 'кг', value: set.weight,
          oninput: ev => { draft.sets[index].weight = ev.target.value; }
        });
        const reps = e('input', {
          type: 'number', inputmode: 'numeric', min: '0', step: '1',
          placeholder: 'раз', value: set.reps,
          oninput: ev => { draft.sets[index].reps = ev.target.value; }
        });
        weight.addEventListener('keydown', ev => { if (ev.key === 'Enter') reps.focus(); });
        reps.addEventListener('keydown', ev => { if (ev.key === 'Enter') submit(); });
        sets.appendChild(e('div', { class: 'set' }, [
          e('span', { text: String(index + 1) }),
          weight,
          e('span', { class: 'x', text: '×' }),
          reps
        ]));
      });
    }

    function submit() {
      const res = root.Gym.add(ctx.data, {
        exercise: draft.exercise,
        sets: draft.sets,
        date: draft.date
      });
      if (!res.id) { exercise.focus(); return; }
      resetDraft();
      ctx.update(res.data, 'new');
    }

    drawSets();

    const more = e('button', {
      class: 'link', type: 'button', text: '+ ещё подход',
      onclick: () => {
        const last = draft.sets[draft.sets.length - 1] || { weight: '', reps: '' };
        draft.sets.push({ weight: last.weight, reps: last.reps });
        drawSets();
      }
    });

    const date = e('input', {
      type: 'date', value: draft.date, 'aria-label': 'Дата тренировки',
      oninput: ev => { draft.date = ev.target.value; }
    });

    const write = e('button', { class: 'go', type: 'button', text: '✓', title: 'Записать', onclick: submit });

    if (ctx.focus === 'new') setTimeout(() => exercise.focus(), 0);

    return e('div', {}, [
      e('div', { class: 'field' }, [exercise]),
      sets,
      e('div', { class: 'field' }, [more, date, write])
    ]);
  }

  function records(name, ctx) {
    const e = el();
    const best = root.Gym.record(ctx.data, name);
    const line = root.Gym.series(ctx.data, name);
    return e('div', { class: 'pr' }, [
      e('div', { class: 'pr-name', text: name }),
      spark(line, ctx.sphere.color),
      e('div', { class: 'pr-weight', text: best + ' кг' })
    ]);
  }

  function spark(line, color) {
    const s = svg();
    const w = 92, h = 26;
    const box = s('svg', { class: 'spark', width: w, height: h, viewBox: '0 0 ' + w + ' ' + h });
    if (line.length < 2) return box;

    const weights = line.map(p => p.weight);
    const low = Math.min.apply(null, weights);
    const high = Math.max.apply(null, weights);
    const span = high - low || 1;

    const points = line.map((p, i) => {
      const x = (i / (line.length - 1)) * (w - 4) + 2;
      const y = h - 3 - ((p.weight - low) / span) * (h - 8);
      return x.toFixed(1) + ',' + y.toFixed(1);
    }).join(' ');

    const path = s('polyline', {
      points: points, fill: 'none', stroke: color,
      'stroke-width': '1.6', 'stroke-linecap': 'round', 'stroke-linejoin': 'round',
      opacity: '0.9'
    });
    box.appendChild(path);

    const tail = points.split(' ').pop().split(',');
    box.appendChild(s('circle', { cx: tail[0], cy: tail[1], r: '2.2', fill: color }));
    return box;
  }

  function logLine(workout, ctx) {
    const e = el();
    const сеты = workout.sets.map(s => s.weight + '×' + s.reps).join(', ');
    return e('div', { class: 'log-line' }, [
      e('em', { text: root.UI.shortDate(workout.date) }),
      e('b', { text: workout.exercise }),
      e('span', { text: сеты }),
      e('button', {
        class: 'kill', type: 'button', text: '✕', 'aria-label': 'Удалить тренировку',
        style: 'margin-left:auto',
        onclick: () => ctx.update(root.Gym.remove(ctx.data, workout.id))
      })
    ]);
  }

  root.PanelGym = { render };
})(typeof globalThis !== 'undefined' ? globalThis : this);
