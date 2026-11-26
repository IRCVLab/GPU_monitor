import {removal} from '../removal.js';

export function renderSelectionBar(detail, entries, expanded, dispatch) {
  const el = id => document.getElementById(id);
  const panel = el('cleanupPanel'), visible = !!detail && entries.length > 0;
  panel.classList.toggle('show',visible);
  panel.setAttribute('aria-hidden',String(!visible));
  const {summary,command} = visible ? removal(detail,entries) : {summary:'No path selected',command:''};
  const flagged = entries.filter(e => e.flag);
  el('cleanupSummary').textContent = summary + (flagged.length ? ' · ' + flagged.length + ' need review' : '');
  panel.classList.toggle('expanded',expanded);
  el('cleanupDetails').hidden = !expanded;
  el('cleanupToggle').textContent = expanded ? 'Hide' : 'Show';
  el('cleanupToggle').setAttribute('aria-expanded',String(expanded));
  el('cleanupCopyAll').dataset.copyCommand = command;
  el('cleanupCopyAll').disabled = !command;
  el('cleanupClear').disabled = !visible;
  const list = el('cleanupDangerCommand');
  list.replaceChildren();
  if (command) {
    const item = document.createElement('li');
    item.className = 'cleanup-command';
    const pre = document.createElement('pre');
    pre.className = 'cleanup-command-code';
    pre.tabIndex = 0;
    pre.setAttribute('role','region');
    pre.setAttribute('aria-label','Removal command');
    pre.textContent = command;
    const head = document.createElement('div');
    head.className = 'cleanup-command-head';
    const label = document.createElement('strong');
    label.className = 'cleanup-command-title';
    label.textContent = entries.length === 1 ? (entries[0].kind === 'directory' ? 'Remove directory' : 'Remove file') : 'Remove selected paths';
    const copy = document.createElement('button');
    copy.className = 'cleanup-copy-btn';
    copy.dataset.copyCommand = command;
    copy.textContent = 'Copy';
    head.append(label,copy);
    item.append(head,pre);
    list.append(item);
  }
  for (const entry of flagged) {
    const item = document.createElement('li');
    item.textContent = entry.path + ' (' + entry.flag + ') ';
    const remove = document.createElement('button');
    remove.textContent = 'Remove pick';
    remove.onclick = () => dispatch({type:'Unselect',path:entry.path});
    item.append(remove);
    if (entry.flag === 'changed') {
      const confirm = document.createElement('button');
      confirm.textContent = 'Confirm change';
      confirm.onclick = () => dispatch({type:'ConfirmFlagged',path:entry.path});
      item.append(confirm);
    }
    list.append(item);
  }
}
