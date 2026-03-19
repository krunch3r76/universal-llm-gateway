/**
 * Local Models Browser — client-side rendering, sort, filter, node chips.
 *
 * Fetches /api/v1/local-models and renders a flat table with a Node column.
 * Node chip bar filters by node; search filters by model ID.
 */

(function () {
  'use strict';

  const DOM = {
    tableBody:   document.getElementById('table-body'),
    searchInput: document.getElementById('search-input'),
    showAllToggle: document.getElementById('show-all-toggle'),
    runtimeBar:  document.getElementById('runtime-bar'),
    modelCount:  document.getElementById('model-count'),
    lastRefresh: document.getElementById('last-refresh'),
    nodeBar:     document.getElementById('node-bar'),
    pipelinesSection: document.getElementById('pipelines-section'),
    pipelinesList:    document.getElementById('pipelines-list'),
  };

  let allRows     = [];
  let sortCol      = 'node';
  let sortAsc      = true;
  let activeNode   = null;
  let activeRuntime = 'all';

  function isCpuModel(modelId) {
    return modelId.endsWith('-cpu');
  }

  function getViewRows() {
    return DOM.showAllToggle && DOM.showAllToggle.checked
      ? allRows
      : allRows.filter(r => r.activated);
  }

  function applyRuntimeFilter(rows) {
    if (activeRuntime === 'cpu') {
      return rows.filter(r => isCpuModel(r.id));
    }
    if (activeRuntime === 'gpu') {
      return rows.filter(r => !isCpuModel(r.id));
    }
    return rows;
  }

  function getBaseRows() {
    return applyRuntimeFilter(getViewRows());
  }

  function formatContext(len) {
    if (!len) return '\u2014';
    if (len >= 1000000) return (len / 1000000).toFixed(1) + 'M';
    if (len >= 1000)    return Math.round(len / 1000) + 'k';
    return String(len);
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s;
    return div.innerHTML;
  }

  function buildNodeChips(baseRows) {
    if (!DOM.nodeBar) return;

    const counts = {};
    for (const r of baseRows) {
      counts[r.node] = (counts[r.node] || 0) + 1;
    }

    const nodes = Object.keys(counts).sort();
    if (activeNode !== null && !Object.hasOwn(counts, activeNode)) {
      activeNode = null;
    }

    const chips = [
      { label: 'All (' + baseRows.length + ')', value: null },
      ...nodes.map(n => ({ label: n + ' (' + counts[n] + ')', value: n })),
    ];

    DOM.nodeBar.innerHTML = chips.map(c => {
      const active = c.value === activeNode ? ' chip-active' : '';
      const val = c.value === null ? '' : escapeHtml(c.value);
      return '<button class="chip' + active + '" data-node="' + val + '">' + escapeHtml(c.label) + '</button>';
    }).join('');

    DOM.nodeBar.querySelectorAll('.chip').forEach(btn => {
      btn.addEventListener('click', () => {
        activeNode = btn.dataset.node || null;
        applyFilterAndSort();
      });
    });
  }

  function buildRuntimeChips(viewRows) {
    if (!DOM.runtimeBar) return;

    var counts = {
      all: viewRows.length,
      gpu: viewRows.filter(r => !isCpuModel(r.id)).length,
      cpu: viewRows.filter(r => isCpuModel(r.id)).length,
    };

    var chips = [
      { label: 'All runtimes (' + counts.all + ')', value: 'all' },
      { label: 'GPU only (' + counts.gpu + ')', value: 'gpu' },
      { label: 'CPU only (' + counts.cpu + ')', value: 'cpu' },
    ];

    DOM.runtimeBar.innerHTML = chips.map(c => {
      var active = c.value === activeRuntime ? ' chip-active' : '';
      return '<button class="chip' + active + '" data-runtime="' + c.value + '">' +
        escapeHtml(c.label) +
        '</button>';
    }).join('');

    DOM.runtimeBar.querySelectorAll('.chip').forEach(btn => {
      btn.addEventListener('click', () => {
        activeRuntime = btn.dataset.runtime || 'all';
        applyFilterAndSort();
      });
    });
  }

  function renderTable(rows) {
    if (!rows.length) {
      DOM.tableBody.innerHTML =
        '<tr><td colspan="6" class="empty-state">No models match your filter.</td></tr>';
      return;
    }

    const html = rows.map(r => {
      const statusClass = r.activated ? 'status-activated' : 'status-available';
      const statusText  = r.activated ? 'Activated' : 'Available';
      const typeClass   = r.type === 'pipeline' ? 'type-pipeline' : 'type-model';
      return '<tr>' +
        '<td class="col-id" title="' + escapeHtml(r.id) + '">' + escapeHtml(r.id) + '</td>' +
        '<td class="col-node">' + escapeHtml(r.node) + '</td>' +
        '<td class="col-num">' + formatContext(r.context_length) + '</td>' +
        '<td class="col-num">' + formatContext(r.effective_context_per_slot) + '</td>' +
        '<td class="' + statusClass + '">' + statusText + '</td>' +
        '<td class="' + typeClass + '">' + escapeHtml(r.type) + '</td>' +
        '</tr>';
    }).join('');

    DOM.tableBody.innerHTML = html;
  }

  function renderPipelines(pipelines) {
    if (!pipelines || !pipelines.length) {
      DOM.pipelinesSection.style.display = 'none';
      return;
    }
    DOM.pipelinesSection.style.display = '';
    DOM.pipelinesList.innerHTML = pipelines.map(p =>
      '<span class="pipeline-tag">' + escapeHtml(p) + '</span>'
    ).join('');
  }

  function updateCountDisplay(shown, filteredTotal, total) {
    if (filteredTotal === total) {
      DOM.modelCount.textContent = shown === total
        ? total + ' models'
        : shown + ' / ' + total + ' models';
      return;
    }

    if (shown === filteredTotal) {
      DOM.modelCount.textContent = filteredTotal + ' filtered / ' + total + ' total';
      return;
    }

    DOM.modelCount.textContent =
      shown + ' / ' + filteredTotal + ' filtered / ' + total + ' total';
  }

  function cmpValues(av, bv, asc) {
    if (typeof av === 'string' && typeof bv === 'string') {
      var cmp = av.localeCompare(bv, undefined, { sensitivity: 'base' });
      return asc ? cmp : -cmp;
    }
    var na = Number(av) || 0;
    var nb = Number(bv) || 0;
    return asc ? na - nb : nb - na;
  }

  function sortRows(rows) {
    return rows.slice().sort((a, b) => {
      var primary = cmpValues(a[sortCol], b[sortCol], sortAsc);
      if (primary !== 0) return primary;
      return cmpValues(a.id, b.id, true);
    });
  }

  function applySortIndicators() {
    document.querySelectorAll('thead th[data-sort]').forEach(th => {
      th.classList.remove('sort-asc', 'sort-desc');
      if (th.dataset.sort === sortCol) {
        th.classList.add(sortAsc ? 'sort-asc' : 'sort-desc');
      }
    });
  }

  function setupSortHeaders() {
    document.querySelectorAll('thead th[data-sort]').forEach(th => {
      var arrow = document.createElement('span');
      arrow.className = 'sort-arrow';
      arrow.textContent = '\u25B2';
      th.appendChild(arrow);

      th.addEventListener('click', () => {
        var col = th.dataset.sort;
        if (col === sortCol) {
          sortAsc = !sortAsc;
        } else {
          sortAsc = th.dataset.type === 'string';
          sortCol = col;
        }
        applyFilterAndSort();
      });
    });
  }

  function filterRows(query) {
    var results = getBaseRows();

    if (activeNode !== null) {
      results = results.filter(r => r.node === activeNode);
    }

    if (query) {
      var terms = query.toLowerCase().split(/\s+/).filter(Boolean);
      results = results.filter(r => {
        var haystack = (r.id + ' ' + r.node).toLowerCase();
        return terms.every(t => haystack.includes(t));
      });
    }

    return results;
  }

  function applyFilterAndSort() {
    var query    = DOM.searchInput.value.trim();
    var viewRows = getViewRows();
    var baseRows = getBaseRows();
    var filtered = filterRows(query);
    var sorted   = sortRows(filtered);
    buildRuntimeChips(viewRows);
    buildNodeChips(baseRows);
    renderTable(sorted);
    updateCountDisplay(sorted.length, baseRows.length, allRows.length);
    applySortIndicators();
  }

  async function fetchModels() {
    try {
      var res = await fetch('/api/v1/local-models');
      if (!res.ok) throw new Error('HTTP ' + res.status);
      var data = await res.json();

      allRows = [];
      for (var node of (data.nodes || [])) {
        for (var model of (node.models || [])) {
          allRows.push({
            id: model.id,
            node: node.node_id,
            context_length: model.context_length || 0,
            effective_context_per_slot: model.effective_context_per_slot || 0,
            activated: model.activated,
            type: model.type || 'model',
          });
        }
      }

      DOM.lastRefresh.textContent = 'Updated ' + new Date().toLocaleTimeString();
      renderPipelines(data.pipelines);
      applyFilterAndSort();
    } catch (err) {
      DOM.tableBody.innerHTML =
        '<tr><td colspan="6" class="error-state">Failed to load models: ' + escapeHtml(err.message) + '</td></tr>';
    }
  }

  var debounceTimer = null;
  DOM.searchInput.addEventListener('input', () => {
    clearTimeout(debounceTimer);
    debounceTimer = setTimeout(applyFilterAndSort, 150);
  });

  if (DOM.showAllToggle) {
    DOM.showAllToggle.addEventListener('change', applyFilterAndSort);
  }

  setupSortHeaders();
  applySortIndicators();
  fetchModels();
})();
