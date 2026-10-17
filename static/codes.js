// Codes beheren: hernoemen, toevoegen en verwijderen via /api/codes.

document.addEventListener("DOMContentLoaded", () => {
  const stateEl = document.getElementById("codes-state");
  if (!document.querySelector("tr[data-code]")) return;
  const { codesUrl } = JSON.parse(stateEl.textContent);
  const { request, showState } = window.Codes;

  // ---------- Hernoemen en toevoegen ----------

  Codes.initRename(codesUrl);
  Codes.initAddForms(codesUrl);

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
