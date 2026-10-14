'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');
const Gym = require('../js/gym.js');
const Stats = require('../js/stats.js');

function пустые() { return Model.clone(Model.DEFAULT_DATA); }

test('состояний пять, у каждого имя и цвет', () => {
  assert.strictEqual(Model.MOODS.length, 5);
  assert.deepStrictEqual(Model.MOODS.map(m => m.value), [1, 2, 3, 4, 5]);
  for (const mood of Model.MOODS) {
    assert.ok(mood.name, 'у состояния есть имя');
    assert.match(mood.color, /^#[0-9a-f]{6}$/i);
  }
  assert.strictEqual(Model.mood(4).name, Model.MOODS[3].name);
  assert.strictEqual(Model.mood(9), null);
});

test('заметка дня пишется и читается', () => {
  const data = Model.setNote(пустые(), '2026-09-05', { text: '  Тяжёлое утро, вечером разошёлся  ', mood: 4 });
  assert.deepStrictEqual(Model.note(data, '2026-09-05'), { text: 'Тяжёлое утро, вечером разошёлся', mood: 4 });
  assert.strictEqual(Model.note(data, '2026-09-04'), null);
});

test('состояние и текст правятся по отдельности', () => {
  let data = Model.setNote(пустые(), '2026-09-05', { mood: 2 });
  assert.deepStrictEqual(Model.note(data, '2026-09-05'), { text: '', mood: 2 });

  data = Model.setNote(data, '2026-09-05', { text: 'Простыл' });
  assert.deepStrictEqual(Model.note(data, '2026-09-05'), { text: 'Простыл', mood: 2 });

  data = Model.setNote(data, '2026-09-05', { mood: null });
  assert.deepStrictEqual(Model.note(data, '2026-09-05'), { text: 'Простыл', mood: null });
});

test('пустая заметка не копится в данных', () => {
  let data = Model.setNote(пустые(), '2026-09-05', { text: 'Раз', mood: 3 });
  data = Model.setNote(data, '2026-09-05', { text: '   ', mood: null });
  assert.strictEqual(Model.note(data, '2026-09-05'), null);
  assert.deepStrictEqual(Object.keys(data.notes), []);
});

test('кривая дата и кривое состояние не принимаются', () => {
  let data = Model.setNote(пустые(), 'вчера', { text: 'Нет' });
  assert.deepStrictEqual(Object.keys(data.notes), []);

  data = Model.setNote(пустые(), '2026-09-05', { text: 'Есть', mood: 77 });
  assert.strictEqual(Model.note(data, '2026-09-05').mood, null);
});

test('данные без заметок читаются и проверяются', () => {
  const старые = пустые();
  delete старые.notes;
  assert.strictEqual(Model.validate(старые).ok, true);

  const res = Model.parse(JSON.stringify({ version: 2, tasks: [], workouts: [], money: { goal: 0, entries: [] } }));
  assert.strictEqual(res.ok, true);
  assert.deepStrictEqual(res.data.notes, {});
});

test('битая заметка отбраковывается проверкой', () => {
  const data = пустые();
  data.notes = { '2026-09-05': { text: 5, mood: null } };
  assert.strictEqual(Model.validate(data).ok, false);
});

test('итог дня разделяет сделанное и несделанное', () => {
  let data = пустые();
  const закрытая = Model.addTask(data, 'work', 'Договор', '2026-09-05');
  data = Model.toggleTask(закрытая.data, закрытая.id);
  data.tasks.find(t => t.id === закрытая.id).doneAt = '2026-09-05'; // fixed historical fixture, independent of today's date
  data = Model.addTask(data, 'work', 'Счёт', '2026-09-05').data;
  data = Model.addTask(data, 'study', 'Курсовая', '2026-09-07', '2026-09-04').data;
  data = Model.addTask(data, 'work', 'Другой день', '2026-09-06').data;

  const день = Stats.dayReport(data, '2026-09-05');
  assert.deepStrictEqual(день.done.map(t => t.title), ['Договор']);
  assert.deepStrictEqual(день.open.map(t => t.title), ['Счёт', 'Курсовая']);
});

test('итог дня отдаёт заметку', () => {
  const data = Model.setNote(пустые(), '2026-09-05', { text: 'Устал', mood: 2 });
  assert.deepStrictEqual(Stats.dayReport(data, '2026-09-05').note, { text: 'Устал', mood: 2 });
  assert.strictEqual(Stats.dayReport(data, '2026-09-04').note, null);
});

test('за неделю считается среднее состояние', () => {
  let data = пустые();
  data = Model.setNote(data, '2026-09-05', { mood: 5 });
  data = Model.setNote(data, '2026-09-04', { mood: 3 });
  data = Model.setNote(data, '2026-08-20', { mood: 1 });

  const н = Stats.weekReport(data, '2026-09-05');
  assert.strictEqual(н.moodDays, 2);
  assert.strictEqual(н.mood, 4);
  assert.strictEqual(Stats.weekReport(пустые(), '2026-09-05').mood, null);
});

test('вывод недели говорит о состоянии словами', () => {
  let data = пустые();
  data = Model.setNote(data, '2026-09-05', { mood: 5 });
  data = Model.setNote(data, '2026-09-03', { mood: 4 });
  const строки = Stats.summary(data, '2026-09-05');
  assert.ok(строки.some(s => s.indexOf('остояние') !== -1), строки.join(' | '));
});

test('за неделю собирается дневник: только непустые заметки, свежие сверху', () => {
  let data = пустые();
  data = Model.setNote(data, '2026-09-05', { text: 'Сегодня', mood: 5 });
  data = Model.setNote(data, '2026-09-02', { text: 'Во вторник', mood: 3 });
  data = Model.setNote(data, '2026-09-03', { mood: 4 });
  data = Model.setNote(data, '2026-08-20', { text: 'Давно', mood: 1 });

  const дневник = Stats.weekReport(data, '2026-09-05').diary;
  assert.deepStrictEqual(дневник.map(n => n.date), ['2026-09-05', '2026-09-02']);
  assert.strictEqual(дневник[0].text, 'Сегодня');
  assert.strictEqual(дневник[0].mood, 5);
});
