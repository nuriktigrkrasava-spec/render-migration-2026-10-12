'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');
const Gym = require('../js/gym.js');

function пустые() { return Model.clone(Model.DEFAULT_DATA); }

test('тренировка записывается с подходами', () => {
  const res = Gym.add(пустые(), {
    date: '2026-09-05',
    exercise: '  Жим лёжа  ',
    sets: [{ weight: 80, reps: 8 }, { weight: '80', reps: '7' }]
  });
  assert.ok(res.id);
  const w = res.data.workouts[0];
  assert.strictEqual(w.exercise, 'Жим лёжа');
  assert.strictEqual(w.date, '2026-09-05');
  assert.deepStrictEqual(w.sets, [{ weight: 80, reps: 8 }, { weight: 80, reps: 7 }]);
});

test('без даты подставляется сегодня', () => {
  const res = Gym.add(пустые(), { exercise: 'Присед', sets: [{ weight: 100, reps: 5 }] });
  assert.ok(Model.isDate(res.data.workouts[0].date));
});

test('без упражнения, без подходов и с нулевым весом не записывается', () => {
  const безУпражнения = Gym.add(пустые(), { exercise: '  ', sets: [{ weight: 80, reps: 8 }] });
  assert.strictEqual(безУпражнения.id, null);

  const безПодходов = Gym.add(пустые(), { exercise: 'Жим', sets: [] });
  assert.strictEqual(безПодходов.id, null);

  const ноль = Gym.add(пустые(), { exercise: 'Жим', sets: [{ weight: 0, reps: 8 }] });
  assert.strictEqual(ноль.id, null);
  assert.strictEqual(ноль.data.workouts.length, 0);
});

test('пустые подходы отбрасываются, а не роняют запись', () => {
  const res = Gym.add(пустые(), {
    exercise: 'Тяга',
    sets: [{ weight: 100, reps: 5 }, { weight: '', reps: '' }]
  });
  assert.strictEqual(res.data.workouts[0].sets.length, 1);
});

test('личный рекорд — наибольший вес по упражнению', () => {
  let data = пустые();
  data = Gym.add(data, { date: '2026-09-01', exercise: 'Жим', sets: [{ weight: 80, reps: 8 }] }).data;
  data = Gym.add(data, { date: '2026-09-08', exercise: 'Жим', sets: [{ weight: 85, reps: 5 }, { weight: 90, reps: 1 }] }).data;
  data = Gym.add(data, { date: '2026-09-08', exercise: 'Присед', sets: [{ weight: 120, reps: 5 }] }).data;
  assert.strictEqual(Gym.record(data, 'Жим'), 90);
  assert.strictEqual(Gym.record(data, 'Присед'), 120);
  assert.strictEqual(Gym.record(data, 'Планка'), null);
});

test('ряд весов по упражнению идёт по возрастанию даты, по максимуму за день', () => {
  let data = пустые();
  data = Gym.add(data, { date: '2026-09-08', exercise: 'Жим', sets: [{ weight: 85, reps: 5 }] }).data;
  data = Gym.add(data, { date: '2026-09-01', exercise: 'Жим', sets: [{ weight: 80, reps: 8 }] }).data;
  data = Gym.add(data, { date: '2026-09-08', exercise: 'Жим', sets: [{ weight: 90, reps: 1 }] }).data;
  assert.deepStrictEqual(Gym.series(data, 'Жим'), [
    { date: '2026-09-01', weight: 80 },
    { date: '2026-09-08', weight: 90 }
  ]);
});

test('список упражнений без повторов, недавние впереди', () => {
  let data = пустые();
  data = Gym.add(data, { date: '2026-09-01', exercise: 'Жим', sets: [{ weight: 80, reps: 8 }] }).data;
  data = Gym.add(data, { date: '2026-09-03', exercise: 'Присед', sets: [{ weight: 100, reps: 5 }] }).data;
  data = Gym.add(data, { date: '2026-09-05', exercise: 'Жим', sets: [{ weight: 82, reps: 8 }] }).data;
  assert.deepStrictEqual(Gym.exercises(data), ['Жим', 'Присед']);
});

test('журнал: новые сверху, последняя тренировка отдельно, удаление работает', () => {
  let data = пустые();
  const первая = Gym.add(data, { date: '2026-09-01', exercise: 'Жим', sets: [{ weight: 80, reps: 8 }] });
  data = Gym.add(первая.data, { date: '2026-09-05', exercise: 'Присед', sets: [{ weight: 100, reps: 5 }] }).data;
  assert.deepStrictEqual(Gym.list(data).map(w => w.exercise), ['Присед', 'Жим']);
  assert.strictEqual(Gym.last(data).exercise, 'Присед');

  const после = Gym.remove(data, первая.id);
  assert.strictEqual(после.workouts.length, 1);
  assert.strictEqual(Gym.last(пустые()), null);
});

test('объём тренировки — сумма вес × повторы', () => {
  assert.strictEqual(Gym.volume({ sets: [{ weight: 80, reps: 8 }, { weight: 80, reps: 7 }] }), 1200);
});
