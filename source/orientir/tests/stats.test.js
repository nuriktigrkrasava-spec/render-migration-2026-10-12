'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');
const Money = require('../js/money.js');
const Gym = require('../js/gym.js');
const Stats = require('../js/stats.js');

function пустые() { return Model.clone(Model.DEFAULT_DATA); }

test('башня задачной сферы: этажей по числу задач, заполнено по сделанным', () => {
  let data = пустые();
  assert.deepStrictEqual(Stats.fill(data, 'work'), { filled: 0, total: 0 });

  data = Model.addTask(data, 'work', 'Раз', null).data;
  const два = Model.addTask(data, 'work', 'Два', null);
  data = Model.toggleTask(два.data, два.id);
  data = Model.addTask(data, 'study', 'Чужая', null).data;

  assert.deepStrictEqual(Stats.fill(data, 'work'), { filled: 1, total: 2 });
});

test('башня зала: этаж за тренировку, все заполнены', () => {
  let data = пустые();
  data = Gym.add(data, { date: '2026-09-01', exercise: 'Жим', sets: [{ weight: 80, reps: 8 }] }).data;
  data = Gym.add(data, { date: '2026-09-03', exercise: 'Присед', sets: [{ weight: 100, reps: 5 }] }).data;
  assert.deepStrictEqual(Stats.fill(data, 'gym'), { filled: 2, total: 2 });
});

test('башня денег при цели: двадцать этажей, заполнено по проценту', () => {
  let data = Money.setGoal(пустые(), 100000);
  data = Money.add(data, 'save', 50000, '', '2026-09-01').data;
  assert.deepStrictEqual(Stats.fill(data, 'money'), { filled: 10, total: 20 });

  data = Money.add(data, 'save', 200000, '', '2026-09-02').data;
  assert.deepStrictEqual(Stats.fill(data, 'money'), { filled: 20, total: 20 });
});

test('башня денег без цели: этаж за каждые десять тысяч', () => {
  const data = Money.add(пустые(), 'save', 35000, '', '2026-09-01').data;
  assert.deepStrictEqual(Stats.fill(data, 'money'), { filled: 3, total: 3 });
});

test('подпись над башней', () => {
  let data = пустые();
  assert.strictEqual(Stats.label(data, 'work'), 'пусто');

  data = Model.addTask(data, 'work', 'Раз', null).data;
  const два = Model.addTask(data, 'work', 'Два', null);
  data = Model.toggleTask(два.data, два.id);
  assert.strictEqual(Stats.label(data, 'work'), '1 из 2');

  data = Gym.add(data, { date: '2026-09-01', exercise: 'Жим', sets: [{ weight: 80, reps: 8 }] }).data;
  assert.strictEqual(Stats.label(data, 'gym'), '1 тренировка');

  data = Money.setGoal(data, 100000);
  data = Money.add(data, 'save', 46000, '', '2026-09-01').data;
  assert.strictEqual(Stats.label(data, 'money'), '46\u00A0%\u00A0к цели');

  const безЦели = Money.add(пустые(), 'save', 5000, '', '2026-09-01').data;
  assert.strictEqual(Stats.label(безЦели, 'money'), '5\u00A0000\u00A0₽');
});

test('склонение числительных', () => {
  const формы = ['тренировка', 'тренировки', 'тренировок'];
  assert.strictEqual(Stats.plural(1, формы), 'тренировка');
  assert.strictEqual(Stats.plural(2, формы), 'тренировки');
  assert.strictEqual(Stats.plural(5, формы), 'тренировок');
  assert.strictEqual(Stats.plural(11, формы), 'тренировок');
  assert.strictEqual(Stats.plural(21, формы), 'тренировка');
});

test('задачи на дату — только с этим сроком', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'В среду', '2026-09-09').data;
  data = Model.addTask(data, 'study', 'Тоже в среду', '2026-09-09').data;
  data = Model.addTask(data, 'work', 'Позже', '2026-09-10').data;
  assert.deepStrictEqual(Stats.tasksOn(data, '2026-09-09').map(t => t.title), ['В среду', 'Тоже в среду']);
});

test('просрочка — несделанные со сроком раньше сегодня', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Вчера', '2026-09-04').data;
  data = Model.addTask(data, 'work', 'Сегодня', '2026-09-05').data;
  const сделанная = Model.addTask(data, 'work', 'Старая сделанная', '2026-09-01');
  data = Model.toggleTask(сделанная.data, сделанная.id);
  assert.deepStrictEqual(Stats.overdue(data, '2026-09-05').map(t => t.title), ['Вчера']);
});

test('ближайшее: просроченные первыми, затем по сроку, без срока не берём', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Без срока', null).data;
  data = Model.addTask(data, 'work', 'Через неделю', '2026-09-12').data;
  data = Model.addTask(data, 'study', 'Просрочено', '2026-09-01').data;
  data = Model.addTask(data, 'social', 'Сегодня', '2026-09-05').data;
  assert.deepStrictEqual(
    Stats.upcoming(data, '2026-09-05', 3).map(t => t.title),
    ['Просрочено', 'Сегодня', 'Через неделю']
  );
});

test('сетка месяца начинается с понедельника и добита пустыми клетками', () => {
  const сетка = Stats.monthGrid('2026-09');
  assert.strictEqual(сетка[0].length, 7);
  /* 1 сентября 2026 — вторник, значит первая клетка пустая */
  assert.strictEqual(сетка[0][0], null);
  assert.strictEqual(сетка[0][1], '2026-09-01');
  const все = сетка.flat().filter(Boolean);
  assert.strictEqual(все.length, 30);
  assert.strictEqual(все[29], '2026-09-30');
  assert.strictEqual(сетка[сетка.length - 1].length, 7);
});

test('заголовок месяца по-русски, соседние месяцы считаются через год', () => {
  assert.strictEqual(Stats.monthTitle('2026-09'), 'Сентябрь 2026');
  assert.strictEqual(Stats.monthTitle('2026-01'), 'Январь 2026');
  assert.strictEqual(Stats.shiftMonth('2026-01', -1), '2025-12');
  assert.strictEqual(Stats.shiftMonth('2026-12', 1), '2027-01');
});
