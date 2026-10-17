// Begrotingseditor: totalen rekenen mee terwijl je typt, en elke wijziging wordt
// automatisch opgeslagen. Bedragen worden intern in centen bijgehouden (geen afrondingsfouten).

document.addEventListener("DOMContentLoaded", () => {
  const state = JSON.parse(document.getElementById("budget-state").textContent);
  const { request, showState } = window.Codes;

  // ---------- Getallen ----------

  const nl = new Intl.NumberFormat("nl-NL", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const num = (cents) => (cents < 0 ? "−" : "") + nl.format(Math.abs(cents) / 100);
  const eur = (cents) => (cents < 0 ? "−€ " : "€ ") + nl.format(Math.abs(cents) / 100);

  // "1.234,56", "1234.56", "1.500" (= 1500) en "€ 12,50" worden allemaal begrepen
  function parseNumber(text) {
    let t = text;
    if (t.includes(",") && t.includes(".")) {
      const dec = t.lastIndexOf(",") > t.lastIndexOf(".") ? "," : ".";
      t = t.split(dec === "," ? "." : ",").join("").replace(dec, ".");
    } else if (t.includes(",")) {
      t = t.replace(/,/g, ".");
    } else if (/^\d{1,3}(\.\d{3})+$/.test(t)) {
      t = t.replace(/\./g, "");
    }
    return /^(\d+\.?\d*|\.\d+)$/.test(t) ? Number(t) : NaN;
  }

  // Kleine rekenmachine: + - * / en haakjes, zodat "4*375" of "12*(15+3,50)" werkt
  function evaluate(text) {
    const tokens = text.match(/[\d.,]+|[-+*/()]/g);
    if (!tokens || tokens.join("") !== text) return NaN;
    let i = 0;
    const peek = () => tokens[i];
    const next = () => tokens[i++];
    function factor() {
      const t = next();
      if (t === "-") return -factor();
      if (t === "+") return factor();
      if (t === "(") {
        const v = sum();
        return next() === ")" ? v : NaN;
      }
      return t === undefined ? NaN : parseNumber(t);
    }
    function product() {
      let v = factor();
      while (peek() === "*" || peek() === "/") {
        const op = next();
        const r = factor();
        v = op === "*" ? v * r : v / r;
      }
      return v;
    }
    function sum() {
      let v = product();
      while (peek() === "+" || peek() === "-") {
        v = next() === "+" ? v + product() : v - product();
      }
      return v;
    }
    const v = sum();
    return i === tokens.length && Number.isFinite(v) ? v : NaN;
  }

  function parseCents(raw) {
    const text = raw.replace(/€|\s/g, "").replace(/−/g, "-");
    if (text === "") return 0;
    const v = evaluate(text);
    return Number.isFinite(v) && Math.abs(v) < 1e9 ? Math.round(v * 100) : NaN;
  }

  // ---------- Nieuwe begroting ----------

  document.querySelectorAll("[data-create]").forEach((btn) => {
    btn.addEventListener("click", async () => {
      const error = document.querySelector(".create-error");
      document.querySelectorAll("[data-create]").forEach((b) => { b.dataset.busy = b === btn ? "true" : ""; b.disabled = true; });
      error.hidden = true;
      try {
        await request("POST", state.createUrl, { bron: btn.dataset.create, databron: state.source });
        location.reload();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
        document.querySelectorAll("[data-create]").forEach((b) => { b.dataset.busy = ""; b.disabled = b.hasAttribute("data-unavailable"); });
      }
    });
  });

  const rows = [...document.querySelectorAll("tr[data-code]")];
  if (!rows.length) return;
  // knoppen die bij het laden al uitgeschakeld waren, ook na een fout uit laten
  document.querySelectorAll("[data-create][disabled]").forEach((b) => b.setAttribute("data-unavailable", ""));

  // ---------- Totalen ----------

  const values = new Map(); // code -> { debit, credit } in centen
  rows.forEach((tr) => {
    const v = {};
    tr.querySelectorAll("input.amount").forEach((input) => {
      v[input.dataset.field] = Number(input.dataset.cents) || 0;
      input.value = v[input.dataset.field] ? num(v[input.dataset.field]) : "";
    });
    values.set(tr.dataset.code, v);
  });

  function recalc() {
    const sums = { all: { debit: 0, credit: 0 } };
    rows.forEach((tr) => {
      const v = values.get(tr.dataset.code);
      const result = v.credit - v.debit;
      const cell = tr.querySelector(".row-result");
      cell.textContent = v.debit || v.credit ? num(result) : "";
      cell.classList.toggle("negative", result < 0);
      const s = (sums[tr.dataset.digit] ??= { debit: 0, credit: 0 });
      for (const k of ["debit", "credit"]) { s[k] += v[k]; sums.all[k] += v[k]; }
    });
    document.querySelectorAll("[data-sum]").forEach((el) => {
      const s = sums[el.dataset.sum] || { debit: 0, credit: 0 };
      const value = el.dataset.field === "result" ? s.credit - s.debit : s[el.dataset.field];
      el.textContent = el.dataset.format === "eur" ? eur(value) : num(value);
    });
  }
  recalc();

  // ---------- Invoer ----------

  const amountInputs = { debit: [], credit: [] };
  document.querySelectorAll("input.amount").forEach((input) => amountInputs[input.dataset.field].push(input));

  document.querySelectorAll("input.amount").forEach((input) => {
    const code = input.closest("tr").dataset.code;
    input.addEventListener("focus", () => input.select());
    input.addEventListener("input", () => {
      const cents = parseCents(input.value);
      input.setAttribute("aria-invalid", String(Number.isNaN(cents)));
      if (Number.isNaN(cents)) return;
      values.get(code)[input.dataset.field] = cents;
      recalc();
      scheduleSave();
    });
    input.addEventListener("blur", () => {
      const cents = parseCents(input.value);
      if (!Number.isNaN(cents)) input.value = cents ? num(cents) : "";
    });
    input.addEventListener("keydown", (e) => {
      const list = amountInputs[input.dataset.field];
      const at = list.indexOf(input);
      let target;
      if (e.key === "Enter" || e.key === "ArrowDown") target = list[at + 1];
      else if (e.key === "ArrowUp") target = list[at - 1];
      else return;
      e.preventDefault();
      input.blur();
      target?.focus();
    });
  });

  document.querySelectorAll("input.note").forEach((input) => {
    input.addEventListener("input", scheduleSave);
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") { e.preventDefault(); input.blur(); }
    });
  });

  // ---------- Opslaan ----------

  let timer = null;
  let inflight = null;
  let again = false;
  let dirty = false;
  let broken = false;

  function scheduleSave() {
    if (broken) return;
    dirty = true;
    showState("pending", "Wijzigingen…");
    clearTimeout(timer);
    timer = setTimeout(save, 500);
  }

  function payload() {
    const out = {};
    rows.forEach((tr) => {
      const v = values.get(tr.dataset.code);
      const note = tr.querySelector("input.note").value.trim();
      if (v.debit || v.credit || note) {
        out[tr.dataset.code] = { debit: (v.debit / 100).toFixed(2), credit: (v.credit / 100).toFixed(2), note };
      }
    });
    return out;
  }

  async function save() {
    clearTimeout(timer);
    if (inflight) { again = true; return inflight; }
    dirty = false;
    showState("saving", "Opslaan…");
    inflight = request("PUT", state.saveUrl, { version: state.version, rows: payload() })
      .then((data) => {
        state.version = data.version;
        if (!dirty) showState("saved", `Opgeslagen ${data.saved}`);
      })
      .catch((err) => {
        dirty = true;
        if (err.status === 409 || err.status === 423 || err.status === 404) {
          broken = true;
          document.querySelectorAll("input.amount, input.note").forEach((i) => { i.disabled = true; });
        }
        showState("error", err.message);
      })
      .finally(() => {
        inflight = null;
        if (again && !broken) { again = false; save(); }
      });
    return inflight;
  }

  // Openstaande wijzigingen eerst wegschrijven (vóór herladen of een andere actie)
  async function flush() {
    if (timer || dirty) await save();
    while (inflight) await inflight;
  }

  window.addEventListener("beforeunload", (e) => {
    if (dirty || inflight) e.preventDefault();
  });

  // ---------- Codes ----------

  Codes.initAddForms(state.codesUrl, { before: flush });
  Codes.initRename(state.codesUrl);

  // ---------- Vaststellen / ontgrendelen ----------

  document.querySelectorAll("[data-dialog]").forEach((btn) => {
    const dialog = document.getElementById(btn.dataset.dialog);
    btn.addEventListener("click", () => dialog.showModal());
  });
  document.querySelectorAll("dialog form").forEach((form) => {
    const dialog = form.closest("dialog");
    form.querySelector("[value=cancel]").addEventListener("click", () => dialog.close());
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const error = form.querySelector(".form-error");
      error.textContent = "";
      try {
        await flush();
        await request("POST", state.statusUrl, {
          version: state.version, status: form.dataset.status, date: form.elements.date?.value,
        });
        location.reload();
      } catch (err) {
        error.textContent = err.message;
      }
    });
  });
});
