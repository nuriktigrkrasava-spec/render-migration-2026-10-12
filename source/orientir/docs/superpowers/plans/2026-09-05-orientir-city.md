# Ориентир: город задач — план реализации

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Заменить прежнюю карту желаний одной локальной страницей: трёхмерный город из пяти башен (Работа, Зал, Соцсети, Учёба, Деньги), общий календарь сроков, журнал тренировок с весами и учёт денег.

**Architecture:** Данные и все вычисления — чистые модули без DOM (`js/model.js`, `js/money.js`, `js/gym.js`, `js/stats.js`), они же покрыты тестами `node --test`. Хранилище с переносом старых данных — `js/store.js`. Интерфейс разбит по панелям (`js/ui-*.js`) поверх трёхмерной сцены `js/city.js` на Three.js. Каждый модуль — IIFE, который кладёт себя в глобальную переменную в браузере и в `module.exports` в Node, как уже сделано в проекте.

**Tech Stack:** Ванильный JavaScript без сборки, Three.js (локально в `vendor/`, подключается как ES-модуль), `node --test` для тестов, `python3 -m http.server` через `Ориентир.command`.

**Spec:** `docs/superpowers/specs/2026-09-05-orientir-city-design.md`

## Global Constraints

- Язык интерфейса, комментариев и сообщений об ошибках — русский.
- Версия данных: `2`. Ключ хранилища: `orientir-v2`. Архивный ключ: `wishmap-v1-archive`.
- Пять сфер зашиты в код: `work` Работа, `gym` Зал, `social` Соцсети, `study` Учёба, `money` Деньги. Пользователь их не добавляет и не переименовывает.
- Задачи бывают только у сфер `work`, `social`, `study`.
- Даты — строки `ГГГГ-ММ-ДД`, «сегодня» считается по местному времени (не по UTC).
- Деньги — целые рубли, формат `40 000 ₽` с неразрывным пробелом ` ` перед `₽` и между разрядами.
- Никаких внешних сетевых запросов: библиотека лежит в `vendor/`.
- Каждый модуль в браузере — глобальная переменная (`Model`, `Money`, `Gym`, `Stats`, `Store`, `City`), в Node — `module.exports`.
- Стиль кода: как в существующих `js/model.js` и `js/store.js` — `'use strict'`, IIFE, функции возвращают новые данные, а не правят на месте.
- Коммит после каждой задачи. Сообщения на русском, в повелительном или назывном виде, как в истории проекта.

## Файлы

| Файл | За что отвечает |
|------|-----------------|
| `js/model.js` | структура данных, задачи, проверка, сериализация, даты |
| `js/money.js` | операции с деньгами и подсчёты |
| `js/gym.js` | тренировки, рекорды, ряды весов |
| `js/stats.js` | заполнение башен, календарь, ближайшее и просроченное |
| `js/store.js` | localStorage, перенос `wishmap-v1`, копия в файл |
| `js/ui.js` | оболочка: выбор сферы, открытие и закрытие панелей, шапка |
| `js/ui-tasks.js` | панели Работы, Соцсетей, Учёбы |
| `js/ui-calendar.js` | нижняя полоса календаря и строка «ближайшее» |
| `js/ui-gym.js` | панель Зала |
| `js/ui-money.js` | панель Денег |
| `js/city.js` | трёхмерная сцена, башни, подписи, клики |
| `vendor/three.module.js`, `vendor/OrbitControls.js` | библиотека |
| `index.html`, `styles.css` | разметка и оформление |
| `tests/*.test.js` | тесты на модель, деньги, зал, статистику, хранилище |

---

### Task 1: Модель — структура данных и задачи

**Files:**
- Modify: `js/model.js` (полная замена содержимого)
- Test: `tests/model.test.js` (полная замена содержимого)

