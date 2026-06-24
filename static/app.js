'use strict';

// Cache de transações para evitar JSON inline em onclick
const _txCache = {};

// ═══════════════════════════════════════════════════════════════════════════
// ESTADO GLOBAL
// ═══════════════════════════════════════════════════════════════════════════

const now = new Date();
const state = {
  view: 'dashboard',
  dash: { month: now.getMonth() + 1, year: now.getFullYear(), evo_months: 6 },
  tx: {
    month: now.getMonth() + 1,
    year: now.getFullYear(),
    period: '',
    type: '',
    category: '',
    responsible: '',
    payment_method: '',
    search: '',
    invalid_only: false,
    sort_by: 'date',
    sort_order: 'desc',
    page: 1,
    perPage: 20,
  },
  report: { months: [now.getMonth() + 1], year: now.getFullYear(), sortBy: 'total', sortDir: 'desc', openCats: new Set(), sortedCats: [], data: null },
  categories: [],
  invalidCount: 0,
  editingTxId: null,
  editingCatId: null,
  confirmCallback: null,
  charts: {},
};

// ═══════════════════════════════════════════════════════════════════════════
// UTILITÁRIOS
// ═══════════════════════════════════════════════════════════════════════════

const BRL = new Intl.NumberFormat('pt-BR', { style: 'currency', currency: 'BRL' });
const fmt = (v) => (v == null ? '—' : BRL.format(v));
const fmtDate = (s) => {
  if (!s) return '—';
  const [y, m, d] = s.split('T')[0].split('-');
  return `${d}/${m}/${y}`;
};
const monthName = (m) =>
  ['Janeiro','Fevereiro','Março','Abril','Maio','Junho',
   'Julho','Agosto','Setembro','Outubro','Novembro','Dezembro'][m - 1];
const pct = (v, total) => (total ? ((v / total) * 100).toFixed(1) : '0.0');

function esc(str) {
  const d = document.createElement('div');
  d.textContent = str ?? '';
  return d.innerHTML;
}

function showToast(msg, type = 'success') {
  const t = document.createElement('div');
  t.style.cssText = `
    position:fixed;bottom:24px;right:24px;z-index:9999;
    background:${type === 'error' ? '#EF4444' : '#22C55E'};color:#fff;
    padding:10px 18px;border-radius:10px;font-weight:600;font-size:13px;
    box-shadow:0 4px 12px rgba(0,0,0,.2);
  `;
  t.textContent = msg;
  document.body.appendChild(t);
  setTimeout(() => t.remove(), 3000);
}

// ═══════════════════════════════════════════════════════════════════════════
// API
// ═══════════════════════════════════════════════════════════════════════════

const api = {
  async req(method, url, body) {
    const opts = { method, headers: { 'Content-Type': 'application/json' } };
    if (body !== undefined) opts.body = JSON.stringify(body);
    const res = await fetch(url, opts);
    if (res.status === 204) return null;
    let data;
    try { data = await res.json(); } catch { throw new Error(`Erro HTTP ${res.status}`); }
    if (!res.ok) {
      const d = data.detail;
      const msg = Array.isArray(d)
        ? d.map((e) => `${(e.loc || []).slice(-1)[0] ?? ''}: ${e.msg}`).join(' | ')
        : (typeof d === 'string' ? d : JSON.stringify(d));
      throw new Error(msg || `Erro ${res.status}`);
    }
    return data;
  },
  get: (url) => api.req('GET', url),
  post: (url, body) => api.req('POST', url, body),
  put: (url, body) => api.req('PUT', url, body),
  del: (url) => api.req('DELETE', url),
};

function buildQuery(params) {
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v !== '' && v !== null && v !== undefined && v !== false) q.set(k, v);
  }
  return q.toString() ? '?' + q.toString() : '';
}

// ═══════════════════════════════════════════════════════════════════════════
// BANNER DE DADOS INVÁLIDOS
// ═══════════════════════════════════════════════════════════════════════════

function updateInvalidBanner(count) {
  state.invalidCount = count;
  const banner = document.getElementById('invalid-banner');
  const text = document.getElementById('invalid-banner-text');
  if (count > 0) {
    text.textContent = `⚠ ${count} transaç${count > 1 ? 'ões precisam' : 'ão precisa'} de correção de valor`;
    banner.classList.remove('hidden');
  } else {
    banner.classList.add('hidden');
  }
}

function closeBanner() {
  document.getElementById('invalid-banner').classList.add('hidden');
}

function filterInvalid() {
  state.tx.invalid_only = true;
  state.tx.type = '';
  state.tx.category = '';
  state.tx.page = 1;
  navigate('transactions');
}

// ═══════════════════════════════════════════════════════════════════════════
// NAVEGAÇÃO
// ═══════════════════════════════════════════════════════════════════════════

function navigate(view) {
  state.view = view;
  document.querySelectorAll('.nav-item').forEach((el) =>
    el.classList.toggle('active', el.dataset.view === view)
  );
  renderView();
}

