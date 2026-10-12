'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');

test('стартовые данные проходят проверку и пусты', () => {
  const res = Model.validate(Model.DEFAULT_DATA);
  assert.strictEqual(res.ok, true);
  assert.strictEqual(Model.DEFAULT_DATA.version, 2);
  assert.deepStrictEqual(Model.DEFAULT_DATA.tasks, []);
  assert.deepStrictEqual(Model.DEFAULT_DATA.workouts, []);
  assert.deepStrictEqual(Model.DEFAULT_DATA.money, { goal: 0, entries: [] });
});

test('пять сфер, задачи только у трёх', () => {
  assert.deepStrictEqual(Model.SPHERES.map(s => s.id), ['work', 'gym', 'social', 'study', 'money']);
  assert.deepStrictEqual(Model.TASK_SPHERES, ['work', 'social', 'study']);
  assert.strictEqual(Model.sphere('gym').name, 'Зал');
  assert.strictEqual(Model.sphere('нет'), null);
});

test('сегодня считается по местному времени, а не по UTC', () => {
  const ночь = new Date(2026, 8, 5, 1, 30);
  assert.strictEqual(Model.today(ночь), '2026-09-05');
});

test('задача добавляется в сферу и получает дату создания', () => {
  const res = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'work', '  Отправить договор  ', '2026-09-12');
  assert.ok(res.id);
  const task = res.data.tasks[0];
  assert.strictEqual(task.title, 'Отправить договор');
  assert.strictEqual(task.sphereId, 'work');
  assert.strictEqual(task.due, '2026-09-12');
  assert.strictEqual(task.done, false);
  assert.strictEqual(task.doneAt, null);
  assert.ok(Model.isDate(task.createdAt));
});

test('задача без срока хранит null, а не пустую строку', () => {
  const res = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'social', 'Снять ролик', '');
  assert.strictEqual(res.data.tasks[0].due, null);
});

test('пустое название и чужая сфера не создают задачу', () => {
  const пусто = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'work', '   ', null);
  assert.strictEqual(пусто.id, null);
  assert.strictEqual(пусто.data.tasks.length, 0);

  const зал = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'gym', 'Присед', null);
  assert.strictEqual(зал.id, null);
  assert.strictEqual(зал.data.tasks.length, 0);
});

test('галочка ставится и снимается, дата выполнения проставляется', () => {
  const added = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'study', 'Реферат', null);
  const done = Model.toggleTask(added.data, added.id);
  assert.strictEqual(done.tasks[0].done, true);
  assert.ok(Model.isDate(done.tasks[0].doneAt));

  const back = Model.toggleTask(done, added.id);
  assert.strictEqual(back.tasks[0].done, false);
  assert.strictEqual(back.tasks[0].doneAt, null);
});

test('название и срок правятся, задача удаляется', () => {
  const added = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'work', 'Старое', null);
  const renamed = Model.editTaskTitle(added.data, added.id, ' Новое ');
  assert.strictEqual(renamed.tasks[0].title, 'Новое');

  const dated = Model.editTaskDue(renamed, added.id, '2026-10-01');
  assert.strictEqual(dated.tasks[0].due, '2026-10-01');

  const cleared = Model.editTaskDue(dated, added.id, '');
  assert.strictEqual(cleared.tasks[0].due, null);

  const gone = Model.removeTask(cleared, added.id);
  assert.strictEqual(gone.tasks.length, 0);
});

test('пустое название при правке не стирает старое', () => {
  const added = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'work', 'Название', null);
  const same = Model.editTaskTitle(added.data, added.id, '   ');
  assert.strictEqual(same.tasks[0].title, 'Название');
});

test('задачи сферы: сначала со сроком по возрастанию, потом без срока, сделанные внизу', () => {
  let data = Model.clone(Model.DEFAULT_DATA);
  data = Model.addTask(data, 'work', 'Без срока', null).data;
  data = Model.addTask(data, 'work', 'Поздняя', '2026-12-01').data;
  data = Model.addTask(data, 'work', 'Ранняя', '2026-09-10').data;
  const сделанная = Model.addTask(data, 'work', 'Сделанная', '2026-09-09');
  data = Model.toggleTask(сделанная.data, сделанная.id);
  data = Model.addTask(data, 'social', 'Чужая сфера', null).data;

  assert.deepStrictEqual(
    Model.tasksOf(data, 'work').map(t => t.title),
    ['Ранняя', 'Поздняя', 'Без срока', 'Сделанная']
  );
});

test('проверка отбраковывает чужую версию, кривой срок и чужую сферу', () => {
  const база = () => Model.clone(Model.DEFAULT_DATA);

  const старая = база(); старая.version = 1;
  assert.strictEqual(Model.validate(старая).ok, false);

  const срок = база();
  срок.tasks = [{ id: 't1', sphereId: 'work', title: 'Раз', due: '12.09.2026', done: false, createdAt: '2026-09-05', doneAt: null }];
  assert.strictEqual(Model.validate(срок).ok, false);

  const сфера = база();
  сфера.tasks = [{ id: 't1', sphereId: 'gym', title: 'Раз', due: null, done: false, createdAt: '2026-09-05', doneAt: null }];
  assert.strictEqual(Model.validate(сфера).ok, false);

  const деньги = база();
  деньги.money = { goal: 0 };
  assert.strictEqual(Model.validate(деньги).ok, false);
});

test('битый JSON не роняет разбор', () => {
  const res = Model.parse('{это не json');
  assert.strictEqual(res.ok, false);
  assert.ok(res.error);
});

test('сериализация проставляет время правки и читается обратно', () => {
  const json = Model.serialize(Model.DEFAULT_DATA);
  const res = Model.parse(json);
  assert.strictEqual(res.ok, true);
  assert.ok(res.data.updatedAt);
});
