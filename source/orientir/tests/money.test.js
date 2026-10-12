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
  assert.strictEqual(Money.format(40000), '40\u00A0000\u00A0₽');
  assert.strictEqual(Money.format(0), '0\u00A0₽');
  assert.strictEqual(Money.format(-1500), '−' + '1\u00A0500\u00A0₽');
});
