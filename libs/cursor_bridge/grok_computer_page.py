"""Page scripts evaluated in the Grok Bot renderer over CDP.

``grok_computer`` is the only caller. The update script stays inside the
Grok Bot settings dialog and never clicks a transcript chip. The portal
script closes that dialog's own Close control, not the first Close on the page.
"""

PROBE_JS = """(async () => {
  const phase = await window.desktop.main.getBoxHostPhase();
  const egress = await window.desktop.main.getEgressTunnelStatus();
  const migration = await window.desktop.main.getBoxMigrationStatus();
  const app_update = await window.desktop.main.getUpdateStatus();
  let forever_box = null;
  let running_assistants = [];
  let selected_agent_id = null;
  try {
    const root = document.getElementById("root") || document.body.firstElementChild;
    const fk = root ? Object.keys(root).find(k => k.startsWith("__reactContainer") || k.startsWith("__reactFiber")) : null;
    if (fk) {
      const stack = [root[fk]];
      const seen = new Set();
      let client = null;
      while (stack.length) {
        const node = stack.pop();
        if (!node || seen.has(node)) continue;
        seen.add(node);
        if (node.memoizedProps?.value?.foreverBox) { client = node.memoizedProps.value; break; }
        if (node.child) stack.push(node.child);
        if (node.sibling) stack.push(node.sibling);
      }
      if (client) {
        const rows = client.roster?.snapshots?.get ? (client.roster.snapshots.get()?.agents?.rows || []) : [];
        running_assistants = rows
          .filter(r => r.isRunning === true || r.runState?.state === "running")
          .map(r => ({id: r.id, name: r.name, isRunning: Boolean(r.isRunning)}));
        const sel = client.selection?.snapshots?.get ? client.selection.snapshots.get() : null;
        selected_agent_id = sel?.currentAgentId || rows[0]?.id || "a0a69fa2-aa00-4606-b11a-43936e5d44ba";
        await client.foreverBox.refresh(selected_agent_id);
        forever_box = client.foreverBox.statusSnapshotsFor(selected_agent_id).get()?.status ?? null;
      }
    }
  } catch (_) {}
  const top_bar_update_pill = Array.from(document.querySelectorAll("button")).some(
    b => (b.innerText || "").trim() === "Update Bot's Computer" && !b.getAttribute("aria-label")
  );
  return {phase, egress, migration, app_update, forever_box, selected_agent_id, top_bar_update_pill, running_assistants};
})()"""

WAKE_JS = """(async (agentId, openComputerView) => {
  await window.desktop.main.focusWindow();
  if (openComputerView) {
    await window.desktop.main.setComputerViewOpen({isOpen: true});
    await window.desktop.main.signalBoxIntent({agentId, intent: "composer_focus"});
  } else {
    await window.desktop.main.setComputerViewOpen({isOpen: false});
  }
  let egress = await window.desktop.main.getEgressTunnelStatus();
  if (egress?.state === "off") {
    await window.desktop.main.forceReconnectGateway();
    egress = await window.desktop.main.getEgressTunnelStatus();
  }
  const phase = await window.desktop.main.getBoxHostPhase();
  return {phase, egress, open_computer_view: openComputerView};
})"""

UPDATE_CLICK_JS = """(async () => {
  const settingsSelector = '[role=dialog][aria-label="Grok Bot settings"]';
  const updateSelector = 'button[aria-label="Update Bot\\'s Computer"]';
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const settingsDialog = () => document.querySelector(settingsSelector);
  const updateInDialog = () => {
    const dialog = settingsDialog();
    return dialog ? dialog.querySelector(updateSelector) : null;
  };

  await window.desktop.main.focusWindow();
  let btn = Array.from(document.querySelectorAll("button")).find(
    b => (b.innerText || "").trim() === "Update Bot's Computer" && !b.getAttribute("aria-label")
  );
  let path = btn ? "top_bar" : null;
  if (!btn) {
    btn = updateInDialog();
    path = btn ? "settings_dialog" : path;
  }
  if (!btn) {
    const km = Array.from(document.querySelectorAll("button")).find(
      el => (el.getAttribute("aria-label") || "").trim() === "Open account menu"
    );
    if (km) km.click();
    await sleep(300);
    const settings = Array.from(document.querySelectorAll("[role=menuitem]")).find(
      el => (el.innerText || "").trim() === "Settings"
    );
    if (settings) settings.click();
    await sleep(400);
    const dialog = settingsDialog();
    const computer = dialog
      ? Array.from(dialog.querySelectorAll("button")).find(el => (el.innerText || "").trim() === "Computer")
      : null;
    if (computer) computer.click();
    await sleep(400);
    btn = updateInDialog();
    path = btn ? "account_settings" : path;
  }
  if (!btn) return {ok: false, reason: "update_button_missing", path};
  const scope = btn.closest(settingsSelector) || document;
  btn.click();
  await sleep(400);
  const confirm =
    scope.querySelector('button[data-confirming="true"][aria-label*="Update Bot"]')
    || scope.querySelector('button[aria-label*="Click Again to Confirm"]');
  if (confirm) confirm.click();
  const migration = await window.desktop.main.getBoxMigrationStatus();
  return {ok: true, path, migration, confirmed: Boolean(confirm)};
})()"""

RETURN_TO_PORTAL_JS = """(async () => {
  const dialog = document.querySelector('[role=dialog][aria-label="Grok Bot settings"]');
  let closeClicks = 0;
  if (dialog) {
    const close = dialog.querySelector('button[aria-label="Close"]');
    if (close) {
      close.click();
      closeClicks += 1;
      await new Promise(r => setTimeout(r, 250));
    }
  }
  const menu = document.querySelector("[role=menu]");
  if (menu) {
    const km = Array.from(document.querySelectorAll("button")).find(
      el => (el.getAttribute("aria-label") || "").trim() === "Open account menu"
    );
    if (km) km.click();
    await new Promise(r => setTimeout(r, 200));
  }
  await window.desktop.main.setComputerViewOpen({isOpen: false});
  await window.desktop.main.focusWindow();
  const settingsStillOpen = Boolean(
    document.querySelector('[role=dialog][aria-label="Grok Bot settings"]')
  );
  return {
    closed: closeClicks > 0,
    close_clicks: closeClicks,
    settings_still_open: settingsStillOpen,
    menu_open: Boolean(document.querySelector("[role=menu]")),
  };
})()"""
