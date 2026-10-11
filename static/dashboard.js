// Kleine interacties: tooltips, boekregels uitklappen, huidige sectie in de zijbalk.

document.addEventListener("DOMContentLoaded", () => {
  // Tooltip voor alles met data-tip (grafiekrijen, resultaatstaafjes)
  const tip = document.querySelector(".tooltip");
  const show = (el, x, y) => {
    tip.textContent = el.dataset.tip;
    tip.hidden = false;
    const r = tip.getBoundingClientRect();
    const left = Math.min(x + 14, window.innerWidth - r.width - 8);
    const top = y + 14 + r.height > window.innerHeight ? y - r.height - 10 : y + 14;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${top}px`;
  };
  const hide = () => { tip.hidden = true; };
  document.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("pointermove", (e) => show(el, e.clientX, e.clientY));
    el.addEventListener("pointerleave", hide);
    el.addEventListener("focus", () => {
      const r = el.getBoundingClientRect();
      show(el, r.left + r.width / 2, r.top + r.height / 2);
    });
    el.addEventListener("blur", hide);
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") hide(); });

  // Boekregels onder een evenement tonen of verbergen
  document.querySelectorAll(".expand").forEach((btn) => {
    btn.addEventListener("click", () => {
      const open = btn.getAttribute("aria-expanded") === "true";
      btn.setAttribute("aria-expanded", String(!open));
      document.getElementById(btn.getAttribute("aria-controls")).hidden = open;
    });
  });

  // Zijbalk: markeer de sectie die in beeld is
  const links = new Map([...document.querySelectorAll(".rail a")].map((a) => [a.hash.slice(1), a]));
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return;
      links.forEach((a) => a.classList.remove("current"));
      links.get(entry.target.id)?.classList.add("current");
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  links.forEach((_, id) => {
    const section = document.getElementById(id);
    if (section) observer.observe(section);
  });
});
