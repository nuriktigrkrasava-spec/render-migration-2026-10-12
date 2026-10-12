'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');
const Stats = require('../js/stats.js');

function пустые() { return Model.clone(Model.DEFAULT_DATA); }

test('у задачи может быть начало периода', () => {
  const res = Model.addTask(пустые(), 'work', 'Ремонт', '2026-09-12', '2026-09-08');
  const task = res.data.tasks[0];
  assert.strictEqual(task.start, '2026-09-08');
  assert.strictEqual(task.due, '2026-09-12');
});

test('без начала период не заводится', () => {
  const res = Model.addTask(пустые(), 'work', 'Обычная', '2026-09-12');
  assert.strictEqual(res.data.tasks[0].start, null);
});

test('начало позже срока не принимается', () => {
  const res = Model.addTask(пустые(), 'work', 'Криво', '2026-09-05', '2026-09-12');
  assert.strictEqual(res.data.tasks[0].start, null);
});

test('начало без срока не имеет смысла и не сохраняется', () => {
  const res = Model.addTask(пустые(), 'work', 'Без конца', '', '2026-09-08');
  assert.strictEqual(res.data.tasks[0].start, null);
});

test('период правится и снимается', () => {
  const added = Model.addTask(пустые(), 'work', 'Ремонт', '2026-09-12', null);
  let data = Model.editTaskStart(added.data, added.id, '2026-09-09');
  assert.strictEqual(data.tasks[0].start, '2026-09-09');

  data = Model.editTaskStart(data, added.id, '');
  assert.strictEqual(data.tasks[0].start, null);
});

test('сдвиг срока раньше начала обнуляет начало', () => {
  const added = Model.addTask(пустые(), 'work', 'Ремонт', '2026-09-12', '2026-09-09');
  const data = Model.editTaskDue(added.data, added.id, '2026-09-07');
  assert.strictEqual(data.tasks[0].start, null);
  assert.strictEqual(data.tasks[0].due, '2026-09-07');
});

test('старые данные без поля start проходят проверку', () => {
  const data = пустые();
  data.tasks = [{ id: 't1', sphereId: 'work', title: 'Старая', due: '2026-09-12', done: false, createdAt: '2026-09-01', doneAt: null }];
  assert.strictEqual(Model.validate(data).ok, true);
});

test('задача с периодом попадает в каждый свой день', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Ремонт', '2026-09-12', '2026-09-10').data;
  assert.deepStrictEqual(Stats.tasksOn(data, '2026-09-09').map(t => t.title), []);
  assert.deepStrictEqual(Stats.tasksOn(data, '2026-09-10').map(t => t.title), ['Ремонт']);
  assert.deepStrictEqual(Stats.tasksOn(data, '2026-09-11').map(t => t.title), ['Ремонт']);
  assert.deepStrictEqual(Stats.tasksOn(data, '2026-09-12').map(t => t.title), ['Ремонт']);
  assert.deepStrictEqual(Stats.tasksOn(data, '2026-09-13').map(t => t.title), []);
});

test('полосы месяца знают, где период начинается и где кончается', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Ремонт', '2026-09-12', '2026-09-10').data;
  data = Model.addTask(data, 'study', 'Реферат', '2026-09-11').data;
  const bars = Stats.monthBars(data, '2026-09');

  assert.deepStrictEqual(bars['2026-09-10'], [{ sphereId: 'work', span: true, head: true, tail: false }]);
  assert.deepStrictEqual(bars['2026-09-11'], [
    { sphereId: 'work', span: true, head: false, tail: false },
    { sphereId: 'study', span: false, head: true, tail: true }
  ]);
  assert.deepStrictEqual(bars['2026-09-12'], [{ sphereId: 'work', span: true, head: false, tail: true }]);
  assert.strictEqual(bars['2026-09-13'], undefined);
});

test('период, начавшийся в прошлом месяце, продолжается в этом без головы', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Долгая', '2026-09-02', '2026-08-28').data;
  const bars = Stats.monthBars(data, '2026-09');
  assert.deepStrictEqual(bars['2026-09-01'], [{ sphereId: 'work', span: true, head: false, tail: false }]);
  assert.deepStrictEqual(bars['2026-09-02'], [{ sphereId: 'work', span: true, head: false, tail: true }]);
});

test('просрочка смотрит на срок, а не на начало периода', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Идёт', '2026-09-10', '2026-09-01').data;
  assert.strictEqual(Stats.overdue(data, '2026-09-05').length, 0);
  assert.strictEqual(Stats.overdue(data, '2026-09-11').length, 1);
});
