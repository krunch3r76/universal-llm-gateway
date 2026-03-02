/**
 * OpenRouter Model Browser — client-side table rendering, sort, and filter.
 *
 * Sort behaviour:
 *   - Clicking "Modality" groups by modality, then sorts by the active cost
 *     column (prompt or completion) as secondary key.
 *   - Clicking a cost column sorts by that cost; modality sort uses that same
 *     column as its secondary key going forward.
 *   - Default: prefer text->text modality chip when present (else All),
 *     sorted by modality then completion cost ascending.
 */

(function () {
  'use strict';

  const DOM = {
    tableBody:    document.getElementById('table-body'),
    searchInput:  document.getElementById('search-input'),
    refreshBtn:   document.getElementById('refresh-btn'),
    modelCount:   document.getElementById('model-count'),
    lastRefresh:  document.getElementById('last-refresh'),
    modalityBar:  document.getElementById('modality-bar'),
  };

  let allModels    = [];
  let sortCol      = 'modality';   // primary sort column key
  let sortAsc      = true;
  let costSortCol  = 'completion_cost'; // secondary cost key when sorting by modality
  let activeModality = null;            // null = show all

  // ── Formatting ──────────────────────────────────────────────────────────

  function formatCost(perMillion) {
    if (perMillion === 0) return 'Free';
    if (perMillion < 0.01) return '$' + perMillion.toFixed(4);
    if (perMillion < 1)    return '$' + perMillion.toFixed(3);
    return '$' + perMillion.toFixed(2);
  }

  function costClass(perMillion) {
    if (perMillion === 0) return 'price-free';
    if (perMillion < 1)   return 'price-cheap';
    if (perMillion < 10)  return 'price-mid';
    return 'price-expensive';
  }

  function formatContext(len) {
    if (!len) return '\u2014';
    if (len >= 1_000_000) return (len / 1_000_000).toFixed(1) + 'M';
    if (len >= 1000)      return Math.round(len / 1000) + 'k';
    return String(len);
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  // ── Modality chips ───────────────────────────────────────────────────────

  function buildModalityChips(models) {
    if (!DOM.modalityBar) return;

    const counts = {};
    for (const m of models) {
      const mod = m.modality || 'unknown';
      counts[mod] = (counts[mod] || 0) + 1;
    }

    // Order: text->text first, then alphabetical
    const modalities = Object.keys(counts).sort((a, b) => {
      if (a === 'text->text') return -1;
      if (b === 'text->text') return 1;
      return a.localeCompare(b);
    });

    if (activeModality === null && Object.hasOwn(counts, 'text->text')) {
      activeModality = 'text->text';
    }
    if (activeModality !== null && !Object.hasOwn(counts, activeModality)) {
      activeModality = null;
    }

    const chips = [{ label: 'All', value: null }, ...modalities.map(m => ({
      label: `${m} (${counts[m]})`,
      value: m,
    }))];

    DOM.modalityBar.innerHTML = chips.map(c => {
      const active = c.value === activeModality ? ' chip-active' : '';
      const val = c.value === null ? '' : escapeHtml(c.value);
      return `<button class="chip${active}" data-modality="${val}">${escapeHtml(c.label)}</button>`;
    }).join('');

    DOM.modalityBar.querySelectorAll('.chip').forEach(btn => {
      btn.addEventListener('click', () => {
        activeModality = btn.dataset.modality || null;
        applyFilterAndSort();
        // Update active chip styling
        DOM.modalityBar.querySelectorAll('.chip').forEach(b => b.classList.remove('chip-active'));
        btn.classList.add('chip-active');
      });
    });
  }

  // ── Rendering ───────────────────────────────────────────────────────────

  function renderTable(models) {
    if (!models.length) {
      DOM.tableBody.innerHTML =
        '<tr><td colspan="6" class="empty-state">No models match your filter.</td></tr>';
      return;
    }

    const rows = models.map(m => {
      const promptClass = costClass(m.prompt_cost);
      const compClass   = costClass(m.completion_cost);
      return `<tr>
        <td class="col-name" title="${escapeHtml(m.id)}">${escapeHtml(m.name)}</td>
        <td class="col-provider">${escapeHtml(m.provider)}</td>
        <td class="col-cost ${promptClass}">${formatCost(m.prompt_cost)}</td>
        <td class="col-cost ${compClass}">${formatCost(m.completion_cost)}</td>
        <td class="col-num">${formatContext(m.context_length)}</td>
        <td class="col-modality">${escapeHtml(m.modality || '\u2014')}</td>
      </tr>`;
    });

    DOM.tableBody.innerHTML = rows.join('');
  }

  function updateCountDisplay(shown, total) {
    DOM.modelCount.textContent = shown === total
      ? `${total} models`
      : `${shown} / ${total} models`;
  }

  // ── Sort ─────────────────────────────────────────────────────────────────

  function cmpValues(av, bv, asc) {
    if (typeof av === 'string' && typeof bv === 'string') {
      const cmp = av.localeCompare(bv, undefined, { sensitivity: 'base' });
      return asc ? cmp : -cmp;
    }
    const na = Number(av) || 0;
    const nb = Number(bv) || 0;
    return asc ? na - nb : nb - na;
  }

  function sortModels(models) {
    return [...models].sort((a, b) => {
      if (sortCol === 'modality') {
        // Primary: modality group; Secondary: active cost column ascending
        const modCmp = cmpValues(a.modality || '', b.modality || '', sortAsc);
        if (modCmp !== 0) return modCmp;
        return cmpValues(a[costSortCol], b[costSortCol], true);
      }

      // Cost or other column — simple single-key sort
      return cmpValues(a[sortCol], b[sortCol], sortAsc);
    });
  }

  function applySortIndicators() {
    document.querySelectorAll('thead th[data-sort]').forEach(th => {
      th.classList.remove('sort-asc', 'sort-desc', 'sort-secondary');
      const col = th.dataset.sort;
      if (col === sortCol) {
        th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
      } else if (sortCol === 'modality' && col === costSortCol) {
        // Show which cost column is the secondary sort key
        th.classList.add('sort-secondary');
      }
    });
  }

  function setupSortHeaders() {
    document.querySelectorAll('thead th[data-sort]').forEach(th => {
      const arrow = document.createElement('span');
      arrow.className = 'sort-arrow';
      arrow.textContent = '\u25B2';
      th.appendChild(arrow);

      th.addEventListener('click', () => {
        const col = th.dataset.sort;
        if (col === sortCol) {
          sortAsc = !sortAsc;
        } else {
          sortAsc = th.dataset.type === 'string';
          sortCol = col;
        }
        // Track which cost column was last explicitly sorted
        if (col === 'prompt_cost' || col === 'completion_cost') {
          costSortCol = col;
        }
        applyFilterAndSort();
      });
    });
  }

  // ── Filter ──────────────────────────────────────────────────────────────

  function filterModels(query) {
    let results = allModels;

    if (activeModality !== null) {
      results = results.filter(m => (m.modality || 'unknown') === activeModality);
    }

    if (query) {
      const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
      results = results.filter(m => {
        const haystack = (m.name + ' ' + m.id + ' ' + m.provider).toLowerCase();
        return terms.every(t => haystack.includes(t));
      });
    }

    return results;
  }

  function applyFilterAndSort() {
    const query    = DOM.searchInput.value.trim();
    const filtered = filterModels(query);
    const sorted   = sortModels(filtered);
    renderTable(sorted);
    updateCountDisplay(sorted.length, allModels.length);
    applySortIndicators();
  }

  // ── Data Fetching ───────────────────────────────────────────────────────

  async function fetchModels() {
    try {
      const res = await fetch('/api/models');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      allModels = data.models || [];
      DOM.lastRefresh.textContent = 'Updated ' + new Date().toLocaleTimeString();
      buildModalityChips(allModels);
      applyFilterAndSort();
    } catch (err) {
      DOM.tableBody.innerHTML =
        `<tr><td colspan="6" class="error-state">Failed to load models: ${escapeHtml(err.message)}</td></tr>`;
    }
  }

  async function refreshModels() {
    DOM.refreshBtn.classList.add('loading');
    DOM.refreshBtn.textContent = 'Refreshing...';
    try {
      await fetch('/api/refresh', { method: 'POST' });
      await fetchModels();
    } finally {
      DOM.refreshBtn.classList.remove('loading');
      DOM.refreshBtn.textContent = 'Refresh';
    }
  }

  // ── Init ─────────────────────────────────────────────────────────────────

  let debounceTimer = null;
  DOM.searchInput.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(applyFilterAndSort, 150);
  });

  DOM.refreshBtn.addEventListener('click', refreshModels);

  setupSortHeaders();
  applySortIndicators();
  fetchModels();
})();
