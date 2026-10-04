// Contor anonim: numara vizitele si click-urile pe produse. Fara cookie-uri, fara date personale.
(function () {
  function send(data) {
    var body = JSON.stringify(data);
    try {
      if (navigator.sendBeacon && navigator.sendBeacon("/.netlify/functions/track", new Blob([body], { type: "application/json" }))) return;
    } catch (e) {}
    try { fetch("/.netlify/functions/track", { method: "POST", body: body, keepalive: true, headers: { "Content-Type": "application/json" } }); } catch (e) {}
  }
  var seen = false;
  try { seen = sessionStorage.getItem("tx_view") === "1"; sessionStorage.setItem("tx_view", "1"); } catch (e) {}
  if (!seen) send({ e: "view" });

  var clicked = {};
  document.addEventListener("click", function (ev) {
    var a = ev.target && ev.target.closest ? ev.target.closest("a.card[data-id]") : null;
    if (!a) return;
    var id = a.getAttribute("data-id");
    if (clicked[id]) return; // un click numarat per produs per vizita
    clicked[id] = true;
    send({ e: "click", id: id, s: a.getAttribute("data-s"), title: a.getAttribute("data-title") });
  }, true);

  // produsele ascunse sau sterse din consola dispar imediat de pe pagina
  try {
    fetch("/.netlify/functions/moderate", { cache: "no-store" }).then(function (r) { return r.json(); }).then(function (m) {
      var off = {};
      (m.hidden || []).concat(m.deleted || []).forEach(function (id) { off[id] = true; });
      document.querySelectorAll("a.card[data-id]").forEach(function (a) {
        if (off[a.getAttribute("data-id")]) a.style.display = "none";
      });
    }).catch(function () {});
  } catch (e) {}
})();