**Interfaces:**
- Consumes: ничего
- Produces: `Model.VERSION` (число 2), `Model.SPHERES` (массив `{id, name, color, kind}`), `Model.TASK_SPHERES` (массив id), `Model.DEFAULT_DATA`, `Model.sphere(id)`, `Model.createId(prefix)`, `Model.isDate(v)`, `Model.today(now)`, `Model.clone(data)`, `Model.validate(data) -> {ok, data|error}`, `Model.parse(json)`, `Model.serialize(data)`, `Model.addTask(data, sphereId, title, due) -> {data, id}`, `Model.toggleTask(data, id) -> data`, `Model.editTaskTitle(data, id, title) -> data`, `Model.editTaskDue(data, id, due) -> data`, `Model.removeTask(data, id) -> data`, `Model.tasksOf(data, sphereId) -> [task]`

- [ ] **Step 1: Написать падающие тесты**

Полностью заменить `tests/model.test.js`:

```js
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `node --test tests/model.test.js`
Expected: FAIL — старая модель не знает про `SPHERES`, `addTask` с другой сигнатурой.

- [ ] **Step 3: Написать модель**

Полностью заменить `js/model.js`:

```js
(function (root) {
  'use strict';

  const VERSION = 2;

  const SPHERES = [
    { id: 'work',   name: 'Работа',  color: '#4f7cff', kind: 'tasks' },
    { id: 'gym',    name: 'Зал',     color: '#ff6b4a', kind: 'gym'   },
    { id: 'social', name: 'Соцсети', color: '#c463ff', kind: 'tasks' },
    { id: 'study',  name: 'Учёба',   color: '#25c2a0', kind: 'tasks' },
    { id: 'money',  name: 'Деньги',  color: '#f2b134', kind: 'money' }
  ];

  const TASK_SPHERES = SPHERES.filter(s => s.kind === 'tasks').map(s => s.id);

  const DEFAULT_DATA = {
    version: VERSION,
    updatedAt: null,
    tasks: [],
    workouts: [],
    money: { goal: 0, entries: [] }
  };

  let counter = 0;
  function createId(prefix) {
    counter += 1;
    return prefix + Date.now().toString(36) + counter.toString(36);
  }

  const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
  function isDate(value) {
    return typeof value === 'string' && DATE_RE.test(value);
  }

  function today(now) {
    /* по местному времени, не по UTC: иначе всё, что заведено ночью,
       помечается вчерашним днём в часовых поясах восточнее Гринвича */
    const d = now || new Date();
    const m = String(d.getMonth() + 1);
    const day = String(d.getDate());
    return d.getFullYear() + '-' + (m.length < 2 ? '0' + m : m) + '-' + (day.length < 2 ? '0' + day : day);
  }

  function sphere(id) {
    return SPHERES.find(s => s.id === id) || null;
  }

  function clone(data) {
    return JSON.parse(JSON.stringify(data));
  }

  function validate(data) {
    if (!data || typeof data !== 'object') return { ok: false, error: 'Данные не объект' };
    if (data.version !== VERSION) return { ok: false, error: 'Другая версия данных' };
    if (!Array.isArray(data.tasks)) return { ok: false, error: 'Нет списка задач' };
    if (!Array.isArray(data.workouts)) return { ok: false, error: 'Нет списка тренировок' };
    if (!data.money || typeof data.money !== 'object') return { ok: false, error: 'Нет раздела денег' };
    if (!Array.isArray(data.money.entries)) return { ok: false, error: 'Нет списка операций' };
    if (typeof data.money.goal !== 'number' || data.money.goal < 0) {
      return { ok: false, error: 'Неверная цель накопления' };
    }

    for (const task of data.tasks) {
      if (!task || typeof task.title !== 'string' || !task.title.trim()) {
        return { ok: false, error: 'У задачи нет названия' };
      }
      if (TASK_SPHERES.indexOf(task.sphereId) === -1) {
        return { ok: false, error: 'Задача в чужой сфере: ' + task.title };
      }
      if (task.due !== null && !isDate(task.due)) {
        return { ok: false, error: 'Неверный срок у задачи: ' + task.title };
      }
      if (typeof task.done !== 'boolean') {
        return { ok: false, error: 'У задачи нет отметки о выполнении: ' + task.title };
      }
    }

    for (const workout of data.workouts) {
      if (!workout || !isDate(workout.date)) return { ok: false, error: 'У тренировки нет даты' };
      if (typeof workout.exercise !== 'string' || !workout.exercise.trim()) {
        return { ok: false, error: 'У тренировки нет упражнения' };
      }
      if (!Array.isArray(workout.sets) || workout.sets.length === 0) {
        return { ok: false, error: 'У тренировки нет подходов: ' + workout.exercise };
      }
      for (const set of workout.sets) {
        if (!set || !(set.weight > 0) || !(set.reps > 0)) {
          return { ok: false, error: 'Неверный подход: ' + workout.exercise };
        }
      }
    }

    for (const entry of data.money.entries) {
      if (!entry || !isDate(entry.date)) return { ok: false, error: 'У операции нет даты' };
      if (['spend', 'income', 'save'].indexOf(entry.kind) === -1) {
        return { ok: false, error: 'Неизвестный вид операции' };
      }
      if (!Number.isFinite(entry.amount) || entry.amount <= 0) {
        return { ok: false, error: 'Неверная сумма операции' };
      }
    }

    return { ok: true, data: data };
  }

  function parse(json) {
    let raw;
    try {
      raw = JSON.parse(json);
    } catch (e) {
      return { ok: false, error: 'Не удалось разобрать файл' };
    }
    return validate(raw);
  }

  function serialize(data) {
    const copy = Object.assign({}, data, { updatedAt: new Date().toISOString() });
    return JSON.stringify(copy, null, 2);
  }

  function cleanDue(due) {
    const value = String(due == null ? '' : due).trim();
    return isDate(value) ? value : null;
  }

  function addTask(data, sphereId, title, due) {
    const next = clone(data);
    const clean = String(title || '').trim();
    if (!clean || TASK_SPHERES.indexOf(sphereId) === -1) return { data: next, id: null };
    const task = {
      id: createId('t'),
      sphereId: sphereId,
      title: clean,
      due: cleanDue(due),
      done: false,
      createdAt: today(),
      doneAt: null
    };
    next.tasks.push(task);
    return { data: next, id: task.id };
  }

  function findTask(data, id) {
    return data.tasks.find(t => t.id === id) || null;
  }

  function toggleTask(data, id) {
    const next = clone(data);
    const task = findTask(next, id);
    if (!task) return next;
    task.done = !task.done;
    task.doneAt = task.done ? today() : null;
    return next;
  }

  function editTaskTitle(data, id, title) {
    const next = clone(data);
    const task = findTask(next, id);
    const clean = String(title || '').trim();
    if (task && clean) task.title = clean;
    return next;
  }

  function editTaskDue(data, id, due) {
    const next = clone(data);
    const task = findTask(next, id);
    if (task) task.due = cleanDue(due);
    return next;
  }

  function removeTask(data, id) {
    const next = clone(data);
    next.tasks = next.tasks.filter(t => t.id !== id);
    return next;
  }

  function tasksOf(data, sphereId) {
    /* сначала несделанные: со сроком по возрастанию, без срока в конце;
       сделанные всегда внизу */
    return data.tasks
      .filter(t => t.sphereId === sphereId)
      .slice()
      .sort((a, b) => {
        if (a.done !== b.done) return a.done ? 1 : -1;
        if (a.due && b.due) return a.due < b.due ? -1 : (a.due > b.due ? 1 : 0);
        if (a.due) return -1;
        if (b.due) return 1;
        return 0;
      });
  }

  const Model = {
    VERSION, SPHERES, TASK_SPHERES, DEFAULT_DATA,
    createId, isDate, today, sphere, clone,
    validate, parse, serialize,
    addTask, findTask, toggleTask, editTaskTitle, editTaskDue, removeTask, tasksOf
  };

  if (typeof module !== 'undefined' && module.exports) module.exports = Model;
  else root.Model = Model;
})(typeof globalThis !== 'undefined' ? globalThis : this);
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `node --test tests/model.test.js`
Expected: PASS, все тесты зелёные.