async function renderView() {
  const main = document.getElementById('main-content');
  main.innerHTML = '<div class="loading"><div class="spinner"></div></div>';
  try {
    if (state.view === 'dashboard')    await renderDashboard();
    else if (state.view === 'transactions') await renderTransactions();
    else if (state.view === 'categories')   await renderCategories();
    else if (state.view === 'report')       await renderReport();
  } catch (err) {
    main.innerHTML = `<div class="error-state">Erro ao carregar: ${esc(err.message)}</div>`;
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// SELECTS DE ANO / MÊS
// ═══════════════════════════════════════════════════════════════════════════

function monthOptions(selected) {
  return Array.from({ length: 12 }, (_, i) => {
    const m = i + 1;
    return `<option value="${m}" ${m === selected ? 'selected' : ''}>${monthName(m)}</option>`;
  }).join('');
}

function yearOptions(selected) {
  const years = [2024, 2025, 2026, 2027];
  return years.map((y) => `<option value="${y}" ${y === selected ? 'selected' : ''}>${y}</option>`).join('');
}

// ═══════════════════════════════════════════════════════════════════════════
// DASHBOARD
// ═══════════════════════════════════════════════════════════════════════════

async function renderDashboard() {
  const { month, year } = state.dash;
  const [summary, cats] = await Promise.all([
    api.get(`/api/summary?month=${month}&year=${year}`),
    api.get('/api/categories'),
  ]);
  state.categories = cats;
  updateInvalidBanner(summary.invalid_count);

  const expenseDelta = summary.total_expense - summary.prev_month_expense;
  const deltaSign = expenseDelta > 0 ? '+' : '';
  const deltaClass = expenseDelta > 0 ? 'delta-up' : expenseDelta < 0 ? 'delta-down' : 'delta-zero';

  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="view-header">
      <h1>Dashboard — ${monthName(month)} ${year}</h1>
      <div class="period-selector">
        <select id="dash-month">${monthOptions(month)}</select>
        <select id="dash-year">${yearOptions(year)}</select>
      </div>
    </div>

    <div class="cards-row">
      <div class="card card-income">
        <div class="card-icon">📈</div>
        <div class="card-label">Receitas</div>
        <div class="card-value">${fmt(summary.total_income)}</div>
        <div class="card-sub">${summary.transaction_count} transações no mês</div>
      </div>
      <div class="card card-expense">
        <div class="card-icon">💸</div>
        <div class="card-label">Despesas</div>
        <div class="card-value">${fmt(summary.total_expense)}</div>
        <div class="card-sub"><span class="${deltaClass}">${deltaSign}${fmt(expenseDelta)} vs mês anterior</span></div>
      </div>
      <div class="card card-investment">
        <div class="card-icon">💎</div>
        <div class="card-label">Investimentos</div>
        <div class="card-value">${fmt(summary.total_investment)}</div>
        <div class="card-sub">alocado no mês</div>
      </div>
      <div class="card card-balance">
        <div class="card-icon">⚖️</div>
        <div class="card-label">Saldo</div>
        <div class="card-value">${fmt(summary.balance)}</div>
        <div class="card-sub">${summary.balance >= 0 ? '✓ No azul' : '⚠ No vermelho'}</div>
      </div>
      ${summary.invalid_count > 0 ? `
      <div class="card card-warning" onclick="filterInvalid()">
        <div class="card-icon">⚠️</div>
        <div class="card-label">Correções Pendentes</div>
        <div class="card-value">${summary.invalid_count}</div>
        <div class="card-sub">transações sem valor — clique para ver</div>
      </div>` : ''}
    </div>

    <div class="charts-row">
      <div class="chart-card">
        <h3>Despesas por Categoria</h3>
        <div class="chart-container"><canvas id="cat-chart"></canvas></div>
      </div>
      <div class="chart-card">
        <h3>Despesas por Responsável</h3>
        <div id="resp-breakdown" class="resp-list"></div>
      </div>
    </div>

    <div class="chart-card evo-card">
      <div class="evo-header">
        <h3>Evolução Mensal</h3>
        <div class="evo-months-btns">
          <button onclick="setEvoMonths(3)">3 meses</button>
          <button onclick="setEvoMonths(6)">6 meses</button>
          <button onclick="setEvoMonths(12)">12 meses</button>
        </div>
      </div>
      <div class="chart-container" style="min-height:220px"><canvas id="evo-chart"></canvas></div>
    </div>

    <div class="table-card">
      <div class="section-header" style="padding:16px 16px 0">
        <span class="section-title">Transações Recentes</span>
        <button class="btn btn-ghost btn-sm" onclick="navigate('transactions')">Ver todas →</button>
      </div>
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th>Data</th>
              <th>Descrição</th>
              <th>Categoria</th>
              <th>Responsável</th>
              <th class="text-right">Valor</th>
            </tr>
          </thead>
          <tbody id="recent-tbody">
            ${summary.recent.map((t) => `
              <tr class="${t.amount_invalid ? 'row-invalid' : ''}">
                <td class="font-mono">${fmtDate(t.date)}</td>
                <td class="td-desc">
                  <span class="desc-text">${esc(t.description)}</span>
                  ${t.amount_invalid ? '<span class="badge badge-invalid" style="margin-top:2px">⚠ Inválido</span>' : ''}
                </td>
                <td><span class="badge badge-cat">${esc(t.category)}</span></td>
                <td>${esc(t.responsible || '—')}</td>
                <td class="text-right ${t.amount_invalid ? 'amount-unknown' : t.type === 'income' ? 'amount-income' : t.type === 'investment' ? 'amount-investment' : 'amount-expense'}">
                  ${t.amount_invalid ? '—' : t.type === 'income' ? '+' + fmt(t.amount) : t.type === 'investment' ? '▲' + fmt(t.amount) : '-' + fmt(t.amount)}
                </td>
              </tr>`).join('') || '<tr><td colspan="5" class="text-muted" style="text-align:center;padding:24px">Nenhuma transação neste mês</td></tr>'}
          </tbody>
        </table>
      </div>
    </div>
  `;

  // Gráfico de categorias
  if (summary.by_category.length > 0) {
    const ctx = document.getElementById('cat-chart').getContext('2d');
    if (state.charts.cat) state.charts.cat.destroy();
    const colors = ['#3B82F6','#EF4444','#22C55E','#F59E0B','#8B5CF6','#06B6D4','#EC4899','#84CC16','#F97316','#64748B'];
    state.charts.cat = new Chart(ctx, {
      type: 'doughnut',
      data: {
        labels: summary.by_category.map((c) => c.name),
        datasets: [{
          data: summary.by_category.map((c) => c.total),
          backgroundColor: colors,
          borderWidth: 2,
          borderColor: '#fff',
        }],
      },
      options: {
        responsive: true, maintainAspectRatio: false,
        plugins: {
          legend: { position: 'right', labels: { font: { size: 11 }, boxWidth: 12, padding: 10 } },
          tooltip: {
            callbacks: {
              label: (ctx) => ` ${ctx.label}: ${fmt(ctx.raw)} (${pct(ctx.raw, summary.total_expense)}%)`,
            },
          },
        },
      },
    });
  } else {
    document.getElementById('cat-chart').parentElement.innerHTML = '<div class="empty-state" style="min-height:180px">Sem despesas neste período</div>';
  }

  // Breakdown por responsável
  const respEl = document.getElementById('resp-breakdown');
  const byResp = summary.by_responsible;
  const maxResp = Math.max(...Object.values(byResp), 1);
  const respColors = { Nicolas: '#3B82F6', Camila: '#EC4899', Juntos: '#22C55E', Outros: '#94A3B8' };
  if (Object.keys(byResp).length > 0) {
    respEl.innerHTML = Object.entries(byResp)
      .sort((a, b) => b[1] - a[1])
      .map(([name, val]) => `
        <div class="resp-item">
          <div class="resp-header">
            <span class="resp-name">${esc(name || '—')}</span>
            <span class="resp-value">${fmt(val)}</span>
          </div>
          <div class="resp-bar">
            <div class="resp-bar-fill" style="width:${pct(val, maxResp)}%;background:${respColors[name] || '#64748B'}"></div>
          </div>
        </div>`).join('');
  } else {
    respEl.innerHTML = '<div class="empty-state" style="min-height:120px">Sem dados</div>';
  }

  // Gráfico de evolução
  await renderEvolution();

  // Event listeners do seletor de período
  document.getElementById('dash-month').addEventListener('change', (e) => {
    state.dash.month = +e.target.value;
    renderDashboard();
  });
  document.getElementById('dash-year').addEventListener('change', (e) => {
    state.dash.year = +e.target.value;
    renderDashboard();
  });
}

async function renderEvolution() {
  const n = state.dash.evo_months;
  // Atualiza botões ativos
  document.querySelectorAll('.evo-months-btns button').forEach((b) => {
    const months = parseInt(b.textContent);
    b.classList.toggle('active', months === n);
  });
  const data = await api.get(`/api/evolution?months=${n}`);
  const ctx = document.getElementById('evo-chart');
  if (!ctx) return;
  if (state.charts.evo) state.charts.evo.destroy();
  state.charts.evo = new Chart(ctx.getContext('2d'), {
    type: 'bar',
    data: {
      labels: data.map((d) => d.label),
      datasets: [
        { label: 'Receitas',      data: data.map((d) => d.income),     backgroundColor: '#22C55E', borderRadius: 4 },
        { label: 'Despesas',      data: data.map((d) => d.expense),    backgroundColor: '#EF4444', borderRadius: 4 },
        { label: 'Investimentos', data: data.map((d) => d.investment), backgroundColor: '#8B5CF6', borderRadius: 4 },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top', labels: { font: { size: 12 }, boxWidth: 14, padding: 16 } },
        tooltip: { callbacks: { label: (c) => ` ${c.dataset.label}: ${fmt(c.raw)}` } },
      },
      scales: {
        x: { grid: { display: false } },
        y: {
          grid: { color: '#F1F5F9' },
          ticks: { callback: (v) => 'R$ ' + new Intl.NumberFormat('pt-BR').format(v) },
        },
      },
    },
  });
}

async function setEvoMonths(n) {
  state.dash.evo_months = n;
  await renderEvolution();
}

// ═══════════════════════════════════════════════════════════════════════════
// TRANSAÇÕES
// ═══════════════════════════════════════════════════════════════════════════

function computePeriodRange(period, year) {
  const map = {
    q1:   [`${year}-01-01`, `${year}-03-31`],
    q2:   [`${year}-04-01`, `${year}-06-30`],
    q3:   [`${year}-07-01`, `${year}-09-30`],
    q4:   [`${year}-10-01`, `${year}-12-31`],
    s1:   [`${year}-01-01`, `${year}-06-30`],
    s2:   [`${year}-07-01`, `${year}-12-31`],
    year: [`${year}-01-01`, `${year}-12-31`],
  };
  return map[period] || [null, null];
}

function setSort(col) {
  if (state.tx.sort_by === col) {
    state.tx.sort_order = state.tx.sort_order === 'asc' ? 'desc' : 'asc';
  } else {
    state.tx.sort_by = col;
    state.tx.sort_order = 'desc';
  }
  state.tx.page = 1;
  renderTransactions();
}

async function renderTransactions() {
  const s = state.tx;
  const dateParams = s.period
    ? (() => { const [df, dt] = computePeriodRange(s.period, s.year); return { date_from: df, date_to: dt }; })()
    : { month: s.month, year: s.year };

  const q = buildQuery({
    ...dateParams,
    type: s.type, category: s.category,
    responsible: s.responsible, payment_method: s.payment_method,
    search: s.search,
    invalid_only: s.invalid_only || undefined,
    sort_by: s.sort_by, sort_order: s.sort_order,
    page: s.page, per_page: s.perPage,
  });

  const [data, cats] = await Promise.all([
    api.get(`/api/transactions${q}`),
    api.get('/api/categories'),
  ]);
  state.categories = cats;
  updateInvalidBanner(state.invalidCount);

  const catOptions = cats.map((c) => `<option value="${esc(c.name)}" ${c.name === s.category ? 'selected' : ''}>${esc(c.name)}</option>`).join('');

  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="view-header">
      <h1>Transações</h1>
      <div class="header-actions">
        <a href="/api/export${s.period ? buildQuery((() => { const [df,dt] = computePeriodRange(s.period, s.year); return {date_from: df, date_to: dt}; })()) : buildQuery({ month: s.month, year: s.year })}" class="btn btn-ghost btn-sm">↓ Exportar CSV</a>
        <button class="btn btn-primary btn-sm" onclick="openTransactionModal()">+ Nova Transação</button>
      </div>
    </div>

    ${s.invalid_only ? '<div class="alert-warning">⚠ Mostrando apenas transações com <strong>valor inválido</strong>. <a href="#" onclick="clearInvalidFilter()" style="color:#92400E;font-weight:700">Limpar filtro</a></div>' : ''}

    <div class="filters-bar">
      <div class="filter-group">
        <label>Período</label>
        <select id="f-period" onchange="togglePeriodFields()">
          <option value=""   ${s.period === ''    ? 'selected' : ''}>Mês</option>
          <option value="q1" ${s.period === 'q1'  ? 'selected' : ''}>Q1 (Jan-Mar)</option>
          <option value="q2" ${s.period === 'q2'  ? 'selected' : ''}>Q2 (Abr-Jun)</option>
          <option value="q3" ${s.period === 'q3'  ? 'selected' : ''}>Q3 (Jul-Set)</option>
          <option value="q4" ${s.period === 'q4'  ? 'selected' : ''}>Q4 (Out-Dez)</option>
          <option value="s1" ${s.period === 's1'  ? 'selected' : ''}>S1 (Jan-Jun)</option>
          <option value="s2" ${s.period === 's2'  ? 'selected' : ''}>S2 (Jul-Dez)</option>
          <option value="year" ${s.period === 'year' ? 'selected' : ''}>Ano todo</option>
        </select>
      </div>
      <div class="filter-group" id="f-month-wrap" ${s.period ? 'style="display:none"' : ''}>
        <label>Mês</label>
        <select id="f-month">${monthOptions(s.month)}</select>
      </div>
      <div class="filter-group">
        <label>Ano</label>
        <select id="f-year">${yearOptions(s.year)}</select>
      </div>
      <div class="filter-group">
        <label>Tipo</label>
        <select id="f-type">
          <option value="">Todos</option>
          <option value="expense"    ${s.type === 'expense'    ? 'selected' : ''}>Despesa</option>
          <option value="income"     ${s.type === 'income'     ? 'selected' : ''}>Receita</option>
          <option value="investment" ${s.type === 'investment' ? 'selected' : ''}>Investimento</option>
        </select>
      </div>
      <div class="filter-group">
        <label>Categoria</label>
        <select id="f-cat">
          <option value="">Todas</option>
          ${catOptions}
        </select>
      </div>
      <div class="filter-group">
        <label>Responsável</label>
        <select id="f-resp">
          <option value="">Todos</option>
          ${['Nicolas','Camila','Juntos','Outros'].map((r) => `<option ${r === s.responsible ? 'selected' : ''}>${r}</option>`).join('')}
        </select>
      </div>
      <div class="filter-group">
        <label>Busca</label>
        <input type="search" id="f-search" placeholder="Descrição..." value="${esc(s.search)}" />
      </div>
      <div class="filter-actions">
        <button class="btn btn-ghost btn-sm" onclick="clearFilters()">Limpar</button>
        <button class="btn btn-primary btn-sm" onclick="applyFilters()">Filtrar</button>
      </div>
    </div>

    <div class="table-card">
      <div class="table-scroll">
        <table>
          <thead>
            <tr>
              <th class="th-sort ${s.sort_by === 'date' ? s.sort_order : ''}" onclick="setSort('date')">Data</th>
              <th>Descrição</th>
              <th>Categoria</th>
              <th>Método</th>
              <th>Responsável</th>
              <th class="text-right th-sort ${s.sort_by === 'amount' ? s.sort_order : ''}" onclick="setSort('amount')">Valor</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            ${data.items.length === 0
              ? '<tr><td colspan="7" style="text-align:center;padding:32px;color:#64748B">Nenhuma transação encontrada</td></tr>'
              : data.items.map((t) => {
                  _txCache[t.id] = t;
                  return `
                <tr class="${t.amount_invalid ? 'row-invalid' : ''}">
                  <td class="font-mono" style="white-space:nowrap">${fmtDate(t.date)}</td>
                  <td class="td-desc">
                    <span class="desc-text">${esc(t.description)}</span>
                    ${t.notes ? `<span class="desc-notes">${esc(t.notes)}</span>` : ''}
                    ${t.amount_invalid ? '<span class="badge badge-invalid" style="margin-top:2px">⚠ Valor inválido</span>' : ''}
                  </td>
                  <td><span class="badge badge-cat">${esc(t.category)}</span></td>
                  <td class="text-muted" style="font-size:12px">${esc(t.payment_method || '—')}</td>
                  <td class="text-muted" style="font-size:12px">${esc(t.responsible || '—')}</td>
                  <td class="text-right ${t.amount_invalid ? 'amount-unknown' : t.type === 'income' ? 'amount-income' : t.type === 'investment' ? 'amount-investment' : 'amount-expense'}">
                    ${t.amount_invalid ? '<span title="Valor pendente de correção">—</span>' : t.type === 'income' ? '+' + fmt(t.amount) : t.type === 'investment' ? '▲' + fmt(t.amount) : '-' + fmt(t.amount)}
                  </td>
                  <td>
                    <div class="row-actions">
                      <button class="btn-icon" title="Editar" onclick="editTransaction('${t.id}')">✏</button>
                      <button class="btn-icon danger" title="Excluir" onclick="deleteTransaction('${t.id}', '${esc(t.description).replace(/'/g, "\\'")}')">🗑</button>
                    </div>
                  </td>
                </tr>`;
                }).join('')}
          </tbody>
        </table>
      </div>

      <div class="pagination">
        <span>${data.total} transaç${data.total !== 1 ? 'ões' : 'ão'} — Página ${data.page} de ${data.total_pages}</span>
        <div class="pagination-pages">
          <button class="page-btn" onclick="goPage(1)" ${s.page === 1 ? 'disabled' : ''}>«</button>
          <button class="page-btn" onclick="goPage(${s.page - 1})" ${s.page === 1 ? 'disabled' : ''}>‹</button>
          ${buildPageButtons(data.page, data.total_pages)}
          <button class="page-btn" onclick="goPage(${s.page + 1})" ${s.page >= data.total_pages ? 'disabled' : ''}>›</button>
          <button class="page-btn" onclick="goPage(${data.total_pages})" ${s.page >= data.total_pages ? 'disabled' : ''}>»</button>
        </div>
      </div>
    </div>
  `;

  // Enter key on search
  document.getElementById('f-search').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') applyFilters();
  });
}

