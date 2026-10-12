'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');
const Store = require('../js/store.js');

function fakeStorage() {
  const map = new Map();
  return {
    map: map,
    getItem: k => (map.has(k) ? map.get(k) : null),
    setItem: (k, v) => { map.set(k, String(v)); },
    removeItem: k => { map.delete(k); }
  };
}

function brokenStorage() {
  return {
    getItem: () => { throw new Error('SecurityError'); },
    setItem: () => { throw new Error('SecurityError'); },
    removeItem: () => { throw new Error('SecurityError'); }
  };
}

test('на пустом хранилище отдаются стартовые данные', () => {
  const res = Store.load(fakeStorage());
  assert.strictEqual(res.source, 'default');
  assert.strictEqual(res.data.version, 2);
  assert.strictEqual(res.archive, null);
});

test('сохранённое возвращается обратно', () => {
  const storage = fakeStorage();
  const added = Model.addTask(Model.clone(Model.DEFAULT_DATA), 'work', 'Договор', '2026-09-12');
  Store.save(storage, added.data);
  const res = Store.load(storage);
  assert.strictEqual(res.source, 'storage');
  assert.strictEqual(res.data.tasks[0].title, 'Договор');
});

test('старая карта желаний уезжает в архив, а место освобождается', () => {
  const storage = fakeStorage();
  const старое = JSON.stringify({ version: 1, dreams: [{ id: 'd1', title: 'Дом' }] });
  storage.setItem('wishmap-v1', старое);

  const res = Store.load(storage);
  assert.strictEqual(res.source, 'default');
  assert.strictEqual(res.archive, старое);
  assert.strictEqual(storage.getItem('wishmap-v1-archive'), старое);
  assert.strictEqual(storage.getItem('wishmap-v1'), null);
});

test('перенос происходит один раз: второй запуск уже без полосы', () => {
  const storage = fakeStorage();
  storage.setItem('wishmap-v1', JSON.stringify({ version: 1 }));
  Store.load(storage);
  const второй = Store.load(storage);
  assert.strictEqual(второй.archive, null);
});

test('перенос не затирает уже существующий архив', () => {
  const storage = fakeStorage();
  storage.setItem('wishmap-v1-archive', 'первый архив');
  storage.setItem('wishmap-v1', 'второй заход');
  Store.load(storage);
  assert.strictEqual(storage.getItem('wishmap-v1-archive'), 'первый архив');
});

test('испорченная запись не роняет загрузку', () => {
  const storage = fakeStorage();
  storage.setItem('orientir-v2', '{сломано');
  const res = Store.load(storage);
  assert.strictEqual(res.source, 'default');
  assert.ok(res.warning);
});

test('запись чужой версии не роняет загрузку', () => {
  const storage = fakeStorage();
  storage.setItem('orientir-v2', JSON.stringify({ version: 99 }));
  const res = Store.load(storage);
  assert.strictEqual(res.source, 'default');
  assert.ok(res.warning);
});

test('недоступное хранилище распознаётся и не роняет страницу', () => {
  assert.strictEqual(Store.available(fakeStorage()), true);
  assert.strictEqual(Store.available(brokenStorage()), false);

  const res = Store.load(brokenStorage());
  assert.strictEqual(res.source, 'default');
  assert.ok(res.warning);
  assert.doesNotThrow(() => Store.save(brokenStorage(), Model.DEFAULT_DATA));
  assert.strictEqual(Store.save(brokenStorage(), Model.DEFAULT_DATA), false);
});

test('имя файла копии содержит дату', () => {
  assert.match(Store.exportName(new Date('2026-09-05T10:00:00Z')), /^orientir-2026-09-05\.json$/);
});
