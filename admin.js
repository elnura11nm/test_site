for (const list of document.querySelectorAll('[data-record-list]')) {
  const entries = list.querySelector('[data-records]');
  const addButton = list.querySelector('[data-add]');
  const template = list.querySelector('template');

  function updateLimit() {
    addButton.disabled = entries.childElementCount >= 40;
  }

  addButton.addEventListener('click', () => {
    const entry = template.content.cloneNode(true);
    const title = entry.querySelector('input');
    entries.append(entry);
    title.focus();
    updateLimit();
  });

  entries.addEventListener('click', (event) => {
    const removeButton = event.target.closest('[data-remove]');
    if (!removeButton) return;
    removeButton.closest('.admin-record').remove();
    addButton.focus();
    updateLimit();
  });
  updateLimit();
}
