// Afiseaza numele produselor si textele paginii in limba vizitatorului.
// Ordinea: limba telefonului, apoi fusul orar, altfel engleza.
(function () {
  var SUPPORTED = ["ro", "en", "it", "de", "fr", "es"];
  var TZ_LANG = {
    "Europe/Bucharest": "ro", "Europe/Chisinau": "ro",
    "Europe/Rome": "it", "Europe/San_Marino": "it", "Europe/Vatican": "it",
    "Europe/Berlin": "de", "Europe/Vienna": "de", "Europe/Zurich": "de",
    "Europe/Paris": "fr", "Europe/Brussels": "fr", "Europe/Luxembourg": "fr", "Europe/Monaco": "fr",
    "Europe/Madrid": "es", "Atlantic/Canary": "es", "America/Mexico_City": "es", "America/Bogota": "es",
    "America/Argentina/Buenos_Aires": "es", "America/Lima": "es", "America/Santiago": "es"
  };
  var TEXT = {
    ro: { clips: "Din clipurile TikTok", find: "Ai văzut un număr în clip? Ex: 12", go: "Caută", tag: "Oferte zilnice", featured: "Din clipurile mele", hot: "Oferte noi · actualizat {d}", buy: "Cumpără", meta: "{r}% recenzii pozitive · {o}+ comenzi", title: "@trendixeu — Oferte zilnice" },
    en: { clips: "From my TikTok videos", find: "Saw a number in a video? E.g. 12", go: "Find", tag: "Daily deals", featured: "From my videos", hot: "New deals · updated {d}", buy: "Buy now", meta: "{r}% positive reviews · {o}+ orders", title: "@trendixeu — Daily deals" },
    it: { clips: "Dai miei video TikTok", find: "Hai visto un numero nel video? Es. 12", go: "Cerca", tag: "Offerte del giorno", featured: "Dai miei video", hot: "Nuove offerte · aggiornato {d}", buy: "Acquista", meta: "{r}% recensioni positive · {o}+ ordini", title: "@trendixeu — Offerte del giorno" },
    de: { clips: "Aus meinen TikTok-Videos", find: "Nummer im Video gesehen? Z. B. 12", go: "Suchen", tag: "Tägliche Angebote", featured: "Aus meinen Videos", hot: "Neue Angebote · aktualisiert {d}", buy: "Kaufen", meta: "{r}% positive Bewertungen · {o}+ Bestellungen", title: "@trendixeu — Tägliche Angebote" },
    fr: { clips: "De mes vidéos TikTok", find: "Un numéro vu dans la vidéo ? Ex. 12", go: "Chercher", tag: "Bons plans du jour", featured: "De mes vidéos", hot: "Nouvelles offres · mis à jour le {d}", buy: "Acheter", meta: "{r}% d'avis positifs · {o}+ commandes", title: "@trendixeu — Bons plans du jour" },
    es: { clips: "De mis vídeos de TikTok", find: "¿Viste un número en el vídeo? Ej. 12", go: "Buscar", tag: "Ofertas diarias", featured: "De mis vídeos", hot: "Nuevas ofertas · actualizado {d}", buy: "Comprar", meta: "{r}% reseñas positivas · {o}+ pedidos", title: "@trendixeu — Ofertas diarias" }
  };

  function pickLang() {
    var langs = navigator.languages || [navigator.language || ""];
    for (var i = 0; i < langs.length; i++) {
      var base = String(langs[i]).slice(0, 2).toLowerCase();
      if (base === "mo") base = "ro";
      if (SUPPORTED.indexOf(base) !== -1) return base;
    }
    try {
      var tz = Intl.DateTimeFormat().resolvedOptions().timeZone || "";
      if (TZ_LANG[tz]) return TZ_LANG[tz];
    } catch (e) {}
    return "en";
  }

  var lang = pickLang();
  if (lang === "ro") return; // pagina e deja in romana
  var T = TEXT[lang], titles = window.TX_TITLES || {};
  document.documentElement.lang = lang;
  document.title = T.title;

  var nodes = document.querySelectorAll("[data-i18n]");
  for (var i = 0; i < nodes.length; i++) {
    var el = nodes[i], k = el.getAttribute("data-i18n");
    if (!T[k]) continue;
    var txt = T[k];
    if (k === "hot") {
      var d = el.getAttribute("data-date"), shown = d;
      try { shown = new Date(d + "T12:00:00Z").toLocaleDateString(lang, { day: "numeric", month: "short" }); } catch (e) {}
      txt = txt.replace("{d}", shown);
    }
    el.textContent = txt;
  }

  var ph = document.querySelectorAll("[data-i18n-ph]");
  for (var q = 0; q < ph.length; q++) {
    var key = ph[q].getAttribute("data-i18n-ph");
    if (T[key]) ph[q].setAttribute("placeholder", T[key]);
  }

  var tnodes = document.querySelectorAll("[data-tid]");
  for (var j = 0; j < tnodes.length; j++) {
    var t = titles[tnodes[j].getAttribute("data-tid")];
    if (t && t[lang]) tnodes[j].textContent = t[lang];
  }

  var metas = document.querySelectorAll(".meta[data-o]");
  for (var m = 0; m < metas.length; m++) {
    var o = Number(metas[m].getAttribute("data-o"));
    var oTxt = o; try { oTxt = o.toLocaleString(lang); } catch (e) {}
    metas[m].textContent = T.meta.replace("{r}", metas[m].getAttribute("data-r")).replace("{o}", oTxt);
  }
})();
