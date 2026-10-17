// Gedeeld door de begrotingseditor en de codespagina: API-verzoeken, de statusmelding
// rechtsboven, en codes hernoemen en toevoegen. Beschikbaar als window.Codes.

window.Codes = (() => {
  // JSON naar de eigen API; bij een fout een Error met de melding van de server en de HTTP-status
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
    if (!response.ok) {
      const err = new Error(data.error || `Er ging iets mis (${response.status}).`);
      err.status = response.status;
      throw err;
    }
    return data;
  }

  // Melding in de bovenbalk; kind = pending, saving, saved of error
  function showState(kind, text) {
    const el = document.querySelector(".save-state");
    el.dataset.state = kind;
    el.textContent = text;
  }

  // Potloodje naast de naam: ter plekke hernoemen (Enter of wegklikken = opslaan, Escape = annuleren)
  function initRename(codesUrl) {
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
  }

  // "Code toevoegen" onder elke categorie. `before` draait vóór het verzoek (de editor slaat
  // daar eerst openstaande wijzigingen op); daarna herladen, terug bij dezelfde categorie.
  function initAddForms(codesUrl, { before } = {}) {
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
          if (before) await before();
          await request("POST", codesUrl, { code: form.elements.code.value, name: form.elements.name.value });
          if (section.id) location.hash = section.id;
          location.reload();
        } catch (err) {
          error.textContent = err.message;
        }
      });
    });
  }

  return { request, showState, initRename, initAddForms };
})();
