(function () {
  "use strict";

  var banner = document.getElementById("c-banner");

  // htmx's own trigger filters need eval, which the CSP forbids, so polling is paused here.
  document.addEventListener("htmx:beforeRequest", function (event) {
    var element = event.detail.elt;
    if (!element.hasAttribute("data-poll")) {
      return;
    }
    if (document.hidden || element.querySelector("details[open]")) {
      event.preventDefault();
    }
  });

  function showBanner() {
    banner.hidden = false;
  }

  document.addEventListener("htmx:responseError", showBanner);
  document.addEventListener("htmx:sendError", showBanner);
  // htmx fires afterOnLoad for a 4xx or 5xx too, right after responseError.
  document.addEventListener("htmx:afterOnLoad", function (event) {
    if (event.detail.successful) {
      banner.hidden = true;
    }
  });
})();
