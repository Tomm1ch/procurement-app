const forms = document.querySelector('#line-forms');
const total = document.querySelector('#id_order_lines-TOTAL_FORMS');
const template = document.querySelector('#empty-line');
const addButton = document.querySelector('#add-line');

function renumber() {
  forms.querySelectorAll('.line-form:not([hidden])').forEach((row, index) => {
    const number = row.querySelector('.line-number');
    if (number) number.textContent = index + 1;
  });
}

function calculateLineTotals() {
  let amount = 0;
  let quantity = 0;
  forms.querySelectorAll('.line-form:not([hidden])').forEach(row => {
    amount += Number(row.querySelector('[name$="total_price"]')?.value) || 0;
    quantity += Number(row.querySelector('[name$="quantity"]')?.value) || 0;
  });
  return { amount, quantity };
}

function updateRequestTotal(syncDetails = false) {
  const { amount, quantity } = calculateLineTotals();
  document.querySelector('#order-lines-total-quantity').textContent = quantity.toLocaleString(undefined, { maximumFractionDigits: 3 });
  document.querySelector('#order-lines-total-amount').textContent = amount.toFixed(2);
  if (syncDetails && requestTotalInput) {
    requestTotalInput.value = (amount + additionalCosts).toFixed(2);
  }
}

const summary = document.createElement('div');
summary.className = 'order-lines-summary';
summary.innerHTML = '<span>Total quantity <strong id="order-lines-total-quantity">0</strong></span><span>Order lines total <strong id="order-lines-total-amount">0.00</strong></span>';
forms.insertAdjacentElement('afterend', summary);

const requestTotalInput = document.querySelector('#id_total_cost');
const initialLineAmount = calculateLineTotals().amount;
const initialRequestTotal = Number(requestTotalInput?.value) || initialLineAmount;
const additionalCosts = initialRequestTotal - initialLineAmount;

addButton.addEventListener('click', () => {
  const index = Number(total.value);
  const wrapper = document.createElement('div');
  wrapper.innerHTML = template.innerHTML.replaceAll('__prefix__', index).trim();
  forms.appendChild(wrapper.firstElementChild);
  total.value = index + 1;
  renumber();
  updateRequestTotal(true);
});

forms.addEventListener('input', event => {
  if (!event.target.name?.includes('unit_price') && !event.target.name?.includes('quantity')) return;
  const row = event.target.closest('.line-form');
  const unitPrice = Number(row.querySelector('[name$="unit_price"]').value);
  const quantity = Number(row.querySelector('[name$="quantity"]').value);
  const lineTotal = row.querySelector('[name$="total_price"]');
  if (unitPrice >= 0 && quantity >= 0) lineTotal.value = (unitPrice * quantity).toFixed(2);
  updateRequestTotal(true);
});

forms.addEventListener('click', event => {
  const button = event.target.closest('.remove-line');
  if (!button) return;
  const row = button.closest('.line-form');
  const deleteInput = row.querySelector('[name$="-DELETE"]');
  if (deleteInput) deleteInput.checked = true;
  row.hidden = true;
  renumber();
  updateRequestTotal(true);
});

forms.addEventListener('input', event => {
  if (event.target.name?.includes('total_price')) updateRequestTotal(true);
});

renumber();
updateRequestTotal();