- [ ] **Step 5: Коммит**

```bash
git add js/model.js tests/model.test.js
git commit -m "Модель второй версии: пять сфер и задачи со сроками"
```

---

### Task 2: Модель денег

**Files:**
- Create: `js/money.js`
- Test: `tests/money.test.js`

**Interfaces:**
- Consumes: `Model.clone`, `Model.createId`, `Model.today`, `Model.isDate`
- Produces: `Money.KINDS` (`['spend','income','save']`), `Money.add(data, kind, amount, note, date) -> {data, id}`, `Money.remove(data, id) -> data`, `Money.setGoal(data, goal) -> data`, `Money.pocket(data) -> число`, `Money.saved(data) -> число`, `Money.spentTotal(data) -> число`, `Money.spentInMonth(data, 'ГГГГ-ММ') -> число`, `Money.progress(data) -> {saved, goal, percent}`, `Money.entries(data, limit) -> [operation]`, `Money.format(amount) -> '40 000 ₽'`

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/money.test.js`:

```js
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const Model = require('../js/model.js');
const Money = require('../js/money.js');

function пустые() { return Model.clone(Model.DEFAULT_DATA); }

test('операция записывается с датой и заметкой', () => {
  const res = Money.add(пустые(), 'spend', 1200, '  Продукты  ', '2026-09-05');
  assert.ok(res.id);
  const entry = res.data.money.entries[0];
  assert.strictEqual(entry.kind, 'spend');
  assert.strictEqual(entry.amount, 1200);
  assert.strictEqual(entry.note, 'Продукты');
  assert.strictEqual(entry.date, '2026-09-05');
});