function buildPageButtons(current, total) {
  const pages = [];
  let start = Math.max(1, current - 2);
  let end = Math.min(total, start + 4);
  if (end - start < 4) start = Math.max(1, end - 4);
  for (let i = start; i <= end; i++) {
    pages.push(`<button class="page-btn ${i === current ? 'active' : ''}" onclick="goPage(${i})">${i}</button>`);
  }
  return pages.join('');
}

function togglePeriodFields() {
  const period = document.getElementById('f-period').value;
  const wrap = document.getElementById('f-month-wrap');
  if (wrap) wrap.style.display = period ? 'none' : '';
}

function applyFilters() {
  state.tx.period = document.getElementById('f-period').value;
  state.tx.year = +document.getElementById('f-year').value;
  if (!state.tx.period) {
    state.tx.month = +document.getElementById('f-month').value;
  }
  state.tx.type = document.getElementById('f-type').value;
  state.tx.category = document.getElementById('f-cat').value;
  state.tx.responsible = document.getElementById('f-resp').value;
  state.tx.search = document.getElementById('f-search').value.trim();
  state.tx.page = 1;
  renderTransactions();
}

function clearFilters() {
  state.tx = { ...state.tx, period: '', type: '', category: '', responsible: '', payment_method: '', search: '', invalid_only: false, sort_by: 'date', sort_order: 'desc', page: 1 };
  renderTransactions();
}

