'use strict';
const list = document.getElementById('tickets');
const detail = document.getElementById('detail');
function field(label, value) {
  const row = document.createElement('p');
  row.textContent = `${label}：${Array.isArray(value) ? value.join('；') : (value || '待补充')}`;
  detail.append(row);
}
async function showTicket(number) {
  const response = await fetch(`/s1pro/tickets/${encodeURIComponent(number)}`);
  if (!response.ok) { detail.textContent = '工单暂时无法查看。'; return; }
  const ticket = await response.json();
  detail.replaceChildren();
  const title = document.createElement('h2'); title.textContent = ticket.ticket_no; detail.append(title);
  for (const [label, value] of [['状态',ticket.status],['型号',ticket.product_model],['症状',ticket.symptom],['已建议步骤',ticket.suggested_steps],['已尝试步骤',ticket.completed_steps],['用户反馈',ticket.feedback],['交接摘要',ticket.description]]) field(label,value);
  for (const source of ticket.sources || []) {
    const row = document.createElement('p'); row.textContent = '资料来源：';
    const link = document.createElement('a'); link.textContent = source.title; link.href = source.url; link.target = '_blank'; link.rel = 'noopener noreferrer';
    row.append(link); detail.append(row);
  }
}
async function loadTickets() {
  try {
    const response = await fetch('/s1pro/tickets');
    const tickets = await response.json();
    list.replaceChildren();
    if (!tickets.length) { list.textContent = '暂无 S1 Pro 工单。'; return; }
    for (const ticket of tickets) {
      const button = document.createElement('button');
      button.textContent = `${ticket.ticket_no} · ${ticket.symptom || '问题待确认'} · ${ticket.status}`;
      button.className = 'workbench-ticket';
      button.onclick = () => showTicket(ticket.ticket_no);
      list.append(button);
    }
  } catch {
    list.textContent = '工单列表暂时无法加载。';
  }
}
loadTickets();