test('без даты подставляется сегодня', () => {
  const res = Money.add(пустые(), 'income', 500, '', '');
  assert.ok(Model.isDate(res.data.money.entries[0].date));
});

test('ноль, минус, мусор и чужой вид операции не записываются', () => {
  for (const плохо of [0, -100, 'сто', NaN, null]) {
    const res = Money.add(пустые(), 'spend', плохо, '', '2026-09-05');
    assert.strictEqual(res.id, null, 'сумма ' + плохо);
    assert.strictEqual(res.data.money.entries.length, 0);
  }
  const чужой = Money.add(пустые(), 'подарил', 100, '', '2026-09-05');
  assert.strictEqual(чужой.id, null);
});

test('дробные рубли округляются до целых', () => {
  const res = Money.add(пустые(), 'spend', 1200.7, '', '2026-09-05');
  assert.strictEqual(res.data.money.entries[0].amount, 1201);
});

test('в кармане = приходы минус траты минус отложенное', () => {
  let data = пустые();
  data = Money.add(data, 'income', 100000, 'Зарплата', '2026-09-01').data;
  data = Money.add(data, 'spend', 12000, 'Аренда', '2026-09-02').data;
  data = Money.add(data, 'save', 30000, 'На цель', '2026-09-03').data;
  assert.strictEqual(Money.pocket(data), 58000);
  assert.strictEqual(Money.saved(data), 30000);
  assert.strictEqual(Money.spentTotal(data), 12000);
});

test('трата за месяц считается только по своему месяцу', () => {
  let data = пустые();
  data = Money.add(data, 'spend', 1000, '', '2026-08-31').data;
  data = Money.add(data, 'spend', 2000, '', '2026-09-01').data;
  data = Money.add(data, 'spend', 3000, '', '2026-09-30').data;
  assert.strictEqual(Money.spentInMonth(data, '2026-09'), 5000);
  assert.strictEqual(Money.spentInMonth(data, '2026-08'), 1000);
});

test('операция удаляется и суммы пересчитываются', () => {
  const added = Money.add(пустые(), 'spend', 700, 'Ошибка', '2026-09-05');
  const data = Money.remove(added.data, added.id);
  assert.strictEqual(data.money.entries.length, 0);
  assert.strictEqual(Money.spentTotal(data), 0);
});

test('цель ставится, отрицательная и мусорная становятся нулём', () => {
  assert.strictEqual(Money.setGoal(пустые(), 500000).money.goal, 500000);
  assert.strictEqual(Money.setGoal(пустые(), -5).money.goal, 0);
  assert.strictEqual(Money.setGoal(пустые(), 'много').money.goal, 0);
});

test('процент к цели не превышает ста и не делится на ноль', () => {
  let data = Money.setGoal(пустые(), 100000);
  data = Money.add(data, 'save', 46000, '', '2026-09-05').data;
  assert.deepStrictEqual(Money.progress(data), { saved: 46000, goal: 100000, percent: 46 });

  data = Money.add(data, 'save', 100000, '', '2026-09-06').data;
  assert.strictEqual(Money.progress(data).percent, 100);

  const безЦели = Money.add(пустые(), 'save', 5000, '', '2026-09-05').data;
  assert.strictEqual(Money.progress(безЦели).percent, 0);
});

