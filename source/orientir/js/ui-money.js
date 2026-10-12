(function (root) {
  'use strict';

  const el = () => root.Dom.el;

  let draft = { amount: '', note: '' };

  function render(host, ctx) {
    const e = el();
    const data = ctx.data;
    const Money = root.Money;
    const прогресс = Money.progress(data);
    const месяц = root.Model.today().slice(0, 7);

    host.appendChild(e('div', { class: 'figures' }, [
      e('div', { class: 'figure wide' }, [
        e('em', { text: 'В кармане' }),
        e('b', {
          text: Money.format(Money.pocket(data)),
          class: Money.pocket(data) < 0 ? 'out' : null
        })
      ]),
      e('div', { class: 'figure' }, [
        e('em', { text: 'Накоплено' }),
        e('b', { text: Money.format(прогресс.saved) }),
        goalLine(прогресс, ctx),
        прогресс.goal > 0 ? e('div', { class: 'bar' }, [
          e('i', { style: 'width:' + прогресс.percent + '%' })
        ]) : null
      ]),
      e('div', { class: 'figure' }, [
        e('em', { text: 'Потратил за месяц' }),
        e('b', { text: Money.format(Money.spentInMonth(data, месяц)) }),
        e('small', { text: 'всего ' + Money.format(Money.spentTotal(data)) })
      ])
    ]));

    const amount = e('input', {
      type: 'number', inputmode: 'numeric', min: '0', step: '1',
      placeholder: 'Сумма', value: draft.amount,
      oninput: ev => { draft.amount = ev.target.value; }
    });
    const note = e('input', {
      type: 'text', placeholder: 'На что', value: draft.note,
      oninput: ev => { draft.note = ev.target.value; }
    });

    function write(kind) {
      const res = root.Money.add(ctx.data, kind, draft.amount, draft.note, '');
      if (!res.id) { amount.focus(); return; }
      draft = { amount: '', note: '' };
      ctx.update(res.data, 'new');
    }

    amount.addEventListener('keydown', ev => { if (ev.key === 'Enter') note.focus(); });
    note.addEventListener('keydown', ev => { if (ev.key === 'Enter') write('spend'); });

    if (ctx.focus === 'new') setTimeout(() => amount.focus(), 0);

    host.appendChild(e('div', { class: 'field' }, [amount, note]));
    host.appendChild(e('div', { class: 'kinds' }, [
      e('button', { class: 'kind spend', type: 'button', text: 'Потратил', onclick: () => write('spend') }),
      e('button', { class: 'kind', type: 'button', text: 'Пришло', onclick: () => write('income') }),
      e('button', { class: 'kind save', type: 'button', text: 'Отложил', onclick: () => write('save') })
    ]));

    const список = root.Money.entries(data, 40);
    if (список.length === 0) {
      host.appendChild(e('p', { class: 'empty', text: 'Операций нет. Запишите первую трату или приход.' }));
      return;
    }

    host.appendChild(e('p', { class: 'sub', text: 'Последние операции' }));
    const rows = e('ul', { class: 'rows' });
    for (const entry of список) rows.appendChild(entryRow(entry, ctx));
    host.appendChild(rows);
  }

  const SIGN = { spend: '−', income: '+', save: '→' };
  const CLASS = { spend: 'out', income: 'in', save: 'put' };
  const WORD = { spend: 'трата', income: 'приход', save: 'отложено' };

  function entryRow(entry, ctx) {
    const e = el();
    return e('li', { class: 'row money-row' }, [
      e('div', { class: 'row-main' }, [
        e('div', { text: entry.note || WORD[entry.kind] }),
        e('div', { class: 'row-due', text: root.UI.shortDate(entry.date) })
      ]),
      e('b', { class: CLASS[entry.kind], text: SIGN[entry.kind] + ' ' + root.Money.format(entry.amount) }),
      e('button', {
        class: 'kill', type: 'button', text: '✕', 'aria-label': 'Удалить операцию',
        onclick: () => ctx.update(root.Money.remove(ctx.data, entry.id))
      })
    ]);
  }

  function goalLine(прогресс, ctx) {
    const e = el();
    const text = прогресс.goal > 0
      ? прогресс.percent + '\u00A0%\u00A0от ' + root.Money.format(прогресс.goal)
      : 'поставить цель';

    const button = e('button', {
      class: 'link', type: 'button', text: text, style: 'margin-top:4px'
    });

    button.addEventListener('click', () => {
      const input = e('input', {
        class: 'row-title', type: 'number', min: '0', step: '1000',
        placeholder: 'Цель, ₽',
        value: прогресс.goal > 0 ? прогресс.goal : '',
        style: 'margin-top:4px'
      });
      input.addEventListener('keydown', ev => { if (ev.key === 'Enter') ev.target.blur(); });
      input.addEventListener('blur', () => ctx.update(root.Money.setGoal(ctx.data, input.value)));
      button.replaceWith(input);
      input.focus();
    });

    return button;
  }

  root.PanelMoney = { render };
})(typeof globalThis !== 'undefined' ? globalThis : this);
