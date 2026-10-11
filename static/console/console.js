(function () {
  "use strict";

  function hasSelectionIn(element) {
    var selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.rangeCount) {
      return false;
    }
    return selection.getRangeAt(0).intersectsNode(element);
  }

  // htmx's own trigger filters need eval, which the CSP forbids, so polling is paused here.
  // A swap drops focus and selection inside the element, so it waits while either is there.
  document.addEventListener("htmx:beforeRequest", function (event) {
    var element = event.detail.elt;
    if (!element.hasAttribute("data-poll")) {
      return;
    }
    if (
      document.hidden ||
      element.querySelector("details[open]") ||
      element.contains(document.activeElement) ||
      hasSelectionIn(element)
    ) {
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
