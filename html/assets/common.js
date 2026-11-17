/* Навігація, форматування, безпечний localStorage. Працює з file://, без зовнішніх ресурсів. */
(function () {
  'use strict';
  var PAGES = [
    { id: 'index', file: 'index.html', t: 'Карта', stage: 0 },
    { id: 'case', file: '1_case.html', t: 'Кейс', stage: 1 },
    { id: 'glossary', file: '1_glossary.html', t: 'Терміни й формули', stage: 1 },
    { id: 'questions', file: '1_questions.html', t: 'Питання на 18.11', stage: 1 },
    { id: 'data', file: '2_data.html', t: 'Дані та пошук', stage: 2 },
    { id: 'seasonality', file: '3a_seasonality.html', t: '1 · Сезонність', stage: 3 },
    { id: 'coeff', file: '3b_service_coeff.html', t: '2 · Сервіс і COEFF', stage: 3 },
    { id: 'scenarios', file: '3c_scenarios.html', t: '3 · Сценарії', stage: 3 },
    { id: 'deliver', file: '4_deliverables.html', t: 'Що здаємо', stage: 4 }
  ];
  var STAGES = ['', '1 Зрозуміти', '2 Дані', '3 Рахувати', '4 Здати'];

  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function renderNav() {
    var cur = document.body.getAttribute('data-page');
    var top = document.getElementById('top');
    if (!top) return;
    var html = '<div class="top-in"><a class="brand" href="index.html">АГРОСЕМ · Трактори (B)</a>';
    var lastStage = -1;
    PAGES.forEach(function (p) {
      if (p.id === 'index') return;
      if (p.stage !== lastStage) {
        if (lastStage !== -1) html += '</div>';
        html += '<div class="stage"><span class="stage-label">' + STAGES[p.stage] + '</span>';
        lastStage = p.stage;
      }
      html += '<a class="nav' + (p.id === cur ? ' on' : '') + '" href="' + p.file + '">' + esc(p.t) + '</a>';
    });
    html += '</div></div>';
    top.className = 'top';
    top.innerHTML = html;

    var pager = document.getElementById('pager');
    if (pager) {
      var i = PAGES.findIndex(function (p) { return p.id === cur; });
      var prev = PAGES[i - 1], next = PAGES[i + 1];
      pager.className = 'pager';
      pager.innerHTML = (prev ? '<a href="' + prev.file + '">← ' + esc(prev.t) + '</a>' : '<span></span>') +
        (next ? '<a href="' + next.file + '">' + esc(next.t) + ' →</a>' : '<span></span>');
    }
  }

  var fmtInt = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 0 });
  var fmt1 = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 1, minimumFractionDigits: 1 });
  var fmt2 = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 2, minimumFractionDigits: 2 });
  var fmt3 = new Intl.NumberFormat('uk-UA', { maximumFractionDigits: 3, minimumFractionDigits: 3 });

  function store(key, value) {
    try {
      if (value === undefined) { var v = localStorage.getItem('agrosem.' + key); return v === null ? null : JSON.parse(v); }
      localStorage.setItem('agrosem.' + key, JSON.stringify(value));
    } catch (e) { return null; }
  }

  function download(name, text, mime) {
    var blob = new Blob(['﻿' + text], { type: mime || 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob); a.download = name;
    document.body.appendChild(a); a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }

  /* Місячні мітки */
  var MON = ['січ', 'лют', 'бер', 'кві', 'тра', 'чер', 'лип', 'сер', 'вер', 'жов', 'лис', 'гру'];
  function monthLabel(ym) { return MON[parseInt(ym.slice(5), 10) - 1] + ' ' + ym.slice(2, 4); }

  /* Опис Critical Code (Додаток 3 кейсу) */
  var CC = {
    0: ['Невідомий код', 'У Додатку 3 коду 0 немає'],
    11: ['Періодичне ТО, витратні', 'Масла, фільтри, паси, лампи: клієнт очікує негайної видачі зі складу'],
    12: ['Зупинка машини, монтаж ≤ 2 год', 'Стартер, насоси, шланги, реле: без деталі техніка стоїть'],
    13: ['Деталі, що виготовляє дилер', 'Шланги збирають у дилера; тримати треба компоненти (вони CC 12)'],
    14: ['Зупинка машини, монтаж > 2 год', 'Орієнтир: наявність дилера або John Deere протягом 24 год'],
    15: ['Знос і ремонт', 'Не зупиняють машину одразу, але потрібні швидко: гальмівні диски, щітки'],
    20: ['Відновлення ресурсу', 'Капремонт, відновлення вузлів; простій можливий, але не завжди'],
    21: ['Метизи', 'Болти, гайки: товар широкого вжитку, не «зупиняє машину»'],
    50: ['Повільний рух (як CC 20)', 'Можливо не потрібно тримати на складі'],
    51: ['Повільний рух (як CC 11)', 'Рідкісні; не обов’язково на складі'],
    52: ['Повільний рух (як CC 12)', 'Рідкісні, але критичність базового коду зберігається'],
    54: ['Повільний рух (як CC 14)', 'Рідкісні, але критичність базового коду зберігається'],
    55: ['Повільний рух (як CC 15)', 'Рідкісні; не обов’язково на складі'],
    90: ['Авто-комплекти поліпшень (PIP)', 'Надсилаються автоматично'],
    91: ['Необов’язкові комплекти PIP', 'Замовлення на вибір'],
    99: ['Комерційні товари', 'Не оригінальні запчастини John Deere']
  };

  window.U = {
    PAGES: PAGES, esc: esc, fmtInt: function (x) { return fmtInt.format(x); }, fmt1: function (x) { return fmt1.format(x); },
    fmt2: function (x) { return fmt2.format(x); }, fmt3: function (x) { return fmt3.format(x); },
    pct: function (x, d) { return (x * 100).toFixed(d == null ? 0 : d).replace('.', ',') + '%'; },
    store: store, download: download, monthLabel: monthLabel, CC: CC,
    el: function (id) { return document.getElementById(id); }
  };
  /* <span data-sum="sku_n"></span> підставляє число з AGRO.summary (data-f: int | money | pct) */
  function bindSummary() {
    var S = window.AGRO && window.AGRO.summary;
    if (!S) return;
    Array.prototype.forEach.call(document.querySelectorAll('[data-sum]'), function (n) {
      var v = S[n.getAttribute('data-sum')];
      if (v == null) { n.textContent = '?'; return; }
      var f = n.getAttribute('data-f') || 'int';
      n.textContent = f === 'money' ? fmtInt.format(Math.round(v)) : f === 'f2' ? fmt2.format(v) : fmtInt.format(v);
    });
  }
  document.addEventListener('DOMContentLoaded', function () { renderNav(); bindSummary(); });
})();
