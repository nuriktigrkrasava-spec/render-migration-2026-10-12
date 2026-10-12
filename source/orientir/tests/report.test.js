'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');
const Money = require('../js/money.js');
const Gym = require('../js/gym.js');
const Stats = require('../js/stats.js');

function пустые() { return Model.clone(Model.DEFAULT_DATA); }

/* задача, закрытая в указанный день */
function closed(data, sphereId, title, doneAt, due) {
  const added = Model.addTask(data, sphereId, title, due || null);
  const next = Model.toggleTask(added.data, added.id);
  next.tasks.find(t => t.id === added.id).doneAt = doneAt;
  return next;
}

test('соседние дни считаются через границу месяца', () => {
  assert.strictEqual(Stats.shiftDate('2026-09-01', -1), '2026-08-31');
  assert.strictEqual(Stats.shiftDate('2026-12-31', 1), '2027-01-01');
  assert.strictEqual(Stats.shiftDate('2026-09-05', -6), '2026-08-30');
});

test('итог дня: закрытые задачи, тренировки и деньги за этот день', () => {
  let data = пустые();
  data = closed(data, 'work', 'Договор', '2026-09-05');
  data = closed(data, 'study', 'Реферат', '2026-09-04');
  data = Gym.add(data, { date: '2026-09-05', exercise: 'Жим', sets: [{ weight: 80, reps: 8 }] }).data;
  data = Money.add(data, 'spend', 1200, 'Обед', '2026-09-05').data;
  data = Money.add(data, 'save', 5000, '', '2026-09-05').data;
  data = Money.add(data, 'spend', 900, 'Вчера', '2026-09-04').data;

  const день = Stats.dayReport(data, '2026-09-05');
  assert.deepStrictEqual(день.done.map(t => t.title), ['Договор']);
  assert.strictEqual(день.workouts.length, 1);
  assert.strictEqual(день.spent, 1200);
  assert.strictEqual(день.saved, 5000);
});

test('пустой день не ломается', () => {
  const день = Stats.dayReport(пустые(), '2026-09-05');
  assert.deepStrictEqual(день.done, []);
  assert.strictEqual(день.spent, 0);
});

test('неделя — семь дней вместе с сегодняшним', () => {
  const неделя = Stats.weekReport(пустые(), '2026-09-05');
  assert.strictEqual(неделя.from, '2026-08-30');
  assert.strictEqual(неделя.to, '2026-09-05');
  assert.strictEqual(неделя.byDay.length, 7);
  assert.strictEqual(неделя.byDay[0].date, '2026-08-30');
  assert.strictEqual(неделя.byDay[6].date, '2026-09-05');
});

test('за неделю считаются задачи, тренировки, тоннаж и деньги', () => {
  let data = пустые();
  data = closed(data, 'work', 'Раз', '2026-09-05');
  data = closed(data, 'work', 'Два', '2026-09-03');
  data = closed(data, 'study', 'Три', '2026-08-31');
  data = closed(data, 'work', 'Старое', '2026-08-20');
  data = Gym.add(data, { date: '2026-09-04', exercise: 'Жим', sets: [{ weight: 80, reps: 10 }] }).data;
  data = Gym.add(data, { date: '2026-08-25', exercise: 'Жим', sets: [{ weight: 70, reps: 10 }] }).data;
  data = Money.add(data, 'spend', 3000, '', '2026-09-02').data;
  data = Money.add(data, 'save', 20000, '', '2026-09-02').data;
  data = Money.add(data, 'spend', 9999, '', '2026-08-01').data;

  const н = Stats.weekReport(data, '2026-09-05');
  assert.strictEqual(н.done, 3);
  assert.strictEqual(н.workouts, 1);
  assert.strictEqual(н.tonnage, 800);
  assert.strictEqual(н.spent, 3000);
  assert.strictEqual(н.saved, 20000);
  assert.deepStrictEqual(н.bySphere.map(s => [s.id, s.count]), [['work', 2], ['study', 1]]);
  assert.strictEqual(н.byDay[6].done, 1);
  assert.strictEqual(н.byDay[5].trained, true);
});

test('просрочка на конец недели считается отдельно', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Провалено', '2026-09-01').data;
  data = Model.addTask(data, 'work', 'Ещё успею', '2026-09-30').data;
  assert.strictEqual(Stats.weekReport(data, '2026-09-05').overdue, 1);
});

test('новый личный рекорд за неделю попадает в итог', () => {
  let data = пустые();
  data = Gym.add(data, { date: '2026-08-10', exercise: 'Жим', sets: [{ weight: 80, reps: 5 }] }).data;
  data = Gym.add(data, { date: '2026-09-03', exercise: 'Жим', sets: [{ weight: 85, reps: 3 }] }).data;
  data = Gym.add(data, { date: '2026-09-04', exercise: 'Присед', sets: [{ weight: 100, reps: 5 }] }).data;
  data = Gym.add(data, { date: '2026-08-11', exercise: 'Присед', sets: [{ weight: 120, reps: 5 }] }).data;

  const н = Stats.weekReport(data, '2026-09-05');
  assert.deepStrictEqual(н.records, [{ exercise: 'Жим', weight: 85 }]);
});

test('вывод недели: живые строки, а не пустые заготовки', () => {
  let data = пустые();
  data = closed(data, 'work', 'Раз', '2026-09-05');
  data = closed(data, 'work', 'Два', '2026-09-04');
  data = Gym.add(data, { date: '2026-09-04', exercise: 'Жим', sets: [{ weight: 80, reps: 10 }] }).data;
  data = Money.add(data, 'save', 20000, '', '2026-09-02').data;

  const строки = Stats.summary(data, '2026-09-05');
  assert.ok(строки.some(s => s.indexOf('2 задачи') !== -1), строки.join(' | '));
  assert.ok(строки.some(s => s.indexOf('1 тренировка') !== -1), строки.join(' | '));
  assert.ok(строки.some(s => s.indexOf('Работа') !== -1), строки.join(' | '));
});

test('пустая неделя честно говорит, что пусто', () => {
  const строки = Stats.summary(пустые(), '2026-09-05');
  assert.strictEqual(строки.length, 1);
  assert.ok(строки[0].indexOf('пусто') !== -1 || строки[0].indexOf('Пусто') !== -1);
});
