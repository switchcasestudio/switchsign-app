(function () {
  const canvas = document.getElementById('signature-pad');
  const form = document.getElementById('sign-form');
  if (!canvas || !form || !window.SignaturePad) return;

  const signaturePad = new SignaturePad(canvas);
  const clearButton = document.getElementById('clear-signature');
  const signatureInput = document.getElementById('signature_data_url');
  const agreed = document.getElementById('agreed');
  const submitButton = document.getElementById('submit-button');

  function resizeCanvas() {
    const ratio = Math.max(window.devicePixelRatio || 1, 1);
    const data = signaturePad.isEmpty() ? null : canvas.toDataURL('image/png');
    canvas.width = Math.floor(canvas.offsetWidth * ratio);
    canvas.height = Math.floor(200 * ratio);
    canvas.getContext('2d').scale(ratio, ratio);
    if (data) {
      const image = new Image();
      image.onload = function () { canvas.getContext('2d').drawImage(image, 0, 0, canvas.offsetWidth, 200); };
      image.src = data;
    } else {
      signaturePad.clear();
    }
  }

  window.addEventListener('resize', resizeCanvas);
  resizeCanvas();

  clearButton.addEventListener('click', function () {
    signaturePad.clear();
  });

  (function setupAddressAutocomplete() {
    const input = document.getElementById('client_signed_address');
    const list = document.getElementById('address-suggestions');
    if (!input || !list) return;

    const token = location.pathname.split('/')[2];
    let debounceTimer = null;
    let activeIndex = -1;
    let currentItems = [];
    let lastQuery = '';

    function close() {
      list.hidden = true;
      list.innerHTML = '';
      input.setAttribute('aria-expanded', 'false');
      activeIndex = -1;
      currentItems = [];
    }

    function select(index) {
      if (index < 0 || index >= currentItems.length) return;
      input.value = currentItems[index];
      lastQuery = input.value;
      close();
      input.focus();
    }

    function highlight(index) {
      activeIndex = index;
      Array.prototype.forEach.call(list.children, function (li, i) {
        li.classList.toggle('is-active', i === index);
      });
    }

    function render(items) {
      currentItems = items;
      list.innerHTML = '';
      if (!items.length) { close(); return; }
      items.forEach(function (text, i) {
        const li = document.createElement('li');
        li.textContent = text;
        li.setAttribute('role', 'option');
        // mousedown fires before the input's blur, so the click still lands.
        li.addEventListener('mousedown', function (e) { e.preventDefault(); select(i); });
        list.appendChild(li);
      });
      list.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      activeIndex = -1;
    }

    input.addEventListener('input', function () {
      const q = input.value.trim();
      lastQuery = q;
      clearTimeout(debounceTimer);
      if (q.length < 3) { close(); return; }
      debounceTimer = setTimeout(function () {
        fetch('/s/' + encodeURIComponent(token) + '/address-suggest?q=' + encodeURIComponent(q))
          .then(function (res) { return res.ok ? res.json() : { suggestions: [] }; })
          .then(function (data) {
            // Ignore stale responses that arrive after further typing.
            if (input.value.trim() !== q || q !== lastQuery) return;
            render(data.suggestions || []);
          })
          .catch(function () { close(); });
      }, 250);
    });

    input.addEventListener('keydown', function (e) {
      if (list.hidden) return;
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        highlight(Math.min(activeIndex + 1, currentItems.length - 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        highlight(Math.max(activeIndex - 1, 0));
      } else if (e.key === 'Enter') {
        if (activeIndex >= 0) {
          e.preventDefault();
          select(activeIndex);
        }
      } else if (e.key === 'Escape') {
        close();
      }
    });

    input.addEventListener('blur', function () {
      setTimeout(close, 150);
    });
  })();

  form.addEventListener('submit', function (event) {
    event.preventDefault();
    // Run native field validation (required, email format) before we take over
    // submission. We submit programmatically below, which would otherwise skip it
    // and let empty required fields reach the server as a raw 422.
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }
    if (signaturePad.isEmpty()) {
      alert('Please sign before submitting.');
      return;
    }
    if (!agreed.checked) {
      alert('Please confirm you have read the agreement.');
      return;
    }
    signatureInput.value = signaturePad.toDataURL('image/png');
    submitButton.disabled = true;
    // Replace current history entry so pressing Back after submission lands
    // on a neutral state rather than re-opening this signing page.
    history.replaceState(null, '', location.href);
    form.submit();
  });
})();
