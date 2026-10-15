(function () {
  "use strict";

  // htmx's own trigger filters need eval, which the CSP forbids, so polling is paused here.
  // Focus is never a reason to pause: htmx refocuses the element with the same id after a swap.
  document.addEventListener("htmx:beforeRequest", function (event) {
    var element = event.detail.elt;
    if (!element.hasAttribute("data-poll")) {
      return;
    }
    if (document.hidden || element.querySelector("details[open]")) {
      event.preventDefault();
    }
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) {
      return;
    }
    document.querySelectorAll("[data-poll]").forEach(function (element) {
      element.dispatchEvent(new CustomEvent("console:refresh"));
    });
  });

  // htmx only leaves modified clicks alone on boosted links. Without this, a click meant
  // to open a new tab or window swaps the list in place.
  document.addEventListener(
    "click",
    function (event) {
      var modified = event.metaKey || event.ctrlKey || event.shiftKey || event.altKey;
      if ((modified || event.button !== 0) && event.target.closest("a[hx-get]")) {
        event.stopPropagation();
      }
    },
    true
  );

  // A print shows every stage's output but not its raw JSON, so only the list's own
  // details open. An open details also holds off the poll, which would otherwise
  // replace the elements remembered here.
  var printOpened = [];
  window.addEventListener("beforeprint", function () {
    document.querySelectorAll("[data-print-open] > * > details:not([open])").forEach(function (details) {
      details.open = true;
      printOpened.push(details);
    });
  });
  window.addEventListener("afterprint", function () {
    printOpened.forEach(function (details) {
      details.open = false;
    });
    printOpened = [];
  });

  var banner = document.getElementById("c-banner");
  if (!banner) {
    return;
  }

  function showBanner() {
    banner.hidden = false;
  }

  document.addEventListener("htmx:responseError", showBanner);
  document.addEventListener("htmx:sendError", showBanner);
  document.addEventListener("htmx:timeout", showBanner);
  // htmx fires afterOnLoad for a 4xx or 5xx too, right after responseError.
  document.addEventListener("htmx:afterOnLoad", function (event) {
    if (event.detail.successful) {
      banner.hidden = true;
    }
  });
})();
