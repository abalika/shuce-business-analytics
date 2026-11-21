// The native file input owns both picking and dropping. Existing preview handlers
// keep reading the same FileList and receiving the same change event.
for (const zone of document.querySelectorAll('.upload-zone')) {
  const input = zone.querySelector('input[type="file"]');
  const name = zone.querySelector('.upload-name');
  const sync = () => { name.textContent = input.files[0]?.name || ''; };
  input.addEventListener('change', sync);
  input.form?.addEventListener('reset', () => requestAnimationFrame(sync));
  input.addEventListener('dragenter', () => zone.classList.add('drag-active'));
  for (const event of ['dragleave', 'drop', 'dragend']) {
    input.addEventListener(event, () => zone.classList.remove('drag-active'));
  }
}
