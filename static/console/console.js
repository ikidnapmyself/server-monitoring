(function () {
  "use strict";

  // A swap puts every filter control back to what the server sent, so a poll waits while
  // the filter form holds a change not yet submitted, or a select that may be open.
  function editing(element) {
    return Array.prototype.some.call(element.querySelectorAll("form[role=search]"), function (form) {
      if (form.querySelector("select:focus")) {
        return true;
      }
      return Array.prototype.some.call(form.elements, function (control) {
        if (control.tagName === "SELECT") {
          // With no option marked selected, the first one is the default.
          var initial = control.options[0];
          Array.prototype.forEach.call(control.options, function (option) {
            if (option.defaultSelected) {
              initial = option;
            }
          });
          return Boolean(initial) && control.value !== initial.value;
        }
        if (control.tagName === "INPUT" && control.type !== "hidden") {
          return control.value !== control.defaultValue;
        }
        return false;
      });
    });
  }

  // htmx's own trigger filters need eval, which the CSP forbids, so polling is paused here.
  // Focus alone is never a reason to pause: htmx refocuses the element with the same id
  // after a swap.
  document.addEventListener("htmx:beforeRequest", function (event) {
    var element = event.detail.elt;
    if (!element.hasAttribute("data-poll")) {
      return;
    }
    // A poll that queued behind an action runs the moment the action's response lands,
    // and would replace its outcome before anyone read it. That one poll is skipped.
    var fresh = element.querySelector("[data-fresh]");
    if (fresh) {
      fresh.removeAttribute("data-fresh");
      event.preventDefault();
      return;
    }
    if (document.hidden || element.querySelector("details[open]") || editing(element)) {
      event.preventDefault();
    }
  });

  // The button that was clicked is gone after an action's swap, so focus would fall to
  // <body>. afterSwap fires before a queued poll can strip data-fresh.
  document.addEventListener("htmx:afterSwap", function (event) {
    var notice = (event.detail.target || document).querySelector("[data-fresh]");
    if (notice) {
      notice.focus();
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

  // navigator.clipboard exists only in a secure context. Without it the button does nothing.
  document.addEventListener("click", function (event) {
    var button = event.target.closest ? event.target.closest("[data-copy]") : null;
    if (!button || !navigator.clipboard || button.hasAttribute("data-copied")) {
      return;
    }
    navigator.clipboard.writeText(button.getAttribute("data-copy")).then(function () {
      var text = button.textContent;
      var label = button.getAttribute("aria-label");
      button.setAttribute("data-copied", "");
      button.textContent = "Copied";
      button.setAttribute("aria-label", "Copied");
      setTimeout(function () {
        button.removeAttribute("data-copied");
        button.textContent = text;
        button.setAttribute("aria-label", label);
      }, 2000);
    }, function () {});
  });

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

  // A plain form posts and navigates away, so hx-disabled-elt never sees it. A second
  // click would race the first submit, and SQLite cannot make the loser wait.
  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.hasAttribute || !form.hasAttribute("data-disable-on-submit")) {
      return;
    }
    // A disabled submitter drops out of the post, so its name and value ride a hidden copy.
    var submitter = event.submitter;
    if (submitter && submitter.name) {
      var kept = document.createElement("input");
      kept.type = "hidden";
      kept.name = submitter.name;
      kept.value = submitter.value;
      kept.setAttribute("data-submitter", "");
      form.appendChild(kept);
    }
    form.querySelectorAll("button").forEach(function (button) {
      button.disabled = true;
    });
  });

  // The back button restores a page from the cache with its buttons still disabled.
  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) {
      return;
    }
    document.querySelectorAll("[data-disable-on-submit] button").forEach(function (button) {
      button.disabled = false;
    });
    var kept = document.querySelectorAll("[data-disable-on-submit] [data-submitter]");
    kept.forEach(function (input) {
      input.remove();
    });
  });

  var banner = document.getElementById("c-banner");
  if (!banner) {
    return;
  }

  // The reload an action failure asks for must not clear the banner that explains it.
  var keepBanner = false;

  function reload(target) {
    var polled = target && target.closest ? target.closest("[data-poll]") : null;
    var elements = polled ? [polled] : document.querySelectorAll("[data-poll]");
    Array.prototype.forEach.call(elements, function (element) {
      var fresh = element.querySelector("[data-fresh]");
      if (fresh) {
        fresh.removeAttribute("data-fresh");
      }
      element.dispatchEvent(new CustomEvent("console:refresh"));
    });
  }

  // htmx swaps nothing on a 4xx, a 5xx, a network error or a timeout. For a poll the
  // page is only stale. For an action nobody knows whether the server applied it, so the
  // state is reloaded. A repeat is safe: the server refuses an action already applied.
  function onFailure(event) {
    var config = event.detail.requestConfig;
    var action = Boolean(config) && config.verb !== "get";
    banner.textContent = banner.getAttribute(action ? "data-action-text" : "data-refresh-text");
    banner.hidden = false;
    keepBanner = action;
    if (action) {
      reload(event.detail.target);
    }
  }

  document.addEventListener("htmx:responseError", onFailure);
  document.addEventListener("htmx:sendError", onFailure);
  document.addEventListener("htmx:timeout", onFailure);
  // htmx fires afterOnLoad for a 4xx or 5xx too, right after responseError.
  document.addEventListener("htmx:afterOnLoad", function (event) {
    if (!event.detail.successful) {
      return;
    }
    if (keepBanner) {
      keepBanner = false;
      return;
    }
    banner.hidden = true;
  });
})();
