// Auto-formats any input with class "phone-input" as XXX-XXX-XXXX while typing.
(function () {
  function formatPhone(value) {
    const digits = value.replace(/\D/g, "").slice(0, 10);
    const len = digits.length;
    if (len < 4) return digits;
    if (len < 7) return digits.slice(0, 3) + "-" + digits.slice(3);
    return digits.slice(0, 3) + "-" + digits.slice(3, 6) + "-" + digits.slice(6);
  }

  function attach(input) {
    input.addEventListener("input", function () {
      const cursorAtEnd = input.selectionStart === input.value.length;
      input.value = formatPhone(input.value);
      if (cursorAtEnd) {
        input.selectionStart = input.selectionEnd = input.value.length;
      }
    });
    // Format any pre-filled value (e.g. editing an existing contact) on page load.
    if (input.value) {
      input.value = formatPhone(input.value);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("input.phone-input").forEach(attach);
  });
})();

// Auto-formats any input with class "state-input" as an uppercase 2-letter
// state abbreviation (e.g. "tx" -> "TX") while typing.
(function () {
  function formatState(value) {
    return value.replace(/[^a-zA-Z]/g, "").slice(0, 2).toUpperCase();
  }

  function attach(input) {
    input.addEventListener("input", function () {
      input.value = formatState(input.value);
    });
    if (input.value) {
      input.value = formatState(input.value);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("input.state-input").forEach(attach);
  });
})();

// Auto-formats any input with class "dob-input" as MM/DD/YYYY while typing,
// inserting the slashes automatically so people don't have to type them.
(function () {
  function formatDob(value) {
    const digits = value.replace(/\D/g, "").slice(0, 8);
    const len = digits.length;
    if (len < 3) return digits;
    if (len < 5) return digits.slice(0, 2) + "/" + digits.slice(2);
    return digits.slice(0, 2) + "/" + digits.slice(2, 4) + "/" + digits.slice(4);
  }

  function attach(input) {
    input.addEventListener("input", function () {
      const cursorAtEnd = input.selectionStart === input.value.length;
      input.value = formatDob(input.value);
      if (cursorAtEnd) {
        input.selectionStart = input.selectionEnd = input.value.length;
      }
    });
    // Format any pre-filled value (e.g. editing an existing contact) on page load.
    if (input.value) {
      input.value = formatDob(input.value);
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("input.dob-input").forEach(attach);
  });
})();

// Shows/hides a paired free-text box next to a <select class="self-describe-toggle">
// depending on whether "Prefer to self-describe" is chosen. Pairing is via
// data-describe-target="<id>" on the select pointing at the box's id. Used
// for Gender, Ethnicity, and anything else with a "Prefer to self-describe"
// option.
(function () {
  function sync(select) {
    const boxId = select.getAttribute("data-describe-target");
    const box = boxId && document.getElementById(boxId);
    if (!box) return;
    const show = select.value === "Prefer to self-describe";
    box.style.display = show ? "" : "none";
    const input = box.querySelector("input, textarea");
    if (input && !show) {
      input.value = "";
    }
  }

  function attach(select) {
    select.addEventListener("change", function () { sync(select); });
    sync(select);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select.self-describe-toggle").forEach(attach);
  });
})();

// Shows/hides a paired block of fields next to a <select class="yesno-toggle">
// depending on whether "Yes" is chosen. Pairing is via data-show-target="<id>"
// on the select pointing at the block's id. Unlike the gender box above, this
// never clears the fields inside when hiding them, so flipping the dropdown
// back and forth doesn't lose anything already typed in.
(function () {
  function sync(select) {
    const boxId = select.getAttribute("data-show-target");
    const box = boxId && document.getElementById(boxId);
    if (!box) return;
    box.style.display = select.value === "Yes" ? "" : "none";
  }

  function attach(select) {
    select.addEventListener("change", function () { sync(select); });
    sync(select);
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("select.yesno-toggle").forEach(attach);
  });
})();

// On the contact detail page (Leadership view) and the public long form
// alike, the D1/D2/D3/Refocus/Contract section should disappear as soon as
// Discovery attendance is set to "No" and every discovery date field is
// empty -- live, without needing to hit Save first. It stays visible if
// attendance is "Yes" OR any one of the discovery date fields still has
// something typed into it (a grandfathered contact who has dates on file
// but was marked "No"). No-ops on any page that doesn't have this section
// (i.e. non-Leadership staff, or contacts with no Discovery attendance
// field at all).
(function () {
  function sync() {
    const section = document.getElementById("contract_fields_section");
    if (!section) return;
    const select = document.querySelector('select[name="discovery_attendance"]');
    const dateBox = document.getElementById("discovery_dates_box");
    const hasDates = !!dateBox && Array.prototype.some.call(
      dateBox.querySelectorAll("input"),
      function (input) { return input.value.trim() !== ""; }
    );
    const isYes = !!select && select.value === "Yes";
    // The public long form shows this section ONLY for "Yes". (Leadership's contact page
    // also keeps it open for a "No" contact who still has dates on file.)
    const showsSection = isYes || (!section.hasAttribute("data-yes-only") && hasDates);
    section.style.display = showsSection ? "" : "none";

    // On the public long form, the Finish button lives above this section by
    // default, but moves to the bottom (after the contract fields) as soon as
    // the section itself becomes visible -- so someone who says Yes fills in
    // their contract before finishing, while someone who says No never sees
    // this section at all and Finish stays right where it's always been.
    const finishTop = document.getElementById("finish_block_top");
    if (finishTop) finishTop.style.display = showsSection ? "none" : "";
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (!document.getElementById("contract_fields_section")) return;
    const select = document.querySelector('select[name="discovery_attendance"]');
    if (select) select.addEventListener("change", sync);
    const dateBox = document.getElementById("discovery_dates_box");
    if (dateBox) {
      dateBox.querySelectorAll("input").forEach(function (input) {
        input.addEventListener("input", sync);
      });
    }
    sync();
  });
})();

// Contract fields (D1/D2/D3/D5/D6/etc. on the contact detail page) save
// themselves in the background as the staff member types -- no Save button,
// mirroring the autosave on the public long form. Saves 1.5s after typing
// stops, and immediately on blur (tabbing/clicking away).
(function () {
  function statusEl(form) {
    return form.querySelector(".contract-save-status");
  }

  function save(form) {
    const data = new FormData(form);
    data.set("autosave", "1");
    const status = statusEl(form);
    if (status) status.textContent = "Saving…";
    fetch(form.action, { method: "POST", body: data })
      .then(function (resp) {
        if (!status) return;
        if (resp.ok) {
          const now = new Date();
          const hh = String(now.getHours()).padStart(2, "0");
          const mm = String(now.getMinutes()).padStart(2, "0");
          status.textContent = "Saved at " + hh + ":" + mm;
        } else {
          status.textContent = "Couldn't save just now -- please check your connection.";
        }
      })
      .catch(function () {
        if (status) status.textContent = "Couldn't save just now -- please check your connection.";
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    document.querySelectorAll("form.contract-autosave-form").forEach(function (form) {
      let timer = null;
      const field = form.querySelector('[name="text"]');
      if (!field) return;
      field.addEventListener("input", function () {
        clearTimeout(timer);
        timer = setTimeout(function () { save(form); }, 1500);
      });
      field.addEventListener("blur", function () {
        clearTimeout(timer);
        save(form);
      });
    });
  });
})();
