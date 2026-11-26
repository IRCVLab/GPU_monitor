export function renderNotice(element, message) {
  element.textContent = message || '';
  element.hidden = !message;
  element.classList.toggle('show',Boolean(message));
}