function clearInvalidFilter() {
  state.tx.invalid_only = false;
  renderTransactions();
  return false;
}

function goPage(p) {
  state.tx.page = p;
  renderTransactions();
}

// ═══════════════════════════════════════════════════════════════════════════
// MODAL DE TRANSAÇÃO
// ═══════════════════════════════════════════════════════════════════════════

async function openTransactionModal(tx = null) {
  // Garantir categorias carregadas
  if (!state.categories.length) {
    state.categories = await api.get('/api/categories');
  }

  const modal = document.getElementById('tx-modal');
  const form = document.getElementById('tx-form');
  const title = document.getElementById('modal-title');
  const catSelect = document.getElementById('form-category');
  const invalidRow = document.getElementById('invalid-flag-row');

  state.editingTxId = tx ? tx.id : null;
  title.textContent = tx ? 'Editar Transação' : 'Nova Transação';

  // Reset PRIMEIRO, depois popula
  form.reset();

  // Popula categorias
  const currentCat = tx ? tx.category : '';
  catSelect.innerHTML = state.categories.map((c) =>
    `<option value="${esc(c.name)}" ${c.name === currentCat ? 'selected' : ''}>${esc(c.name)}</option>`
  ).join('');

  if (tx) {
    form.description.value = tx.description || '';
    form.type.value = tx.type || 'expense';
    // Exibe o valor atual mesmo que inválido (para o usuário confirmar/corrigir)
    form.amount.value = tx.amount != null ? tx.amount : '';
    form.date.value = tx.date ? tx.date.split('T')[0] : '';
    form.responsible.value = tx.responsible || '';
    form.payment_method.value = tx.payment_method || '';
    form.notes.value = tx.notes || '';
    catSelect.value = tx.category || '';
    document.getElementById('amount-invalid-cb').checked = !!tx.amount_invalid;
    invalidRow.style.display = 'block';
  } else {
    form.date.value = new Date().toISOString().split('T')[0];
    invalidRow.style.display = 'none';
  }

  modal.classList.remove('hidden');
}

