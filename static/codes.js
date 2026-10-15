// Codes beheren: hernoemen, toevoegen en verwijderen via /api/codes.

document.addEventListener("DOMContentLoaded", () => {
  const stateEl = document.getElementById("codes-state");
  if (!document.querySelector("tr[data-code]")) return;
  const { codesUrl } = JSON.parse(stateEl.textContent);
  const saveState = document.querySelector(".save-state");

  function showState(kind, text) {
    saveState.dataset.state = kind;
    saveState.textContent = text;
  }

  async function request(method, url, body) {
    let response;
    try {
      response = await fetch(url, {
        method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
      });
    } catch {
      throw new Error("Het dashboard is niet bereikbaar. Draait dashboard.py nog?");
    }
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || `Er ging iets mis (${response.status}).`);
    return data;
  }

  // ---------- Hernoemen ----------

  document.querySelectorAll(".rename").forEach((btn) => {
    const tr = btn.closest("tr");
    const label = tr.querySelector(".label");
    btn.addEventListener("click", () => {
      const input = document.createElement("input");
      input.className = "rename-input";
      input.value = label.textContent;
      input.maxLength = 80;
      input.setAttribute("aria-label", `Nieuwe naam voor ${tr.dataset.code}`);
      label.hidden = true;
      btn.hidden = true;
      label.after(input);
      input.focus();
      input.select();
      let done = false;
      const close = () => { done = true; input.remove(); label.hidden = false; btn.hidden = false; };
      const commit = async () => {
        if (done) return;
        const name = input.value.trim();
        if (!name || name === label.textContent) { close(); return; }
        done = true;
        try {
          await request("PUT", `${codesUrl}/${tr.dataset.code}`, { name });
          label.textContent = name;
          close();
          showState("saved", `${tr.dataset.code} hernoemd`);
        } catch (err) {
          done = false;
          input.setAttribute("aria-invalid", "true");
          showState("error", err.message);
        }
      };
      input.addEventListener("keydown", (e) => {
        if (e.key === "Enter") { e.preventDefault(); commit(); }
        if (e.key === "Escape") { e.preventDefault(); close(); btn.focus(); }
      });
      input.addEventListener("blur", commit);
    });
  });

  // ---------- Evenementlabel verplicht ----------

  document.querySelectorAll(".label-toggle").forEach((box) => {
    const code = box.closest("tr").dataset.code;
    box.addEventListener("change", async () => {
      box.disabled = true;
      try {
        await request("PUT", `${codesUrl}/${code}/label`, { required: box.checked });
        showState("saved", `${code}: label ${box.checked ? "verplicht" : "niet verplicht"}`);
      } catch (err) {
        box.checked = !box.checked;
        showState("error", err.message);
      } finally {
        box.disabled = false;
      }
    });
  });

  // ---------- Toevoegen ----------

  document.querySelectorAll(".show-add").forEach((btn) => {
    const section = btn.closest("section");
    const row = section.querySelector(".add-row");
    const form = row.querySelector("form");
    btn.addEventListener("click", () => {
      row.hidden = false;
      btn.hidden = true;
      form.elements.name.focus();
    });
    form.querySelector(".cancel").addEventListener("click", () => {
      row.hidden = true;
      btn.hidden = false;
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const error = form.querySelector(".form-error");
      error.textContent = "";
      try {
        await request("POST", codesUrl, { code: form.elements.code.value, name: form.elements.name.value });
        location.hash = section.id;
        location.reload();
      } catch (err) {
        error.textContent = err.message;
      }
    });
  });

  // ---------- Verwijderen ----------

  const dialog = document.getElementById("delete");
  const dialogForm = dialog.querySelector("form");
  let target = null;
  document.querySelectorAll(".delete").forEach((btn) => {
    btn.addEventListener("click", () => {
      target = btn.closest("tr").dataset.code;
      dialog.querySelector(".delete-what").textContent = `${target} ${btn.dataset.name}`;
      dialog.querySelector(".form-error").textContent = "";
      dialog.showModal();
    });
  });
  dialogForm.querySelector("[value=cancel]").addEventListener("click", () => dialog.close());
  dialogForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    try {
      // de bron (testdata of Moneybird) gaat mee, zodat de server de projecten kan controleren
      await request("DELETE", `${codesUrl}/${target}${location.search}`);
      location.reload();
    } catch (err) {
      dialogForm.querySelector(".form-error").textContent = err.message;
    }
  });
});
