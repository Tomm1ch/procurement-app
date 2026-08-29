const forms = document.querySelector('#line-forms');
const total = document.querySelector('#id_order_lines-TOTAL_FORMS');
const template = document.querySelector('#empty-line');
const addButton = document.querySelector('#add-line');

function renumber() {
  forms.querySelectorAll('.line-form').forEach((row, index) => {
    const number = row.querySelector('.line-number');
    if (number) number.textContent = index + 1;
  });
}

addButton.addEventListener('click', () => {
  const index = Number(total.value);
  const wrapper = document.createElement('div');
  wrapper.innerHTML = template.innerHTML.replaceAll('__prefix__', index).trim();
  forms.appendChild(wrapper.firstElementChild);
  total.value = index + 1;
  renumber();
});

forms.addEventListener('input', event => {
  if (!event.target.name?.includes('unit_price') && !event.target.name?.includes('quantity')) return;
  const row = event.target.closest('.line-form');
  const unitPrice = Number(row.querySelector('[name$="unit_price"]').value);
  const quantity = Number(row.querySelector('[name$="quantity"]').value);
  const lineTotal = row.querySelector('[name$="total_price"]');
  if (unitPrice >= 0 && quantity >= 0) lineTotal.value = (unitPrice * quantity).toFixed(2);
});

renumber();
