function (cfg) {
  const root = document.querySelector('main') || document.body;
  const url = location.href;
  const loginWall = /\/login/i.test(url) || /\/logout/i.test(url);
  const messageSel = (cfg && cfg.message_sel) || [];
  let turnNodes = 0;
  const seen = new Set();
  for (const sel of messageSel) {
  for (const el of document.querySelectorAll(sel)) {
    if (!seen.has(el)) { seen.add(el); turnNodes += 1; }
  }
  }
  const elementCount = root ? root.querySelectorAll('*').length : 0;
  return {
    url: url,
    ready_state: document.readyState,
    login_wall: loginWall,
    turn_nodes: turnNodes,
    element_count: elementCount,
    visibility_state: document.visibilityState,
    streaming: !!document.querySelector("[aria-busy='true']"),
    title: (document.title || ''),
    window_scroll_y: window.scrollY || 0,
    body_scroll_height: (document.body && document.body.scrollHeight) || 0,
    inner_height: window.innerHeight || 0
  };
}