function closeTransactionModal() {
  document.getElementById('tx-modal').classList.add('hidden');
  state.editingTxId = null;
}

function editTransaction(id) {
  const tx = _txCache[id];
  if (!tx) { showToast('Transação não encontrada no cache', 'error'); return; }
  openTransactionModal(tx);
}

async function submitTransaction(e) {
  e.preventDefault();
  const form = e.target;
  const btn = document.getElementById('submit-btn');
  btn.disabled = true;
  btn.textContent = 'Salvando...';

  const amountVal = form.amount.value ? parseFloat(form.amount.value) : null;
  const isInvalidCb = document.getElementById('amount-invalid-cb').checked;

  const payload = {
    description: form.description.value.trim(),
    amount: amountVal,
    type: form.type.value,
    category: form.category.value,
    payment_method: form.payment_method.value || null,
    responsible: form.responsible.value || null,
    notes: form.notes.value.trim() || null,
    date: form.date.value || null,
    amount_invalid: amountVal == null ? true : isInvalidCb,
  };

  try {
    if (state.editingTxId) {
      await api.put(`/api/transactions/${state.editingTxId}`, payload);
      showToast('Transação atualizada!');
    } else {
      await api.post('/api/transactions', payload);
      showToast('Transação criada!');
    }
    closeTransactionModal();
    if (state.view === 'dashboard') renderDashboard();
    else renderTransactions();
  } catch (err) {
    showToast('Erro: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Salvar';
  }
}

function deleteTransaction(id, desc) {
  showConfirm(
    'Excluir Transação',
    `Deseja excluir "${desc}"? Esta ação não pode ser desfeita.`,
    async () => {
      await api.del(`/api/transactions/${id}`);
      showToast('Transação excluída');
      if (state.view === 'dashboard') renderDashboard();
      else renderTransactions();
    }
  );
}

// ═══════════════════════════════════════════════════════════════════════════
// CATEGORIAS
// ═══════════════════════════════════════════════════════════════════════════

async function renderCategories() {
  const cats = await api.get('/api/categories');
  state.categories = cats;

  const main = document.getElementById('main-content');
  main.innerHTML = `
    <div class="view-header">
      <h1>Categorias</h1>
      <button class="btn btn-primary btn-sm" onclick="openCatModal()">+ Nova Categoria</button>
    </div>

    <div class="table-card">
      <div class="cat-list">
        ${cats.length === 0
          ? '<div class="empty-state"><span class="empty-icon">🏷️</span><p>Nenhuma categoria cadastrada</p></div>'
          : cats.map((c) => `
            <div class="cat-item">
              <div>
                <span class="cat-name">${esc(c.name)}</span>
                <span class="cat-count">${c.transaction_count} transaç${c.transaction_count !== 1 ? 'ões' : 'ão'}</span>
              </div>
              <div class="cat-actions">
                <button class="btn-icon" onclick="openCatModal('${c.id}', '${esc(c.name)}')">✏ Renomear</button>
                <button class="btn-icon danger" onclick="deleteCategory('${c.id}', '${esc(c.name)}', ${c.transaction_count})">🗑</button>
              </div>
            </div>`).join('')}
      </div>
    </div>
  `;
}

function openCatModal(id = '', name = '') {
  state.editingCatId = id || null;
  document.getElementById('cat-modal-title').textContent = id ? 'Renomear Categoria' : 'Nova Categoria';
  document.getElementById('cat-name-input').value = name;
  document.getElementById('cat-edit-id').value = id;
  document.getElementById('cat-modal').classList.remove('hidden');
}

function closeCatModal() {
  document.getElementById('cat-modal').classList.add('hidden');
  state.editingCatId = null;
}

async function submitCategory(e) {
  e.preventDefault();
  const name = document.getElementById('cat-name-input').value.trim();
  const id = document.getElementById('cat-edit-id').value;
  try {
    if (id) {
      await api.put(`/api/categories/${id}`, { name });
      showToast('Categoria renomeada e atualizada em todas as transações!');
    } else {
      await api.post('/api/categories', { name });
      showToast('Categoria criada!');
    }
    closeCatModal();
    renderCategories();
  } catch (err) {
    showToast('Erro: ' + err.message, 'error');
  }
}

function deleteCategory(id, name, count) {
  if (count > 0) {
    // Precisa reatribuir — simplificado: solicita confirmação e move para "Outros"
    showConfirm(
      'Excluir Categoria',
      `A categoria "${name}" possui ${count} transaç${count > 1 ? 'ões' : 'ão'}. As transações serão reatribuídas para "Outros".`,
      async () => {
        await api.del(`/api/categories/${id}?reassign_to=Outros`);
        showToast('Categoria excluída. Transações movidas para "Outros".');
        renderCategories();
      }
    );
  } else {
    showConfirm(
      'Excluir Categoria',
      `Deseja excluir a categoria "${name}"?`,
      async () => {
        await api.del(`/api/categories/${id}`);
        showToast('Categoria excluída!');
        renderCategories();
      }
    );
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// RELATÓRIO
// ═══════════════════════════════════════════════════════════════════════════

async function renderReport() {
  const { months, year } = state.report;
  const data = await api.get(`/api/report/multi?months=${months.join(',')}&year=${year}`);
  state.report.data = data;
  state.report.openCats = new Set();

  const main = document.getElementById('main-content');

  const shortMonths = ['Jan','Fev','Mar','Abr','Mai','Jun','Jul','Ago','Set','Out','Nov','Dez'];
  const pillsHtml = shortMonths.map((name, i) => {
    const m = i + 1;
    return `<button class="month-pill${months.includes(m) ? ' active' : ''}" data-month="${m}">${name}</button>`;
  }).join('');

  const periodLabel = months.length === 1
    ? `${monthName(months[0])} ${year}`
    : `${months.length} meses — ${year}`;

  main.innerHTML = `
    <div class="view-header">
      <h1>Relatório — ${periodLabel}</h1>
      <div class="header-actions">
        <div class="period-selector" style="flex-wrap:wrap;gap:8px;align-items:center">
          <select id="rep-year">${yearOptions(year)}</select>
          <div class="month-pills">${pillsHtml}</div>
        </div>
      </div>
    </div>

    <div class="report-summary">
      <div class="card card-expense">
        <div class="card-label">Total Despesas</div>
        <div class="card-value">${fmt(data.total_expense)}</div>
      </div>
      <div class="card card-income">
        <div class="card-label">Total Receitas</div>
        <div class="card-value">${fmt(data.total_income)}</div>
      </div>
      <div class="card card-balance">
        <div class="card-label">Saldo</div>
        <div class="card-value">${fmt(data.total_income - data.total_expense)}</div>
      </div>
    </div>

    ${data.categories.length > 0 ? `
    <div class="charts-row" style="margin-bottom:24px">
      <div class="chart-card" style="grid-column:1/-1">
        <h3>Despesas por Categoria</h3>
        <div class="chart-container" style="height:280px"><canvas id="report-chart"></canvas></div>
      </div>
    </div>
    ` : ''}

    <div class="table-card">
      <div style="padding:16px 16px 0"><span class="section-title">Gastos por Categoria</span></div>
      <div class="table-scroll" id="cat-breakdown-body">
        ${renderReportCatRows(data)}
      </div>
    </div>
  `;

  if (data.categories.length > 0) {
    const ctx = document.getElementById('report-chart').getContext('2d');
    if (state.charts.report) state.charts.report.destroy();
    state.charts.report = new Chart(ctx, {
      type: 'bar',
      data: {
        labels: data.categories.map((c) => c.name),
        datasets: [{
          label: 'Total',
          data: data.categories.map((c) => c.total),
          backgroundColor: '#3B82F6',
          borderRadius: 6,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => ` ${fmt(ctx.raw)}` } },
        },
        scales: {
          y: {
            ticks: { callback: (v) => 'R$' + new Intl.NumberFormat('pt-BR').format(v) },
            beginAtZero: true,
          },
        },
      },
    });
  }

  document.getElementById('rep-year').addEventListener('change', (e) => {
    state.report.year = +e.target.value;
    renderReport();
  });

  document.querySelectorAll('.month-pill').forEach((btn) => {
    btn.addEventListener('click', () => {
      const m = +btn.dataset.month;
      const idx = state.report.months.indexOf(m);
      if (idx >= 0) {
        if (state.report.months.length === 1) return;
        state.report.months.splice(idx, 1);
      } else {
        state.report.months.push(m);
        state.report.months.sort((a, b) => a - b);
      }
      renderReport();
    });
  });

  attachCatRowListeners();
}

function renderReportCatRows(data) {
  const { sortBy, sortDir, openCats } = state.report;

  const cats = [...data.categories].sort((a, b) => {
    let va = a[sortBy], vb = b[sortBy];
    if (sortBy === 'name') { va = va.toLowerCase(); vb = vb.toLowerCase(); }
    if (va < vb) return sortDir === 'asc' ? -1 : 1;
    if (va > vb) return sortDir === 'asc' ? 1 : -1;
    return 0;
  });

  state.report.sortedCats = cats;

  const si = (col) => {
    if (state.report.sortBy !== col) return '<span class="sort-icon">↕</span>';
    return `<span class="sort-icon active">${state.report.sortDir === 'asc' ? '↑' : '↓'}</span>`;
  };

  if (cats.length === 0) {
    return `<table><tbody><tr><td colspan="3" style="text-align:center;padding:32px;color:#64748B">Sem despesas no período selecionado</td></tr></tbody></table>`;
  }

  return `<table>
    <thead>
      <tr>
        <th class="cat-sort-th" data-col="name">Categoria ${si('name')}</th>
        <th class="text-right cat-sort-th" data-col="total">Total ${si('total')}</th>
        <th class="text-right cat-sort-th" data-col="count">Qtd ${si('count')}</th>
      </tr>
    </thead>
    <tbody>
      ${cats.map((c, idx) => {
        const isOpen = openCats.has(c.name);
        const txRows = c.transactions.map((t) => {
          const dayMonth = t.date ? t.date.slice(8, 10) + '/' + t.date.slice(5, 7) : '—';
          return `<tr>
            <td class="inner-tx-desc">${esc(t.description)}</td>
            <td class="text-right amount-expense">${fmt(t.amount)}</td>
            <td class="text-right text-muted">${dayMonth}</td>
          </tr>`;
        }).join('');
        return `
        <tr class="cat-row" data-idx="${idx}">
          <td>
            <span class="cat-toggle${isOpen ? ' open' : ''}">▶</span>
            <span class="badge badge-cat">${esc(c.name)}</span>
          </td>
          <td class="text-right amount-expense">${fmt(c.total)}</td>
          <td class="text-right text-muted">${c.count}</td>
        </tr>
        <tr class="cat-detail-row${isOpen ? '' : ' hidden'}" data-idx-detail="${idx}">
          <td colspan="3" class="cat-detail-td">
            <table class="inner-tx-table">
              <thead>
                <tr>
                  <th class="inner-tx-desc">Descrição</th>
                  <th class="text-right">Valor</th>
                  <th class="text-right">Data</th>
                </tr>
              </thead>
              <tbody>${txRows}</tbody>
              <tfoot>
                <tr>
                  <td class="inner-tx-desc"><strong>Total ${esc(c.name)}</strong></td>
                  <td class="text-right"><strong class="amount-expense">${fmt(c.total)}</strong></td>
                  <td></td>
                </tr>
              </tfoot>
            </table>
          </td>
        </tr>`;
      }).join('')}
    </tbody>
    <tfoot>
      <tr style="border-top:2px solid #E2E8F0;background:#F8FAFC">
        <td><strong>Total</strong></td>
        <td class="text-right"><strong class="amount-expense">${fmt(data.total_expense)}</strong></td>
        <td class="text-right"><strong>${data.categories.reduce((s, c) => s + c.count, 0)}</strong></td>
      </tr>
    </tfoot>
  </table>`;
}

function attachCatRowListeners() {
  document.querySelectorAll('.cat-row').forEach((row) => {
    row.addEventListener('click', () => {
      const idx = +row.dataset.idx;
      const catName = state.report.sortedCats[idx].name;
      const detailRow = document.querySelector(`.cat-detail-row[data-idx-detail="${idx}"]`);
      const toggle = row.querySelector('.cat-toggle');
      if (!detailRow) return;
      if (state.report.openCats.has(catName)) {
        state.report.openCats.delete(catName);
        detailRow.classList.add('hidden');
        toggle.classList.remove('open');
      } else {
        state.report.openCats.add(catName);
        detailRow.classList.remove('hidden');
        toggle.classList.add('open');
      }
    });
  });

  document.querySelectorAll('.cat-sort-th').forEach((th) => {
    th.addEventListener('click', () => {
      const col = th.dataset.col;
      if (state.report.sortBy === col) {
        state.report.sortDir = state.report.sortDir === 'desc' ? 'asc' : 'desc';
      } else {
        state.report.sortBy = col;
        state.report.sortDir = col === 'name' ? 'asc' : 'desc';
      }
      document.getElementById('cat-breakdown-body').innerHTML = renderReportCatRows(state.report.data);
      attachCatRowListeners();
    });
  });
}

// ═══════════════════════════════════════════════════════════════════════════
// MODAL DE CONFIRMAÇÃO
// ═══════════════════════════════════════════════════════════════════════════

function showConfirm(title, message, onConfirm) {
  document.getElementById('confirm-title').textContent = title;
  document.getElementById('confirm-message').textContent = message;
  state.confirmCallback = onConfirm;
  document.getElementById('confirm-modal').classList.remove('hidden');
}

function closeConfirmModal() {
  document.getElementById('confirm-modal').classList.add('hidden');
  state.confirmCallback = null;
}

document.getElementById('confirm-ok').addEventListener('click', async () => {
  if (!state.confirmCallback) return;
  const btn = document.getElementById('confirm-ok');
  btn.disabled = true;
  btn.textContent = 'Aguarde...';
  try {
    await state.confirmCallback();
  } catch (err) {
    showToast('Erro: ' + err.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = 'Confirmar';
    closeConfirmModal();
  }
});

// ═══════════════════════════════════════════════════════════════════════════
// FECHAR MODAIS COM ESC OU CLIQUE NO OVERLAY
// ═══════════════════════════════════════════════════════════════════════════

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeTransactionModal();
    closeConfirmModal();
    closeCatModal();
  }
});

['tx-modal', 'confirm-modal', 'cat-modal'].forEach((id) => {
  document.getElementById(id).addEventListener('click', (e) => {
    if (e.target === e.currentTarget) {
      closeTransactionModal();
      closeConfirmModal();
      closeCatModal();
    }
  });
});

// ═══════════════════════════════════════════════════════════════════════════
// INICIALIZAÇÃO
// ═══════════════════════════════════════════════════════════════════════════

async function init() {
  // Verificar contagem de inválidos para mostrar banner imediatamente
  try {
    const summary = await api.get(`/api/summary?month=${state.dash.month}&year=${state.dash.year}`);
    updateInvalidBanner(summary.invalid_count);
  } catch (_) {}

  navigate('dashboard');
}

init();