test('лента операций — новые сверху, длина ограничена', () => {
  let data = пустые();
  data = Money.add(data, 'spend', 100, 'Раз', '2026-09-01').data;
  data = Money.add(data, 'spend', 200, 'Два', '2026-09-03').data;
  data = Money.add(data, 'spend', 300, 'Три', '2026-09-02').data;
  assert.deepStrictEqual(Money.entries(data, 2).map(e => e.note), ['Два', 'Три']);
});

test('суммы печатаются с разрядами и неразрывными пробелами', () => {
  assert.strictEqual(Money.format(40000), '40 000 ₽');
  assert.strictEqual(Money.format(0), '0 ₽');
  assert.strictEqual(Money.format(-1500), '−1 500 ₽');
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `node --test tests/money.test.js`
Expected: FAIL — `Cannot find module '../js/money.js'`.

- [ ] **Step 3: Написать модуль денег**

Создать `js/money.js`:

```js
(function (root) {
  'use strict';

  const Model = (typeof module !== 'undefined' && module.exports)
    ? require('./model.js')
    : root.Model;

  const KINDS = ['spend', 'income', 'save'];

  function add(data, kind, amount, note, date) {
    const next = Model.clone(data);
    const value = Math.round(Number(amount));
    if (KINDS.indexOf(kind) === -1) return { data: next, id: null };
    if (!Number.isFinite(value) || value <= 0) return { data: next, id: null };
    const entry = {
      id: Model.createId('m'),
      date: Model.isDate(String(date || '').trim()) ? String(date).trim() : Model.today(),
      kind: kind,
      amount: value,
      note: String(note == null ? '' : note).trim()
    };
    next.money.entries.push(entry);
    return { data: next, id: entry.id };
  }

  function remove(data, id) {
    const next = Model.clone(data);
    next.money.entries = next.money.entries.filter(e => e.id !== id);
    return next;
  }

  function setGoal(data, goal) {
    const next = Model.clone(data);
    const value = Math.round(Number(goal));
    next.money.goal = Number.isFinite(value) && value > 0 ? value : 0;
    return next;
  }

  function sumOf(data, kind) {
    return data.money.entries
      .filter(e => e.kind === kind)
      .reduce((total, e) => total + e.amount, 0);
  }

  function pocket(data) {
    return sumOf(data, 'income') - sumOf(data, 'spend') - sumOf(data, 'save');
  }

  function saved(data) { return sumOf(data, 'save'); }

  function spentTotal(data) { return sumOf(data, 'spend'); }

  function spentInMonth(data, month) {
    return data.money.entries
      .filter(e => e.kind === 'spend' && e.date.slice(0, 7) === month)
      .reduce((total, e) => total + e.amount, 0);
  }

  function progress(data) {
    const goal = data.money.goal;
    const now = saved(data);
    const percent = goal > 0 ? Math.min(100, Math.round(now / goal * 100)) : 0;
    return { saved: now, goal: goal, percent: percent };
  }

  function entries(data, limit) {
    const list = data.money.entries.slice().sort((a, b) => (a.date < b.date ? 1 : (a.date > b.date ? -1 : 0)));
    return limit ? list.slice(0, limit) : list;
  }

  function format(amount) {
    const value = Math.round(Number(amount) || 0);
    const digits = String(Math.abs(value)).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    /* минус типографский: в интерфейсе он не должен выглядеть дефисом */
    return (value < 0 ? '−' : '') + digits + ' ₽';
  }

  const Money = { KINDS, add, remove, setGoal, pocket, saved, spentTotal, spentInMonth, progress, entries, format };

  if (typeof module !== 'undefined' && module.exports) module.exports = Money;
  else root.Money = Money;
})(typeof globalThis !== 'undefined' ? globalThis : this);
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `node --test tests/money.test.js`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add js/money.js tests/money.test.js
git commit -m "Деньги: траты, приходы, накопления и цель"
```

---

### Task 3: Модель зала

**Files:**
- Create: `js/gym.js`
- Test: `tests/gym.test.js`

**Interfaces:**
- Consumes: `Model.clone`, `Model.createId`, `Model.today`, `Model.isDate`
- Produces: `Gym.add(data, {date, exercise, sets}) -> {data, id}`, `Gym.remove(data, id) -> data`, `Gym.list(data, limit) -> [workout]` (новые сверху), `Gym.last(data) -> workout|null`, `Gym.exercises(data) -> [строка]`, `Gym.record(data, exercise) -> число|null`, `Gym.series(data, exercise) -> [{date, weight}]`, `Gym.volume(workout) -> число`

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/gym.test.js`:

```js
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `node --test tests/gym.test.js`
Expected: FAIL — `Cannot find module '../js/gym.js'`.

- [ ] **Step 3: Написать модуль зала**

Создать `js/gym.js`:

```js
(function (root) {
  'use strict';

  const Model = (typeof module !== 'undefined' && module.exports)
    ? require('./model.js')
    : root.Model;

  function cleanSets(sets) {
    if (!Array.isArray(sets)) return [];
    return sets
      .map(s => ({ weight: Number(s && s.weight), reps: Number(s && s.reps) }))
      .filter(s => Number.isFinite(s.weight) && s.weight > 0 && Number.isFinite(s.reps) && s.reps > 0);
  }

  function add(data, input) {
    const next = Model.clone(data);
    const exercise = String((input && input.exercise) || '').trim();
    const sets = cleanSets(input && input.sets);
    if (!exercise || sets.length === 0) return { data: next, id: null };
    const date = String((input && input.date) || '').trim();
    const workout = {
      id: Model.createId('w'),
      date: Model.isDate(date) ? date : Model.today(),
      exercise: exercise,
      sets: sets
    };
    next.workouts.push(workout);
    return { data: next, id: workout.id };
  }

  function remove(data, id) {
    const next = Model.clone(data);
    next.workouts = next.workouts.filter(w => w.id !== id);
    return next;
  }

  function list(data, limit) {
    const all = data.workouts.slice().sort((a, b) => (a.date < b.date ? 1 : (a.date > b.date ? -1 : 0)));
    return limit ? all.slice(0, limit) : all;
  }

  function last(data) {
    const all = list(data, 1);
    return all.length ? all[0] : null;
  }

  function maxWeight(workout) {
    return workout.sets.reduce((top, s) => (s.weight > top ? s.weight : top), 0);
  }

  function exercises(data) {
    const seen = [];
    for (const w of list(data)) {
      if (seen.indexOf(w.exercise) === -1) seen.push(w.exercise);
    }
    return seen;
  }

  function record(data, exercise) {
    const мои = data.workouts.filter(w => w.exercise === exercise);
    if (мои.length === 0) return null;
    return мои.reduce((top, w) => Math.max(top, maxWeight(w)), 0);
  }

  function series(data, exercise) {
    /* одна точка на день: рабочий вес дня — наибольший из подходов */
    const byDate = new Map();
    for (const w of data.workouts) {
      if (w.exercise !== exercise) continue;
      const top = maxWeight(w);
      if (!byDate.has(w.date) || byDate.get(w.date) < top) byDate.set(w.date, top);
    }
    return Array.from(byDate.keys())
      .sort()
      .map(date => ({ date: date, weight: byDate.get(date) }));
  }

  function volume(workout) {
    return workout.sets.reduce((total, s) => total + s.weight * s.reps, 0);
  }

  const Gym = { add, remove, list, last, exercises, record, series, volume, maxWeight };

  if (typeof module !== 'undefined' && module.exports) module.exports = Gym;
  else root.Gym = Gym;
})(typeof globalThis !== 'undefined' ? globalThis : this);
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `node --test tests/gym.test.js`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add js/gym.js tests/gym.test.js
git commit -m "Зал: тренировки, веса, личные рекорды"
```

---

### Task 4: Заполнение башен, календарь и просрочка

**Files:**
- Create: `js/stats.js`
- Test: `tests/stats.test.js`

**Interfaces:**
- Consumes: `Model.SPHERES`, `Model.sphere`, `Money.saved`, `Money.progress`, `Money.format`
- Produces: `Stats.fill(data, sphereId) -> {filled, total}`, `Stats.label(data, sphereId) -> строка`, `Stats.tasksOn(data, 'ГГГГ-ММ-ДД') -> [task]`, `Stats.overdue(data, сегодня) -> [task]`, `Stats.upcoming(data, сегодня, limit) -> [task]`, `Stats.monthMarks(data, 'ГГГГ-ММ') -> {дата: [sphereId]}`, `Stats.monthGrid('ГГГГ-ММ') -> [[дата|null × 7]]`, `Stats.monthTitle('ГГГГ-ММ') -> 'Сентябрь 2026'`, `Stats.plural(n, [одна, две, пять]) -> строка`
- Правила заполнения (из спеки): задачные сферы — этажей столько же, сколько задач, заполнено = выполненные; зал — этаж за тренировку, все заполнены; деньги при цели больше нуля — всегда 20 этажей и заполнено по проценту, без цели — этаж за каждые 10 000 ₽ накоплений, все заполнены.

- [ ] **Step 1: Написать падающие тесты**

Создать `tests/stats.test.js`:

```js
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
  assert.strictEqual(Stats.label(data, 'money'), '46 % к цели');

  const безЦели = Money.add(пустые(), 'save', 5000, '', '2026-09-01').data;
  assert.strictEqual(Stats.label(безЦели, 'money'), '5 000 ₽');
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

test('метки месяца: по дням список сфер без повторов', () => {
  let data = пустые();
  data = Model.addTask(data, 'work', 'Раз', '2026-09-09').data;
  data = Model.addTask(data, 'work', 'Два', '2026-09-09').data;
  data = Model.addTask(data, 'study', 'Три', '2026-09-09').data;
  data = Model.addTask(data, 'social', 'Чужой месяц', '2026-10-01').data;
  assert.deepStrictEqual(Stats.monthMarks(data, '2026-09'), { '2026-09-09': ['work', 'study'] });
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

test('заголовок месяца по-русски', () => {
  assert.strictEqual(Stats.monthTitle('2026-09'), 'Сентябрь 2026');
  assert.strictEqual(Stats.monthTitle('2026-01'), 'Январь 2026');
});
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `node --test tests/stats.test.js`
Expected: FAIL — `Cannot find module '../js/stats.js'`.

- [ ] **Step 3: Написать модуль статистики**

Создать `js/stats.js`:

```js
(function (root) {
  'use strict';

  const isNode = typeof module !== 'undefined' && module.exports;
  const Model = isNode ? require('./model.js') : root.Model;
  const Money = isNode ? require('./money.js') : root.Money;

  const MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь',
    'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];

  const MONEY_FLOORS = 20;
  const MONEY_STEP = 10000;

  function plural(n, forms) {
    const десятки = Math.abs(n) % 100;
    const единицы = n % 10;
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
      if (прогресс.goal > 0) return прогресс.percent + ' % к цели';
      if (прогресс.saved > 0) return Money.format(прогресс.saved);
      return 'пусто';
    }

    const счёт = fill(data, sphereId);
    if (счёт.total === 0) return 'пусто';
    return счёт.filled + ' из ' + счёт.total;
  }

  function tasksOn(data, date) {
    return data.tasks.filter(t => t.due === date);
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

  function monthMarks(data, month) {
    const marks = {};
    for (const task of data.tasks) {
      if (!task.due || task.due.slice(0, 7) !== month) continue;
      if (!marks[task.due]) marks[task.due] = [];
      if (marks[task.due].indexOf(task.sphereId) === -1) marks[task.due].push(task.sphereId);
    }
    return marks;
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

  const Stats = {
    plural, fill, label, tasksOn, overdue, upcoming,
    monthMarks, monthGrid, monthTitle, shiftMonth, MONTHS
  };

  if (isNode) module.exports = Stats;
  else root.Stats = Stats;
})(typeof globalThis !== 'undefined' ? globalThis : this);
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `node --test tests/stats.test.js`
Expected: PASS.

- [ ] **Step 5: Коммит**

```bash
git add js/stats.js tests/stats.test.js
git commit -m "Счёт по башням, календарные метки и ближайшие сроки"
```

---

### Task 5: Хранилище второй версии и перенос старых данных

**Files:**
- Modify: `js/store.js` (полная замена содержимого)
- Test: `tests/store.test.js` (полная замена содержимого)

**Interfaces:**
- Consumes: `Model.DEFAULT_DATA`, `Model.clone`, `Model.parse`, `Model.serialize`
- Produces: `Store.KEY` (`'orientir-v2'`), `Store.OLD_KEY` (`'wishmap-v1'`), `Store.ARCHIVE_KEY` (`'wishmap-v1-archive'`), `Store.available(storage)`, `Store.load(storage) -> {data, source, warning, archive}`, `Store.save(storage, data) -> boolean`, `Store.exportName(date) -> 'orientir-ГГГГ-ММ-ДД.json'`
- `load` возвращает `archive` — строку со старым JSON, если перенос случился именно сейчас, иначе `null`. Интерфейс по ней показывает полосу «Прежняя карта желаний сохранена».

- [ ] **Step 1: Написать падающие тесты**

Полностью заменить `tests/store.test.js`:

```js
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `node --test tests/store.test.js`
Expected: FAIL — ключ всё ещё `wishmap-v1`, поля `archive` нет.

- [ ] **Step 3: Написать хранилище**

Полностью заменить `js/store.js`:

```js
(function (root) {
  'use strict';

  const KEY = 'orientir-v2';
  const OLD_KEY = 'wishmap-v1';
  const ARCHIVE_KEY = 'wishmap-v1-archive';

  const Model = (typeof module !== 'undefined' && module.exports)
    ? require('./model.js')
    : root.Model;

  function available(storage) {
    try {
      storage.setItem(KEY + '-probe', '1');
      storage.removeItem(KEY + '-probe');
      return true;
    } catch (e) {
      return false;
    }
  }

  function archiveOld(storage) {
    /* прежняя карта желаний не выбрасывается: она уезжает в отдельный ключ,
       и страница один раз предлагает скачать её файлом */
    try {
      const старое = storage.getItem(OLD_KEY);
      if (!старое) return null;
      if (!storage.getItem(ARCHIVE_KEY)) storage.setItem(ARCHIVE_KEY, старое);
      storage.removeItem(OLD_KEY);
      return старое;
    } catch (e) {
      return null;
    }
  }

  function fresh(warning, archive) {
    return {
      data: Model.clone(Model.DEFAULT_DATA),
      source: 'default',
      warning: warning || null,
      archive: archive || null
    };
  }

  function load(storage) {
    let archive = null;
    let raw = null;
    try {
      archive = archiveOld(storage);
      raw = storage.getItem(KEY);
    } catch (e) {
      return fresh('Браузер не даёт сохранять на этой странице. Правки не переживут перезагрузку — пользуйтесь кнопкой «Сохранить копию».', null);
    }

    if (!raw) return fresh(null, archive);

    const res = Model.parse(raw);
    if (!res.ok) {
      return fresh('Сохранённые данные повреждены: ' + res.error + '. Открыт чистый лист.', archive);
    }
    return { data: res.data, source: 'storage', warning: null, archive: archive };
  }

  function save(storage, data) {
    try {
      storage.setItem(KEY, Model.serialize(data));
      return true;
    } catch (e) {
      return false;
    }
  }

  function exportName(date) {
    const d = date || new Date();
    return 'orientir-' + d.toISOString().slice(0, 10) + '.json';
  }

  const Store = { KEY, OLD_KEY, ARCHIVE_KEY, available, load, save, exportName };

  if (typeof module !== 'undefined' && module.exports) module.exports = Store;
  else root.Store = Store;
})(typeof globalThis !== 'undefined' ? globalThis : this);
```

- [ ] **Step 4: Убедиться, что весь набор тестов проходит**

Run: `node --test tests/`
Expected: PASS по всем файлам — модель, деньги, зал, статистика, хранилище.

- [ ] **Step 5: Коммит**

```bash
git add js/store.js tests/store.test.js
git commit -m "Хранилище второй версии: перенос старой карты в архив"
```
